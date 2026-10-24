"""Compaction: join each camera's finished segments into one file per period (hourly).

MediaMTX records short segments so detection can start right away. Once a period is over
and its segments are preprocessed, `media-streamer compact` (its own process) joins them:

- **stream copy** for cameras on the `original` recording profile: no quality loss, the
  same bytes in far fewer files;
- **re-encode** for cameras whose recording profile transcodes (e.g. 1080p H.265), on the
  best hardware encoder available (see hwaccel). Detection and live view still used the
  camera's original stream; only the stored copy shrinks.

Segment rows stay: they point into the archive (`archive_id`, `archive_offset_s`), so
detections, events and feedback keep working. Every segment starts on a keyframe in the
archive (MediaMTX cuts on keyframes; re-encodes force one at each boundary and are
checked), so a segment can be cut back out without re-encoding.

The original files are deleted a little later (`REPLACE_DELAY`), so a clip being cut from
them right now still finds them. Encoding is behind ``Encoder`` so it can move to another
machine later; the default runs ffmpeg here.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from .. import ffmpeg, hwaccel, sprites
from ..config import RecordingProfileConfig, Settings
from ..db import session_scope
from ..models import Archive, Segment, SegmentStatus, utcnow

log = logging.getLogger(__name__)

ARCHIVE_DIR = "archive"
REPLACE_DELAY = timedelta(minutes=2)  # keep originals this long after they were joined
CACHE_SECONDS = 3600  # segments cut out of archives for playback / download
KEYFRAME_TOLERANCE = 0.1  # seconds
STATE_FILE = "compaction-state.json"  # in data_dir; the server shows it on the Status page
HEARTBEAT_SECONDS = 30


# --- status (written by the compact process, read by the server) --------------------------

_state_lock = threading.Lock()


def _state_path(settings: Settings) -> Path:
    return settings.data_dir / STATE_FILE


def read_state(settings: Settings) -> dict:
    try:
        return json.loads(_state_path(settings).read_text())
    except (OSError, ValueError):
        return {}


def _write_state(settings: Settings, **changes: object) -> None:
    with _state_lock:
        state = {**read_state(settings), **changes}
        path = _state_path(settings)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(state))
            tmp.replace(path)
        except OSError as e:
            log.debug("cannot write %s: %s", path, e)


def _now_iso() -> str:
    return utcnow().isoformat(timespec="seconds") + "Z"


def status(settings: Settings) -> dict:
    """For Jobs → Archiving and the Status page: is the process alive, what is it doing,
    what is waiting (oldest periods first)."""
    cfg = settings.compaction
    state = read_state(settings)
    beat = state.get("heartbeat_at")
    age = (utcnow() - datetime.fromisoformat(beat.rstrip("Z"))).total_seconds() if beat else None
    finished = _period_start(utcnow() - timedelta(minutes=cfg.delay_minutes),
                             cfg.period_minutes)
    with session_scope() as s:
        archives = s.scalar(select(func.count()).select_from(Archive)
                            .where(Archive.deleted_at.is_(None)))
        joined = s.scalar(select(func.count()).select_from(Segment).where(
            Segment.archive_id.is_not(None), Segment.deleted_at.is_(None)))
        rows = s.execute(select(Segment.camera_id, Segment.start_at).where(
            Segment.archive_id.is_(None), Segment.deleted_at.is_(None),
            Segment.duration_s > 0, Segment.start_at < finished)).all()
    per_period: dict[tuple[str, datetime], int] = {}
    for camera_id, start in rows:
        key = (camera_id, _period_start(start, cfg.period_minutes))
        per_period[key] = per_period.get(key, 0) + 1
    # Periods with a single (or incompatible) segment are never joined: not "waiting".
    waiting = {k: n for k, n in per_period.items() if n >= 2}
    return {
        "enabled": cfg.enabled, "period_minutes": cfg.period_minutes,
        "running": age is not None and age < 3 * HEARTBEAT_SECONDS,
        "heartbeat_at": beat, "host": state.get("host"),
        "current": state.get("current"), "last_built": state.get("last_built"),
        "last_error": state.get("last_error"), "last_error_at": state.get("last_error_at"),
        "archives": archives or 0, "joined_segments": joined or 0,
        "waiting_periods": len(waiting), "waiting_segments": sum(waiting.values()),
        "waiting_list": [
            {"camera_id": cam, "start_at": start.isoformat(timespec="seconds") + "Z",
             "segments": n}
            for (cam, start), n in sorted(waiting.items(), key=lambda kv: kv[0][1])[:50]],
    }


# --- where a segment's footage is -----------------------------------------------------------

@dataclass
class Source:
    """A segment's footage: ``path`` from ``offset_s`` for ``duration_s`` seconds."""

    path: Path
    offset_s: float
    duration_s: float
    archived: bool


def segment_source(settings: Settings, seg: Segment) -> Source | None:
    """Call inside a session. None when the footage is gone (retention)."""
    if seg.archive_id is not None:
        arc = seg.archive
        if arc is None or arc.deleted_at is not None:
            return None
        return Source(settings.recordings_dir / arc.path, seg.archive_offset_s or 0.0,
                      seg.duration_s, True)
    if seg.deleted_at is not None:
        return None
    return Source(settings.recordings_dir / seg.path, 0.0, seg.duration_s, False)


def source_for(settings: Settings, segment_id: int) -> Source | None:
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        return segment_source(settings, seg) if seg else None


def cache_dir(settings: Settings) -> Path:
    return settings.data_dir / "cache" / "segments"


def playable_file(settings: Settings, segment_id: int) -> Path | None:
    """A file with exactly this segment: its own, or cut out of its archive (cached)."""
    src = source_for(settings, segment_id)
    if src is None or not src.path.exists():
        return None
    if not src.archived:
        return src.path
    dst = cache_dir(settings) / f"{segment_id}.mp4"
    if not dst.exists():
        ffmpeg.cut(src.path, dst, src.offset_s, src.duration_s)
    return dst


def archive_remote(segment_id: int) -> str | None:
    """S3 key of the segment's archive (served whole when the local file is gone)."""
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        return seg.archive.remote_key if seg is not None and seg.archive is not None else None


# --- encoding ------------------------------------------------------------------------------

@dataclass
class Plan:
    """One archive to build from consecutive segments."""

    camera_id: str
    segment_ids: list[int]
    files: list[Path]
    durations: list[float]
    profile: RecordingProfileConfig

    @property
    def offsets(self) -> list[float]:
        out, t = [], 0.0
        for d in self.durations:
            out.append(t)
            t += d
        return out


# Builds the archive at ``dst`` and returns a short description ("copy", "hevc_nvenc"...).
Encoder = Callable[[Settings, Plan, Path], str]


def _concat_list(plan: Plan, dst: Path) -> Path:
    """concat demuxer list with explicit durations, so segment i starts at offsets[i]."""
    lst = dst.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\nduration {d:.6f}\n"
                           for p, d in zip(plan.files, plan.durations, strict=True)))
    return lst


def _copy(plan: Plan, lst: Path, dst: Path) -> None:
    ffmpeg.run_long(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                     "-i", str(lst), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                     "-movflags", "+faststart", str(dst)])


def _reencode(settings: Settings, plan: Plan, lst: Path, dst: Path) -> str:
    p, m = plan.profile, settings.mediamtx
    enc = hwaccel.Encode(codec=p.codec, max_height=p.max_height, fps=p.fps,
                         bitrate_kbit=p.bitrate_kbit, audio="copy", live=False)
    backend = hwaccel.select(m.hwaccel, enc.codec, device=m.hw_device)
    keys = ",".join(f"{t:.3f}" for t in plan.offsets[1:]) or "0"
    args = hwaccel.ffmpeg_args(
        backend, enc, str(lst), str(dst), device=m.hw_device,
        input_args=["-f", "concat", "-safe", "0"],
        output_args=["-force_key_frames", keys,
                     *(["-forced-idr", "1"] if backend == "nvenc" else []),
                     "-movflags", "+faststart", "-y"])
    ffmpeg.run_long(args)
    return hwaccel.encoder_name(backend, enc.codec)


def local_encoder(settings: Settings, plan: Plan, dst: Path) -> str:
    """Join (and re-encode, per the camera's recording profile) on this machine. A
    re-encode that fails, or does not put a keyframe at every segment start, falls back to
    joining without re-encoding."""
    lst = _concat_list(plan, dst)
    try:
        if plan.profile.transcode:
            try:
                how = _reencode(settings, plan, lst, dst)
                missing = missing_keyframes(dst, plan.offsets)
                if not missing:
                    return how
                log.warning("[%s] %s left %d segment start(s) without a keyframe; joining "
                            "without re-encoding instead", plan.camera_id, how, len(missing))
            except ffmpeg.FfmpegError as e:
                log.warning("[%s] re-encoding failed (%s); joining without re-encoding",
                            plan.camera_id, e)
        _copy(plan, lst, dst)
        return "copy"
    finally:
        lst.unlink(missing_ok=True)


def missing_keyframes(path: Path, offsets: Sequence[float]) -> list[float]:
    """Segment starts with no keyframe within KEYFRAME_TOLERANCE."""
    keys = ffmpeg.keyframe_times(path)
    out = []
    for t in offsets:
        if not any(abs(k - t) <= KEYFRAME_TOLERANCE for k in keys):
            out.append(t)
    return out


# --- planning ------------------------------------------------------------------------------

def _period_start(t: datetime, minutes: int) -> datetime:
    epoch = datetime(2000, 1, 1)
    step = timedelta(minutes=minutes)
    return epoch + ((t - epoch) // step) * step


def _signature(path: Path) -> tuple | None:
    """Streams that must match for files to be joined without re-encoding."""
    try:
        return ffmpeg.stream_signature(path)
    except ffmpeg.FfmpegError as e:
        log.warning("cannot read %s: %s", path, e)
        return None


def plans(settings: Settings, now: datetime | None = None) -> list[Plan]:
    """Archives to build: per camera and finished period, runs of compatible segments."""
    from ..recording import camera_recording_profile

    cfg = settings.compaction
    now = now or utcnow()
    cutoff = now - timedelta(minutes=cfg.delay_minutes)
    period = timedelta(minutes=cfg.period_minutes)
    with session_scope() as s:
        rows = s.execute(select(Segment.id, Segment.camera_id, Segment.path, Segment.start_at,
                                Segment.duration_s, Segment.status).where(
            Segment.archive_id.is_(None), Segment.deleted_at.is_(None),
            Segment.duration_s > 0, Segment.start_at < cutoff,
        ).order_by(Segment.camera_id, Segment.start_at)).all()
    groups: dict[tuple[str, datetime], list] = {}
    for r in rows:
        groups.setdefault((r.camera_id, _period_start(r.start_at, cfg.period_minutes)),
                          []).append(r)

    out: list[Plan] = []
    for (camera_id, start), segs in groups.items():
        if start + period > cutoff:
            continue  # period not over yet
        if any(r.status == SegmentStatus.NEW for r in segs):
            continue  # still being preprocessed (needs the original file)
        cam = settings.camera(camera_id)
        profile = (camera_recording_profile(settings, cam)[0] if cam
                   else RecordingProfileConfig(id="original"))
        run: list = []
        run_sig = None
        for r in segs:
            path = settings.recordings_dir / r.path
            sig = _signature(path) if path.exists() else None
            if sig is None or sig != run_sig:
                out += _plan(camera_id, run, profile, settings)
                run, run_sig = ([r], sig) if sig is not None else ([], None)
            else:
                run.append(r)
        out += _plan(camera_id, run, profile, settings)
    return out


def _plan(camera_id: str, run: list, profile: RecordingProfileConfig,
          settings: Settings) -> list[Plan]:
    # A single segment is joined too when it re-encodes (that is where the saving is).
    if len(run) < (1 if profile.transcode else 2):
        return []
    return [Plan(camera_id, [r.id for r in run],
                 [settings.recordings_dir / r.path for r in run],
                 [r.duration_s for r in run], profile)]


# --- building ------------------------------------------------------------------------------

def build(settings: Settings, plan: Plan, encoder: Encoder = local_encoder) -> int | None:
    """Build one archive and point its segments at it. Returns the archive id."""
    with session_scope() as s:
        first = s.get(Segment, plan.segment_ids[0])
        start = first.start_at
    rel = Path(plan.camera_id) / ARCHIVE_DIR / f"{start:%Y-%m-%d_%H-%M-%S}Z.mp4"
    dst = settings.recordings_dir / rel
    if dst.exists():
        rel = rel.with_name(f"{rel.stem}-{plan.segment_ids[0]}.mp4")
        dst = settings.recordings_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.stem + ".part.mp4")
    started = time.monotonic()
    _write_state(settings, current={
        "camera_id": plan.camera_id, "start_at": start.isoformat(timespec="seconds") + "Z",
        "segments": len(plan.segment_ids), "reencode": plan.profile.transcode,
        "profile": plan.profile.id, "started_at": _now_iso()})
    try:
        how = encoder(settings, plan, part)
        info = ffmpeg.probe(part)
        expected = sum(plan.durations)
        if abs(info.duration_s - expected) > max(2.0, expected * 0.01):
            raise ffmpeg.FfmpegError(
                f"archive is {info.duration_s:.1f} s, expected {expected:.1f} s")
    except Exception as e:  # noqa: BLE001 - keep the segments as they are
        part.unlink(missing_ok=True)
        log.error("[%s] could not join %d segment(s) from %s: %s", plan.camera_id,
                  len(plan.segment_ids), start, e)
        _write_state(settings, current=None, last_error_at=_now_iso(),
                     last_error=f"{plan.camera_id} {start:%Y-%m-%d %H:%M}: {e}"[:500])
        return None
    part.rename(dst)
    size = dst.stat().st_size
    with session_scope() as s:
        arc = Archive(camera_id=plan.camera_id, path=str(rel), start_at=start,
                      duration_s=info.duration_s, size_bytes=size,
                      profile_id=plan.profile.id if how != "copy" else "original",
                      encoder=how)
        s.add(arc)
        s.flush()
        total = sum(plan.durations) or 1
        for seg_id, offset, dur in zip(plan.segment_ids, plan.offsets, plan.durations,
                                       strict=True):
            seg = s.get(Segment, seg_id)
            if seg is None:
                continue
            seg.archive_id, seg.archive_offset_s = arc.id, offset
            # Its share of the archive, so the disk cap counts what is really used.
            seg.size_bytes = int(size * dur / total)
        arc_id = arc.id
    before = sum(p.stat().st_size for p in plan.files if p.exists())
    took = time.monotonic() - started
    log.info("[%s] joined %d segment(s) into %s (%s, %.0f MB -> %.0f MB, %.0f s)",
             plan.camera_id, len(plan.segment_ids), rel, how, before / 1e6, size / 1e6, took)
    _write_state(settings, current=None, last_built={
        "camera_id": plan.camera_id, "start_at": start.isoformat(timespec="seconds") + "Z",
        "segments": len(plan.segment_ids), "encoder": how, "bytes_before": before,
        "bytes_after": size, "seconds": round(took, 1), "at": _now_iso()})
    try:  # the player's timeline thumbnails, while the file is fresh in the page cache
        sprites.archive_sprite(settings, arc_id)
    except Exception as e:  # noqa: BLE001 - made later on demand instead
        log.warning("archive %s: no thumbnail sprite: %s", arc_id, e)
    return arc_id


def delete_replaced(settings: Settings, now: datetime | None = None) -> int:
    """Remove original segment files a while after they were joined into an archive."""
    now = now or utcnow()
    with session_scope() as s:
        rels = list(s.scalars(select(Segment.path).join(Archive, Segment.archive_id == Archive.id)
                              .where(Archive.created_at < now - REPLACE_DELAY,
                                     Archive.created_at > now - timedelta(days=7))))
    removed = 0
    for rel in rels:
        path = settings.recordings_dir / rel
        if path.exists():
            path.unlink()
            removed += 1
    return removed


def clean_cache(settings: Settings) -> None:
    """Segments cut out for playback after an hour; segment thumbnail sprites (cheap to
    make again) after a week."""
    for folder, age in ((cache_dir(settings), CACHE_SECONDS),
                        (sprites.segment_files(settings, 0)[0].parent, 7 * 86400)):
        if not folder.is_dir():
            continue
        cutoff = time.time() - age
        for f in folder.iterdir():
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)


def compact_once(settings: Settings, encoder: Encoder = local_encoder) -> int:
    delete_replaced(settings)
    clean_cache(settings)
    if not settings.compaction.enabled:
        return 0
    built = 0
    for plan in plans(settings):
        if build(settings, plan, encoder) is not None:
            built += 1
    if built == 0:  # quiet: thumbnail sprites for archives made before there were any
        sprites.backfill_archives(settings)
    return built


def run_compaction(settings: Settings, interval: float = 60.0) -> None:
    """`media-streamer compact`: build archives as periods finish, until interrupted."""
    from ..db import init_db

    init_db(settings)
    for part in settings.recordings_dir.glob(f"*/{ARCHIVE_DIR}/*.part.mp4"):
        part.unlink(missing_ok=True)  # left by an interrupted run
    _write_state(settings, current=None, host=socket.gethostname(), pid=os.getpid())

    def heartbeat() -> None:  # also while an hour-long encode runs
        while True:
            _write_state(settings, heartbeat_at=_now_iso())
            time.sleep(HEARTBEAT_SECONDS)

    threading.Thread(target=heartbeat, name="compaction-heartbeat", daemon=True).start()
    log.info("compaction: every %d min, %d min after the period ends (%s)",
             settings.compaction.period_minutes, settings.compaction.delay_minutes,
             "enabled" if settings.compaction.enabled else "disabled in config")
    while True:
        try:
            compact_once(settings)
        except Exception as e:
            log.exception("compaction failed")
            _write_state(settings, current=None, last_error_at=_now_iso(),
                         last_error=str(e)[:500])
        time.sleep(interval)
