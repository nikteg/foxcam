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

Besides the motion that counts anywhere, two kinds count only for a box they lie in (their
centre inside it): faint motion (regions too small or too slow for the sensitivity,
pipeline/motion.py), and the detector's own boxes moving between two looks at the same
target. Both catch what the pixel test misses on its own: a small dog sniffing around at
the edge of the picture, a person walking straight away from the camera.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..models import Detection, Segment
from .motion import Box

#: Frames with motion on one camera, oldest first: (time, boxes around what moved, whether
#: the boxes count only for a box they lie in).
History = list[tuple[datetime, list[Box], bool]]

# Detections of the same label this close in time, with overlapping boxes, are two looks
# at the same target.
SAME_TARGET_SECONDS = 5.0
# The target moved between them when its box's centre moved by more than this share of
# the box's larger side, or a side grew or shrank by more than this share (coming closer,
# walking away). Detector boxes around something standing still jitter less.
BOX_SHIFT = 0.1
BOX_RESIZE = 0.15


def history(s, camera_id: str, start: datetime, end: datetime) -> History:
    """The camera's frames with motion between ``start`` and ``end``."""
    frames: History = []
    # Columns, not Segment rows: no ORM objects for what can be thousands of recordings.
    for start_at, motion_boxes, motion_faint in s.execute(select(
            Segment.start_at, Segment.motion_boxes, Segment.motion_faint).where(
            Segment.camera_id == camera_id, Segment.motion_boxes.is_not(None),
            Segment.start_at <= end,
            Segment.start_at >= start - timedelta(hours=1))):  # segments are shorter
        for faint, found in ((False, motion_boxes), (True, motion_faint)):
            for at, boxes in found or []:
                t = start_at + timedelta(milliseconds=at)
                if start <= t <= end:
                    frames.append((t, boxes, faint))
    frames += [f for f in detector_motion(s, camera_id, start, end) if start <= f[0] <= end]
    frames.sort(key=lambda f: f[0])
    return frames


def detector_motion(s, camera_id: str, start: datetime, end: datetime) -> History:
    """Where the detector's boxes moved: both boxes of each pair of looks at the same target
    that moved in between (detections marked wrong left out)."""
    pad = timedelta(seconds=SAME_TARGET_SECONDS)
    groups: dict[tuple[Any, ...], list[Any]] = {}
    for d in s.scalars(select(Detection).where(
            Detection.camera_id == camera_id, Detection.bbox_x.is_not(None),
            Detection.ts >= start - pad, Detection.ts <= end + pad,
            Detection.feedback.is_not(False)).order_by(Detection.ts)):
        groups.setdefault((d.profile_id, d.detector_id, d.label), []).append(d)
    frames: History = []
    for looks in groups.values():
        times = [d.ts for d in looks]
        for b in looks:
            # The look before: the latest earlier time, its box nearest this one.
            j = bisect_left(times, b.ts)
            if j == 0 or b.ts - times[j - 1] > pad:
                continue
            last = times[j - 1]
            earlier = looks[bisect_left(times, last):j]
            box = bbox_of(b)
            same = [bbox_of(a) for a in earlier if _overlap(bbox_of(a), box)]
            if not same:
                continue
            before = min(same, key=lambda a: _distance(a, box))
            if box_moved(before, box):
                frames += [(last, [before], True), (b.ts, [box], True)]
    return frames


def box_moved(a: Box, b: Box) -> bool:
    """Whether a target's box moved from ``a`` to ``b`` by more than detector jitter."""
    size = max(a[2], a[3], b[2], b[3])
    if _distance(a, b) > BOX_SHIFT * size:
        return True
    return any(abs(a[k] - b[k]) > BOX_RESIZE * max(a[k], b[k]) for k in (2, 3))


def _distance(a: Box, b: Box) -> float:
    """Between the boxes' centres."""
    return ((a[0] + a[2] / 2 - b[0] - b[2] / 2) ** 2
            + (a[1] + a[3] / 2 - b[1] - b[3] / 2) ** 2) ** 0.5


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _inside(box: Box, bbox: Box) -> bool:
    """Whether ``box``'s centre lies in ``bbox``."""
    x, y = box[0] + box[2] / 2, box[1] + box[3] / 2
    return bbox[0] <= x <= bbox[0] + bbox[2] and bbox[1] <= y <= bbox[1] + bbox[3]


def standing_still(frames: History, ts: datetime, bbox: Box | None, minutes: float,
                   window_s: float) -> bool:
    """No motion overlapped ``bbox`` (None: anywhere; faint motion only counts for a box)
    from ``minutes`` before ``ts`` until ``window_s`` after it."""
    lo = ts - timedelta(minutes=minutes, seconds=window_s)
    hi = ts + timedelta(seconds=window_s)
    for t, boxes, faint in frames[bisect_left(frames, lo, key=lambda f: f[0]):]:
        if t > hi:
            break
        if bbox is None:
            if not faint:
                return False
        elif any(_inside(b, bbox) if faint else _overlap(bbox, b) for b in boxes):
            return False
    return True


def bbox_of(d: Any) -> Box | None:
    """A detection's box (None when it has none)."""
    return None if d.bbox_x is None else [d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h]


class Frames:
    """A camera's motion history, loaded an hour at a time either side as targets are looked
    at in time order (a rebuild walks every target there is; lists go newest first)."""

    def __init__(self, s, camera_id: str) -> None:
        self.s, self.camera_id = s, camera_id
        self.lo = self.hi = None
        self.frames: History = []

    def still(self, ts: datetime, bbox: Box | None, minutes: float, window_s: float) -> bool:
        lo = ts - timedelta(minutes=minutes, seconds=window_s)
        hi = ts + timedelta(seconds=window_s)
        if self.lo is None or lo < self.lo or hi > self.hi:
            self.lo, self.hi = lo - timedelta(hours=1), hi + timedelta(hours=1)
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
