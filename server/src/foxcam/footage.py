"""Footage missing from a recording, an event's clip or an archive, for the player's
timeline.

Two kinds:

- a gap between recordings: the recorder stopped and started again (MediaMTX does when the
  stream's time drifts away from the clock, e.g. a cloud relay stalling and catching up,
  and waits for a keyframe to go on). Event clips keep it as a gap, the picture holding
  there; archives join their recordings back to back, so there it is a moment. How much
  footage it cost is unknown: the clock time between the recordings says little when the
  footage arrived late.
- a skip inside a recording: the camera's clock jumped ahead in the stream, footage it never
  delivered. Ingest leaves a short pause in the timestamps there (ts_retime.py) and notes
  how far the camera's clock jumped (`FootageSkip`); preparing the recording finds the
  pauses (`find_skips`), so the place is exact and the amount the camera's.

Each is ``[from_s, to_s, missing_s or None]`` in the video's own time (from == to: a
moment; None: unknown).
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session_scope
from .models import Archive, Event, FootageSkip, Segment, utcnow

log = logging.getLogger(__name__)

#: A pause between two recordings longer than this is missing footage (a new recording
#: starts within a few milliseconds of the last one's end).
GAP_S = 1.0
#: A pause in a recording's timestamps this long is a skip (ingest leaves half a second; a
#: frame lasts a tenth, older relay recordings jitter by a quarter).
SKIP_PAUSE_S = 0.4
#: How far apart (s) a pause in the recording and ingest's note of it may be, by the clock
#: (the stream drifts from the clock by a few seconds before MediaMTX splits a recording).
MATCH_S = 10.0

Gap = list[float | None]


def record_skip(camera_id: str, missing_s: float) -> None:
    """Note that the camera's stream skipped ``missing_s`` seconds just now."""
    try:
        with session_scope() as s:
            s.add(FootageSkip(camera_id=camera_id, at=utcnow(), missing_s=round(missing_s, 1)))
    except Exception:  # the stream goes on either way
        log.exception("[%s] could not note a skip in the footage", camera_id)


def _skips(s: Session, camera_id: str, start: datetime, end: datetime) -> list[FootageSkip]:
    return list(s.scalars(select(FootageSkip).where(
        FootageSkip.camera_id == camera_id, FootageSkip.at >= start, FootageSkip.at <= end,
    ).order_by(FootageSkip.at)))


def _moment(at_s: float, missing_s: float | None) -> Gap:
    return [round(at_s, 2), round(at_s, 2), missing_s]


def find_skips(path: Path, camera_id: str, start: datetime,
               duration_s: float) -> list[list[float | None]]:
    """The skips in a recording (``Segment.skips``): the pauses ingest left in its
    timestamps, each with how far the camera's clock jumped there (the nearest note by the
    clock), or None when there is no note (older footage, other sources)."""
    from . import ffmpeg

    pauses = ffmpeg.timestamp_steps(path, SKIP_PAUSE_S)
    if not pauses:
        return []
    with session_scope() as s:
        notes = _skips(s, camera_id, start - timedelta(seconds=MATCH_S),
                       start + timedelta(seconds=duration_s + MATCH_S))
        noted = [((k.at - start).total_seconds(), k.missing_s) for k in notes]
    out = []
    for at in pauses:
        near = min(noted, key=lambda n: abs(n[0] - at), default=None)
        if near is not None and abs(near[0] - at) <= MATCH_S:
            noted.remove(near)
            out.append([at, near[1]])
        else:
            out.append([at, None])
    return out


def _in_segment(s: Session, seg: Segment) -> list[tuple[float, float | None]]:
    """Skips inside a recording, in its own time: found in the file, or (prepared before
    that) where ingest noted them, by the clock."""
    if seg.skips is not None:
        return [(at, missing) for at, missing in seg.skips]
    return [((k.at - seg.start_at).total_seconds(), k.missing_s)
            for k in _skips(s, seg.camera_id, seg.start_at, seg.end_at)]


def between(segments: Iterable[Segment]) -> list[tuple[Segment, Segment, float]]:
    """Consecutive recordings with footage missing between them, and how much."""
    segs = sorted(segments, key=lambda g: g.start_at)
    out = []
    for a, b in itertools.pairwise(segs):
        gap = (b.start_at - a.end_at).total_seconds()
        if gap > GAP_S:
            out.append((a, b, gap))
    return out


def clip_gaps(segments: Iterable[Segment], clip_start: datetime, length_s: float) -> list[Gap]:
    """The gaps between recordings an event's clip keeps (pipeline/events.py), in clip
    time."""
    out = []
    for a, b, _ in between(segments):
        start = max((a.end_at - clip_start).total_seconds(), 0)
        end = min((b.start_at - clip_start).total_seconds(), length_s)
        if end > start:
            out.append([round(start, 2), round(end, 2), None])
    return out


def segment_gaps(seg: Segment) -> list[Gap]:
    """Skips inside one recording, in its own time."""
    with session_scope() as s:
        return [_moment(at, missing) for at, missing in _in_segment(s, seg)]


def event_gaps(event_id: int) -> list[Gap]:
    """Missing footage in an event's clip, in clip time."""
    with session_scope() as s:
        ev = s.get(Event, event_id)
        if ev is None or ev.clip_start is None:
            return []
        length = ev.clip_duration_s or 0
        gaps = [[start, end, None] for start, end, *_ in ev.clip_gaps or []]
        for g in ev.segments:
            shift = (g.start_at - ev.clip_start).total_seconds()  # clip time is clock time
            gaps += [_moment(shift + at, missing) for at, missing in _in_segment(s, g)]
        return sorted((g for g in gaps if 0 <= g[0] <= length), key=lambda g: g[0])


def archive_gaps(archive_id: int) -> list[Gap]:
    """Missing footage in an archive, in its own time: where recordings with a gap between
    them were joined, and the skips inside them."""
    with session_scope() as s:
        arc = s.get(Archive, archive_id)
        if arc is None:
            return []
        segs = list(s.scalars(select(Segment).where(Segment.archive_id == archive_id)))
        gaps = [_moment(b.archive_offset_s or 0, None) for _, b, _ in between(segs)]
        for g in segs:
            gaps += [_moment((g.archive_offset_s or 0) + at, missing)
                     for at, missing in _in_segment(s, g)]
        return sorted(gaps, key=lambda g: g[0])
