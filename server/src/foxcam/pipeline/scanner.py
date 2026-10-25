"""Register finished MediaMTX recording segments.

Scanning the filesystem (instead of relying only on MediaMTX hooks) means segments
written while the server was down are picked up too.

A segment counts as finished when a newer file exists for the same camera, or its
mtime is older than the segment length plus a grace period.

When a publisher starts, MediaMTX's recorder often "detects drift between recording
duration and absolute time" within the first second and starts over in a new file. The
few seconds left in the first file are dropped here instead of becoming a recording (and a
detection job) of their own: a file shorter than ``FRAGMENT_SECONDS`` (and than half the
camera's segment length) whose camera's next file starts right after it.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from sqlalchemy import select

from .. import ffmpeg
from ..config import Settings
from ..db import session_scope
from ..models import Segment, SegmentStatus

log = logging.getLogger(__name__)

NAME_FORMAT = "%Y-%m-%d_%H-%M-%S-%f%z"  # mediamtx.RECORD_NAME
# Older recordings have no offset and were written by MediaMTX running in UTC.
LEGACY_NAME_FORMAT = "%Y-%m-%d_%H-%M-%S-%f"
GRACE_SECONDS = 30
FRAGMENT_SECONDS = 8
# How soon after a fragment's end the next file must start to count as a recorder reset.
FRAGMENT_GAP_SECONDS = 5


def parse_start(path: Path) -> datetime | None:
    """Start time from the file name, as naive UTC (like the rest of the database)."""
    try:
        return datetime.strptime(path.stem, NAME_FORMAT).astimezone(UTC).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.strptime(path.stem, LEGACY_NAME_FORMAT)
    except ValueError:
        return None


def _files(settings: Settings, camera_id: str) -> list[Path]:
    """The camera's recordings, oldest first."""
    cam_dir = settings.recordings_dir / camera_id
    if not cam_dir.is_dir():
        return []
    # By time, not name: local-time names don't sort chronologically across DST changes.
    starts = {p: parse_start(p) for p in cam_dir.iterdir() if p.suffix == ".mp4"}
    return sorted((p for p, t in starts.items() if t), key=lambda p: starts[p])


def finished_files(settings: Settings, camera_id: str) -> list[Path]:
    files = _files(settings, camera_id)
    if not files:
        return []
    limit = settings.segment_seconds_for(camera_id) + GRACE_SECONDS
    newest = files[-1]
    done = files[:-1]
    if time.time() - newest.stat().st_mtime > limit:
        done.append(newest)
    return done


def _reset_fragment(path: Path, duration_s: float, next_start: datetime | None,
                    segment_seconds: float) -> bool:
    start = parse_start(path)
    if next_start is None or start is None:
        return False
    if duration_s >= min(FRAGMENT_SECONDS, segment_seconds / 2):
        return False
    return (next_start - start).total_seconds() <= duration_s + FRAGMENT_GAP_SECONDS


def scan_once(settings: Settings) -> int:
    added = 0
    # Include disabled cameras so their last segment still gets registered.
    for cam in settings.all_cameras(include_disabled=True):
        files = finished_files(settings, cam.id)
        if not files:
            continue
        everything = _files(settings, cam.id)
        next_start = {p: parse_start(n) for p, n in pairwise(everything)}
        rels = [str(p.relative_to(settings.recordings_dir)) for p in files]
        with session_scope() as s:
            known = set(s.scalars(select(Segment.path).where(Segment.path.in_(rels))))
        for path, rel in zip(files, rels, strict=True):
            if rel in known:
                continue
            try:
                info = ffmpeg.probe(path)
            except ffmpeg.FfmpegError as e:
                # Typically an fMP4 cut short by a disconnect; register it as failed so we
                # don't probe it forever, but keep the file for manual inspection.
                log.warning("unreadable segment %s: %s", rel, e)
                info = None
            if info and _reset_fragment(path, info.duration_s, next_start.get(path),
                                        settings.segment_seconds_for(cam.id)):
                log.info("dropping %s: %.1f s before the recorder started over", rel,
                         info.duration_s)
                path.unlink(missing_ok=True)
                continue
            with session_scope() as s:
                s.add(Segment(
                    camera_id=cam.id, path=rel, start_at=parse_start(path),
                    duration_s=info.duration_s if info else 0,
                    width=info.width if info else None, height=info.height if info else None,
                    size_bytes=path.stat().st_size,
                    status=SegmentStatus.NEW if info and info.duration_s > 0
                    else SegmentStatus.FAILED,
                    error=None if info else "unreadable recording",
                ))
            added += 1
    if added:
        log.info("registered %d new segment(s)", added)
    return added
