"""Whether a target is standing still: its box overlapped no motion for the profile's
``still_minutes`` (pipeline/motion.py measures the motion).

A target that stands still that long stops counting: it is stored with ``still`` set and
is no match (no event, notification or "with a match"), until it moves again. A parked car
stops counting ``still_minutes`` after it parked, and a fox resting by the food keeps
counting for those minutes. ``still_minutes = 0``: a target must be moving right now (within
``motion.window_seconds``); ``None``: off.

The motion before a recording comes from the camera's earlier recordings, so a target
that keeps standing there is recognised across them. A recording without a measurement
counts as moving.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..models import Segment
from .motion import Box

#: Frames with motion on one camera, oldest first: (time, boxes around what moved).
History = list[tuple[datetime, list[Box]]]


def history(s, camera_id: str, start: datetime, end: datetime) -> History:
    """The camera's frames with motion between ``start`` and ``end``."""
    frames: History = []
    for g in s.scalars(select(Segment).where(
            Segment.camera_id == camera_id, Segment.motion_boxes.is_not(None),
            Segment.start_at <= end,
            Segment.start_at >= start - timedelta(hours=1))):  # segments are shorter
        for at, boxes in g.motion_boxes or []:
            t = g.start_at + timedelta(milliseconds=at)
            if start <= t <= end:
                frames.append((t, boxes))
    frames.sort(key=lambda f: f[0])
    return frames


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def standing_still(frames: History, ts: datetime, bbox: Box | None, minutes: float,
                   window_s: float) -> bool:
    """No motion overlapped ``bbox`` (None: anywhere) from ``minutes`` before ``ts`` until
    ``window_s`` after it."""
    lo = ts - timedelta(minutes=minutes, seconds=window_s)
    hi = ts + timedelta(seconds=window_s)
    for t, boxes in frames[bisect_left(frames, lo, key=lambda f: f[0]):]:
        if t > hi:
            break
        if bbox is None or any(_overlap(bbox, b) for b in boxes):
            return False
    return True


def bbox_of(d: Any) -> Box | None:
    """A detection's box (None when it has none)."""
    return None if d.bbox_x is None else [d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h]


class Frames:
    """A camera's motion history, loaded an hour at a time as targets are looked at in
    time order (a rebuild walks every target there is)."""

    def __init__(self, s, camera_id: str) -> None:
        self.s, self.camera_id = s, camera_id
        self.lo = self.hi = None
        self.frames: History = []

    def still(self, ts: datetime, bbox: Box | None, minutes: float, window_s: float) -> bool:
        lo = ts - timedelta(minutes=minutes, seconds=window_s)
        hi = ts + timedelta(seconds=window_s)
        if self.lo is None or lo < self.lo or hi > self.hi:
            self.lo, self.hi = lo, hi + timedelta(hours=1)
            self.frames = history(self.s, self.camera_id, self.lo, self.hi)
        return standing_still(self.frames, ts, bbox, minutes, window_s)


def mark(settings: Any, s, detections: list[Any]) -> None:
    """Set ``still`` on target detections from their profile's ``still_minutes`` (None
    when off, or when their recording has no motion measured)."""
    window = settings.motion.window_seconds
    profiles: dict[str | None, Any] = {}
    cameras: dict[str, Frames] = {}
    for d in sorted(detections, key=lambda d: d.ts):
        d.still = None
        if d.profile_id not in profiles:
            profiles[d.profile_id] = settings.profile(d.profile_id)
        profile = profiles[d.profile_id]
        seg = s.get(Segment, d.segment_id)
        if (d.is_target and profile is not None and profile.still_minutes is not None
                and seg is not None and seg.motion_score is not None):
            frames = cameras.setdefault(d.camera_id, Frames(s, d.camera_id))
            d.still = frames.still(d.ts, bbox_of(d), profile.still_minutes, window)
