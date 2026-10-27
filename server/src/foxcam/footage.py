"""Footage missing from a recording, an event's clip or an archive, for the player's
timeline.

Two kinds:

- a gap between recordings: the recorder stopped and started again (MediaMTX does when the
  stream's time drifts away from the clock, e.g. a cloud relay stalling and catching up,
  and waits for a keyframe to go on). Event clips keep it as a gap, the picture holding
  there; archives join their recordings back to back, so there it is a moment.
- a skip inside a recording: the camera's clock jumped ahead in the stream, what it never
  delivered (`FootageSkip`, noted by ingest, ts_retime.py). The recording runs on without
  a pause, so it is a moment too.

Each is ``[from_s, to_s, missing_s]`` in the video's own time (from == to: a moment).
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session_scope
from .models import Archive, Event, FootageSkip, Segment, utcnow

log = logging.getLogger(__name__)

#: A pause between two recordings longer than this is missing footage (a new recording
#: starts within a few milliseconds of the last one's end).
GAP_S = 1.0

Gap = list[float]


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


def _moment(at_s: float, missing_s: float) -> Gap:
    return [round(at_s, 2), round(at_s, 2), missing_s]


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
    for a, b, gap in between(segments):
        start = max((a.end_at - clip_start).total_seconds(), 0)
        end = min((b.start_at - clip_start).total_seconds(), length_s)
        if end > start:
            out.append([round(start, 2), round(end, 2), round(gap, 1)])
    return out


def segment_gaps(seg: Segment) -> list[Gap]:
    """Skips inside one recording, in its own time."""
    with session_scope() as s:
        return [_moment((k.at - seg.start_at).total_seconds(), k.missing_s)
                for k in _skips(s, seg.camera_id, seg.start_at, seg.end_at)]


def event_gaps(event_id: int) -> list[Gap]:
    """Missing footage in an event's clip, in clip time."""
    with session_scope() as s:
        ev = s.get(Event, event_id)
        if ev is None or ev.clip_start is None:
            return []
        length = ev.clip_duration_s or 0
        gaps = list(ev.clip_gaps or [])
        end = ev.clip_start + timedelta(seconds=length)
        for k in _skips(s, ev.camera_id, ev.clip_start, end):
            gaps.append(_moment((k.at - ev.clip_start).total_seconds(), k.missing_s))
        return sorted(g for g in gaps if 0 <= g[0] <= length)


def archive_gaps(archive_id: int) -> list[Gap]:
    """Missing footage in an archive, in its own time: where recordings with a gap between
    them were joined, and the skips inside them."""
    with session_scope() as s:
        arc = s.get(Archive, archive_id)
        if arc is None:
            return []
        segs = list(s.scalars(select(Segment).where(Segment.archive_id == archive_id)))
        gaps = [_moment(b.archive_offset_s or 0, round(gap, 1)) for _, b, gap in between(segs)]
        for g in segs:
            for k in _skips(s, g.camera_id, g.start_at, g.end_at):
                at = (g.archive_offset_s or 0) + (k.at - g.start_at).total_seconds()
                gaps.append(_moment(at, k.missing_s))
        return sorted(gaps)
