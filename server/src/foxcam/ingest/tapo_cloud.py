"""EXPERIMENTAL: stream a Tapo camera through the TP-Link cloud relay.

Uses ``ontapo`` (unofficial, reverse engineered from the Tapo app; may break at any time
and may conflict with TP-Link's terms). ``Device.live_preview`` writes MPEG-TS to a
file path; we hand it a named pipe that ffmpeg reads and republishes to MediaMTX::

    ontapo live_preview --> FIFO --> ffmpeg -c:v copy --> rtsp://mediamtx/<camera>

The relay closes a live-view session after ~2 minutes without client heartbeats; we send
them like the Tapo app (``tapo_relay.py``) and, when a session ends anyway, feed the next
one into the same ffmpeg publisher, so the stream stays up.

Log in once interactively (handles MFA) to create the session file::

    uv run foxcam tapo-login

Token refresh: the stored access token expires; on a 401 we exchange the refresh token for
a new one (see :func:`call_with_refresh`). The camera threads and the web server all share
one session file, so refreshing happens under a file lock and the file is only written when
the tokens actually change. If the refresh itself is rejected the session is marked as
expired and someone has to sign in again.
"""

from __future__ import annotations

import asyncio
import fcntl
import itertools
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

from ..config import CameraConfig, Settings
from .supervisor import publish_url

log = logging.getLogger(__name__)

T = TypeVar("T")


class TapoSessionExpired(RuntimeError):
    """The refresh token was rejected; a new interactive sign-in is needed."""


def _import_ontapo():
    try:
        import ontapo
    except ImportError as e:
        raise RuntimeError(
            "tapo_cloud sources need the optional dependency: "
            "uv sync --package foxcam --extra tapo-cloud"
        ) from e
    return ontapo


def load_session(settings: Settings):
    ontapo = _import_ontapo()
    path = settings.tapo_session_file
    if not path.exists():
        raise RuntimeError(f"no Tapo session at {path}; run `foxcam tapo-login` first")
    return ontapo.OnTapo.from_dict(json.loads(path.read_text()))


def save_session(settings: Settings, session) -> None:
    path = settings.tapo_session_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session.to_dict()))
    os.chmod(tmp, 0o600)  # the session grants full access to the Tapo account
    tmp.replace(path)
    expired_marker(settings).unlink(missing_ok=True)


def expired_marker(settings: Settings) -> Path:
    return settings.tapo_session_file.with_suffix(".expired")


def session_expired(settings: Settings) -> bool:
    return expired_marker(settings).exists()


@asynccontextmanager
async def _session_lock(settings: Settings):
    """Exclusive lock shared by all threads and processes that touch the session file."""
    path = settings.tapo_session_file.with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # also releases the lock


async def refresh_session(settings: Settings, session) -> None:
    """Get a working token into ``session``, refreshing it at most once across processes."""
    ontapo = _import_ontapo()
    stale = session.token
    async with _session_lock(settings):
        path = settings.tapo_session_file
        if not path.exists():
            raise TapoSessionExpired("signed out of Tapo")
        if session_expired(settings):
            raise TapoSessionExpired("Tapo sign-in expired, please sign in again")
        stored = json.loads(path.read_text())
        if stored.get("token") and stored["token"] != stale:
            # Someone else refreshed while we were using the old token: adopt theirs.
            session.token = stored["token"]
            session.refresh_token = stored.get("refreshToken", "")
            return
        try:
            await session.refresh()
        except ontapo.Unauthorized as e:
            expired_marker(settings).touch()
            log.warning("Tapo refresh token rejected; sign in again (%s)", e)
            raise TapoSessionExpired("Tapo sign-in expired, please sign in again") from e
        save_session(settings, session)
        log.info("Tapo session token refreshed")


async def call_with_refresh(settings: Settings, session, fn: Callable[[], Awaitable[T]]) -> T:
    """Run ``fn``; on an expired token refresh once and run it again."""
    ontapo = _import_ontapo()
    try:
        return await fn()
    except ontapo.Unauthorized:
        await refresh_session(settings, session)
        return await fn()


async def _find_device(settings: Settings, session, cam: CameraConfig):
    src = cam.source
    devices = await call_with_refresh(settings, session, session.devices)
    for dev in devices:
        if (src.device_id and dev.device_id == src.device_id) or \
                (src.device_name and dev.name == src.device_name):
            return dev
    names = ", ".join(f"{d.name!r} ({d.device_id})" for d in devices)
    raise RuntimeError(f"[{cam.id}] Tapo device not found. Available: {names}")


async def _stream_once(settings: Settings, cam: CameraConfig, stop: threading.Event) -> None:
    """One long-lived publisher fed by successive relay sessions.

    TP-Link ends cloud live view after a few minutes. Instead of tearing everything down
    each time (a 10-20 s outage and a new MediaMTX publisher), ffmpeg keeps reading from a
    FIFO that we also hold open for writing, so it never sees end-of-file between relay
    sessions; timestamps come from the arrival time, so the timeline stays continuous across
    sessions. Viewers and the recorder see at most a short freeze. Relay sessions also send
    the app's heartbeats (see tapo_relay.py), so reconnects should be rare.
    """
    session = load_session(settings)
    try:
        dev = await _find_device(settings, session, cam)
        with tempfile.TemporaryDirectory(prefix=f"tapo-{cam.id}-") as tmp:
            fifo = Path(tmp) / "live.ts"
            os.mkfifo(fifo)
            # Video is copied as-is; audio (G.711 on most Tapo models) is converted to AAC
            # so it can be stored in fMP4.
            proc = subprocess.Popen(  # noqa: ASYNC220 - own event loop per camera thread
                ["ffmpeg", "-v", "error", "-use_wallclock_as_timestamps", "1",
                 "-f", "mpegts", "-i", str(fifo),
                 "-map", "0:v", "-map", "0:a?", "-c:v", "copy", "-c:a", "aac",
                 "-f", "rtsp", "-rtsp_transport", "tcp", publish_url(settings, cam)],
                stderr=subprocess.PIPE, text=True,
            )
            # Opening the write end waits for ffmpeg to open the read end. Holding it for
            # the publisher's lifetime keeps EOF away while relay sessions come and go.
            keeper = os.open(fifo, os.O_WRONLY)
            try:
                await _feed_relay_sessions(settings, session, dev, cam, keeper, proc, stop)
            finally:
                os.close(keeper)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                if proc.returncode not in (0, None, -15):
                    err = (proc.stderr.read() if proc.stderr else "").strip()
                    log.warning("[%s] ffmpeg exited %s: %s", cam.id, proc.returncode, err[-300:])
    finally:
        await session.aclose()


async def _feed_relay_sessions(settings: Settings, session, dev, cam: CameraConfig,
                               fd: int, proc: subprocess.Popen, stop: threading.Event) -> None:
    """Run relay sessions back to back into ``fd`` until stopped, ffmpeg dies or the relay
    keeps failing (then the caller tears the publisher down and retries with backoff)."""
    from . import tapo_relay

    failures = 0
    keepalive = tapo_relay.supported()
    if not keepalive:
        log.warning("[%s] ontapo internals changed: no heartbeats, reconnecting instead", cam.id)
    while not stop.is_set() and proc.poll() is None:
        try:
            if keepalive:
                result = await call_with_refresh(settings, session, lambda: tapo_relay.relay_preview(
                    session, dev, fd, resolution=cam.source.resolution,
                    heartbeat=cam.source.heartbeat_seconds,
                    max_seconds=cam.source.session_seconds, stop=stop))
                log.info("[%s] relay session ended after %.0f s, %.1f MB, %d heartbeat(s): %s",
                         cam.id, result.seconds, result.bytes / 1e6, result.heartbeats,
                         result.reason)
            else:
                written = await call_with_refresh(settings, session, lambda: dev.live_preview(
                    f"/dev/fd/{fd}", seconds=cam.source.session_seconds or 24 * 3600,
                    resolution=cam.source.resolution))
                log.info("[%s] relay session ended after %.1f MB", cam.id, written / 1e6)
            failures = 0
        except TapoSessionExpired:
            raise
        except Exception as e:
            failures += 1
            if failures >= 3:
                raise
            log.warning("[%s] relay session failed (%s); reconnecting", cam.id, e)
            await asyncio.sleep(2 * failures)


async def _stream_until_stopped(settings: Settings, cam: CameraConfig,
                                stop: threading.Event) -> None:
    """One relay session, cancelled early when the camera is removed or changed."""
    task = asyncio.create_task(_stream_once(settings, cam, stop))
    while not task.done():
        if stop.is_set():
            task.cancel()
        await asyncio.sleep(1)
    if not task.cancelled():
        task.result()  # re-raise errors for the retry loop


def _needs_sign_in(settings: Settings) -> bool:
    return not settings.tapo_session_file.exists() or session_expired(settings)


def run_tapo_cloud_camera(settings: Settings, cam: CameraConfig, stop: threading.Event) -> None:
    backoff = 5.0
    while not stop.is_set():
        if _needs_sign_in(settings):
            log.warning("[%s] waiting for a Tapo sign-in (Cameras - Integrations)", cam.id)
            while _needs_sign_in(settings) and not stop.wait(10):
                pass
            backoff = 5.0
            continue
        try:
            asyncio.run(_stream_until_stopped(settings, cam, stop))
            backoff = 5.0
        except TapoSessionExpired:
            continue  # the loop above waits for the new sign-in
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] cloud stream failed: %s (retry in %.0fs)", cam.id, e, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 300)


def interactive_login(settings: Settings) -> None:
    import getpass

    ontapo = _import_ontapo()

    async def main() -> None:
        email = input("Tapo account email: ").strip()
        password = getpass.getpass("Tapo account password: ")
        session = await ontapo.OnTapo.login(
            email, password, mfa=lambda: input("MFA code (check email/app): ").strip())
        try:
            devices = await session.devices()
            print("Cameras on this account:")
            for d in devices:
                print(f"  - name={d.name!r} device_id={d.device_id}")
            save_session(settings, session)
            print(f"Session saved to {settings.tapo_session_file}")
        finally:
            await session.aclose()

    asyncio.run(main())


def frame_timing(path: Path) -> str:
    """How evenly the camera's own timestamps in an MPEG-TS sample are spaced: the median
    gap between frames, and how many gaps are under 10 ms (frames bunched together)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    times = sorted(float(v) for v in (line.split(",")[0] for line in out.split())
                   if v not in ("", "N/A"))
    gaps = [b - a for a, b in itertools.pairwise(times)]
    if not gaps:
        return "no frame timestamps"
    median = sorted(gaps)[len(gaps) // 2]
    bunched = sum(g < 0.010 for g in gaps) / len(gaps)
    return (f"{len(times)} frames over {times[-1] - times[0]:.1f} s, median gap "
            f"{median * 1000:.0f} ms, {bunched:.0%} of gaps under 10 ms")


def check(settings: Settings, camera: str | None, seconds: float, resolution: str,
          keep: str | None = None) -> int:
    """Smoke-test the cloud relay without printing any secrets.

    Lists the cameras on the account, records ``seconds`` of live video from one of them
    to a temporary file and reports codec / resolution / bitrate, and how evenly the
    camera's own frame timestamps are spaced (``keep``: save the sample). Returns an exit
    code.
    """
    from .. import ffmpeg

    async def main() -> int:
        session = load_session(settings)
        try:
            devices = await call_with_refresh(settings, session, session.devices)
            print(f"Signed in as {session.email or '?'}; {len(devices)} camera(s):")
            for d in devices:
                print(f"  - {d.name!r}  device_id={d.device_id}")
            if not devices:
                return 1
            dev = next((d for d in devices if camera in (d.name, d.device_id)), None) \
                if camera else devices[0]
            if dev is None:
                print(f"No camera named {camera!r} on this account.")
                return 1
            print(f"Recording {seconds:.0f} s of {resolution} live view from {dev.name!r} "
                  "through the cloud relay…")
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "check.ts"
                written = await call_with_refresh(settings, session, lambda: dev.live_preview(
                    str(out), seconds=seconds, resolution=resolution))
                info = ffmpeg.probe(out)
                kbps = written * 8 / 1000 / max(info.duration_s or seconds, 0.1)
                print(f"OK: {written / 1e6:.1f} MB, {info.codec} {info.width}x{info.height}, "
                      f"{info.duration_s:.1f} s, ~{kbps:.0f} kbit/s")
                print(f"Camera timestamps: {frame_timing(out)}")
                if keep:
                    shutil.copyfile(out, keep)
                    print(f"Sample saved to {keep}")
                if info.codec == "hevc":
                    print("Note: H.265 — browsers may not play it; set H.264 in the Tapo app "
                          "if possible.")
            return 0
        finally:
            await session.aclose()

    return asyncio.run(main())
