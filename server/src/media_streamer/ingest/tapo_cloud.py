"""EXPERIMENTAL: stream a Tapo camera through the TP-Link cloud relay.

Uses ``ontapo`` (unofficial, reverse engineered from the Tapo app; may break at any time
and may conflict with TP-Link's terms). ``Device.live_preview`` writes MPEG-TS to a
file path; we hand it a named pipe that ffmpeg reads and republishes to MediaMTX::

    ontapo live_preview --> FIFO --> ffmpeg -c:v copy --> rtsp://mediamtx/<camera>

Each relay session lasts ``session_seconds`` and is then restarted (a short gap and a new
recording segment are expected at each restart).

Log in once interactively (handles MFA) to create the session file::

    uv run media-streamer tapo-login
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from ..config import CameraConfig, Settings
from .supervisor import publish_url

log = logging.getLogger(__name__)


def _import_ontapo():
    try:
        import ontapo
    except ImportError as e:
        raise RuntimeError(
            "tapo_cloud sources need the optional dependency: "
            "uv sync --package media-streamer --extra tapo-cloud"
        ) from e
    return ontapo


def load_session(settings: Settings):
    ontapo = _import_ontapo()
    path = settings.tapo_session_file
    if not path.exists():
        raise RuntimeError(f"no Tapo session at {path}; run `media-streamer tapo-login` first")
    return ontapo.OnTapo.from_dict(json.loads(path.read_text()))


def save_session(settings: Settings, session) -> None:
    path = settings.tapo_session_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session.to_dict()))
    os.chmod(tmp, 0o600)  # the session grants full access to the Tapo account
    tmp.replace(path)


async def _find_device(session, cam: CameraConfig):
    src = cam.source
    devices = await session.devices()
    for dev in devices:
        if (src.device_id and dev.device_id == src.device_id) or \
                (src.device_name and dev.name == src.device_name):
            return dev
    names = ", ".join(f"{d.name!r} ({d.device_id})" for d in devices)
    raise RuntimeError(f"[{cam.id}] Tapo device not found. Available: {names}")


async def _stream_once(settings: Settings, cam: CameraConfig, stop: threading.Event) -> None:
    session = load_session(settings)
    try:
        dev = await _find_device(session, cam)
        with tempfile.TemporaryDirectory(prefix=f"tapo-{cam.id}-") as tmp:
            fifo = Path(tmp) / "live.ts"
            os.mkfifo(fifo)
            # Video is copied as-is; audio (G.711 on most Tapo models) is converted to AAC
            # so it can be stored in fMP4.
            proc = subprocess.Popen(  # noqa: ASYNC220 - own event loop per camera thread
                ["ffmpeg", "-v", "error", "-fflags", "+genpts", "-f", "mpegts", "-i", str(fifo),
                 "-map", "0:v", "-map", "0:a?", "-c:v", "copy", "-c:a", "aac",
                 "-f", "rtsp", "-rtsp_transport", "tcp", publish_url(settings, cam)],
                stderr=subprocess.PIPE, text=True,
            )
            try:
                log.info("[%s] cloud relay session started (%s)", cam.id, cam.source.resolution)
                # live_preview does blocking file writes into the FIFO; this coroutine runs
                # in its own thread/event loop per camera, so that is acceptable.
                written = await dev.live_preview(str(fifo), seconds=cam.source.session_seconds,
                                                 resolution=cam.source.resolution)
                log.info("[%s] relay session ended after %.1f MB", cam.id, written / 1e6)
            finally:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                if proc.returncode not in (0, None):
                    err = (proc.stderr.read() if proc.stderr else "").strip()
                    log.warning("[%s] ffmpeg exited %s: %s", cam.id, proc.returncode, err[-300:])
    finally:
        save_session(settings, session)  # persist refreshed tokens
        await session.aclose()


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


def run_tapo_cloud_camera(settings: Settings, cam: CameraConfig, stop: threading.Event) -> None:
    backoff = 5.0
    while not stop.is_set():
        try:
            asyncio.run(_stream_until_stopped(settings, cam, stop))
            backoff = 5.0
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


def check(settings: Settings, camera: str | None, seconds: float, resolution: str) -> int:
    """Smoke-test the cloud relay without printing any secrets.

    Lists the cameras on the account, records ``seconds`` of live video from one of them
    to a temporary file and reports codec / resolution / bitrate. Returns an exit code.
    """
    from .. import ffmpeg

    async def main() -> int:
        session = load_session(settings)
        try:
            devices = await session.devices()
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
                written = await dev.live_preview(str(out), seconds=seconds, resolution=resolution)
                info = ffmpeg.probe(out)
                kbps = written * 8 / 1000 / max(info.duration_s or seconds, 0.1)
                print(f"OK: {written / 1e6:.1f} MB, {info.codec} {info.width}x{info.height}, "
                      f"{info.duration_s:.1f} s, ~{kbps:.0f} kbit/s")
                if info.codec == "hevc":
                    print("Note: H.265 — browsers may not play it; set H.264 in the Tapo app "
                          "if possible.")
            return 0
        finally:
            save_session(settings, session)
            await session.aclose()

    return asyncio.run(main())
