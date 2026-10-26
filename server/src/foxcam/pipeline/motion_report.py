"""Whether motion detection would help, from recordings already analysed (shadow mode).

Motion is measured on every new recording, whatever the profiles say, so for each camera
and profile the report can tell what the two motion options *would* have done with the
detections it got, split by the correct / wrong marks on the Detections page:

- ``skip_without_motion``: recordings where nothing moved (no worker time), and the
  targets found in them anyway (these would have been missed);
- ``still_minutes``: targets standing still that long (no match), with the profile's
  minutes, or the default 10 when it has the option off.

``backfill`` measures motion on recordings from before motion detection, from their stored
footage (with the censor areas they were analysed with), so existing marks count too. It
changes nothing else: detections and events stay as they are.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select

from ..config import Settings
from ..db import session_scope
from ..models import (
    CameraState,
    Detection,
    Job,
    MaskVersion,
    Segment,
    SegmentStatus,
    utcnow,
)
from . import motion, still
from .compaction import segment_source

log = logging.getLogger(__name__)

FEEDBACK = ("correct", "wrong", "unrated")
# What the "still" counts try when a profile has the option off.
DEFAULT_STILL_MINUTES = 10


def _rated(d: Detection) -> str:
    return "unrated" if d.feedback is None else "correct" if d.feedback else "wrong"


def _counts() -> dict[str, int]:
    return dict.fromkeys(FEEDBACK, 0)


def report(settings: Settings, since: datetime, camera: str | None = None) -> list[dict[str, Any]]:
    """One row per camera and profile with recordings in the period. Profiles that no
    longer exist are left out: their options cannot be turned on any more."""
    window = settings.motion.window_seconds
    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def row(camera_id: str, profile_id: str | None) -> dict[str, Any] | None:
        profile = settings.profile(profile_id)
        if profile is None or profile_id is None:
            return None
        if (camera_id, profile_id) not in rows:
            rows[camera_id, profile_id] = {
                "camera_id": camera_id, "profile_id": profile_id,
                "profile_name": profile.display_name,
                "still_minutes": profile.still_minutes,
                # The minutes the "still" counts are for.
                "still_minutes_tried": (profile.still_minutes if profile.still_minutes is not None
                                        else DEFAULT_STILL_MINUTES),
                "skip_without_motion": profile.skip_without_motion,
                "recordings": 0, "measured": 0, "without_motion": 0,
                # Targets in measured recordings; of them, the ones in still recordings
                # (skipping would miss them) and the ones standing still.
                "targets": _counts(), "in_still": _counts(), "standing_still": _counts(),
            }
        return rows[camera_id, profile_id]

    with session_scope() as s:
        seg_q = select(Segment).where(Segment.start_at >= since,
                                      Segment.status == SegmentStatus.DONE)
        if camera:
            seg_q = seg_q.where(Segment.camera_id == camera)
        segments = {g.id: g for g in s.scalars(seg_q)}
        if not segments:
            return []
        ids = list(segments)
        for seg_id, profile_id in s.execute(
                select(Job.segment_id, Job.profile_id).where(Job.segment_id.in_(ids))
                .distinct()):
            g = segments[seg_id]
            if (r := row(g.camera_id, profile_id)) is None:
                continue
            r["recordings"] += 1
            if g.motion_score is not None:
                r["measured"] += 1
                r["without_motion"] += g.motion_score == 0
        targets = [d for d in s.scalars(select(Detection).where(
                       Detection.segment_id.in_(ids), Detection.is_target.is_(True))
                       .order_by(Detection.ts))
                   if segments[d.segment_id].motion_score is not None]
        cameras: dict[str, still.Frames] = {}
        for d in targets:
            g = segments[d.segment_id]
            if (r := row(g.camera_id, d.profile_id)) is None:
                continue
            kind = _rated(d)
            r["targets"][kind] += 1
            if g.motion_score == 0:
                r["in_still"][kind] += 1
            frames = cameras.setdefault(g.camera_id, still.Frames(s, g.camera_id))
            if frames.still(d.ts, still.bbox_of(d), r["still_minutes_tried"], window):
                r["standing_still"][kind] += 1
    return sorted(rows.values(), key=lambda r: (r["camera_id"], r["profile_id"]))


class Backfill:
    """One backfill at a time, in a thread of the server process."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {"running": False, "done": 0, "total": 0,
                                      "failed": 0, "started_at": None, "finished_at": None}

    def start(self, settings: Settings, since: datetime) -> bool:
        with self.lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "done": 0, "total": 0, "failed": 0,
                          "started_at": utcnow().isoformat() + "Z", "finished_at": None}
        threading.Thread(target=self._run, args=(settings, since), daemon=True,
                         name="motion-backfill").start()
        return True

    def _run(self, settings: Settings, since: datetime) -> None:
        try:
            ids = pending(since)
            self.state["total"] = len(ids)
            for seg_id in ids:
                try:
                    measure_segment(settings, seg_id)
                except Exception as e:  # noqa: BLE001 - footage gone, broken file
                    log.warning("motion backfill: segment %s: %s", seg_id, e)
                    self.state["failed"] += 1
                self.state["done"] += 1
        finally:
            self.state["running"] = False
            self.state["finished_at"] = utcnow().isoformat() + "Z"


backfill = Backfill()


def pending(since: datetime) -> list[int]:
    """Recordings without a motion measurement (or one from before faint motion was kept):
    the ones with targets first (they decide the report's accuracy), then the rest, newest
    first."""
    with session_scope() as s:
        base = select(Segment.id).where(Segment.start_at >= since,
                                        or_(Segment.motion_score.is_(None),
                                            Segment.motion_faint.is_(None)),
                                        Segment.status == SegmentStatus.DONE)
        with_targets = set(s.scalars(base.where(Segment.id.in_(
            select(Detection.segment_id).where(Detection.is_target.is_(True))))))
        rest = [i for i in s.scalars(base.order_by(Segment.start_at.desc()))
                if i not in with_targets]
        return sorted(with_targets, reverse=True) + rest


def measure_segment(settings: Settings, segment_id: int) -> motion.Motion | None:
    """Measure a recording's motion from its stored footage, like preprocessing does from
    the derivative (same frame rate and censor areas), and store it."""
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None:
            return None
        src = segment_source(settings, seg)
        if src is None or not src.path.exists():
            raise FileNotFoundError("the recording is gone")
        mask = s.get(MaskVersion, seg.mask_version_id) if seg.mask_version_id else None
        state = s.get(CameraState, seg.camera_id)
        sensitivity = (state and state.motion_sensitivity) or settings.motion.sensitivity
        ignore = [*(mask.rects if mask else []), *((state and state.motion_rects) or [])]
        fps = seg.derived_fps or settings.processing.sample_fps
        night = bool(seg.is_night)
    frames = motion.decode(src.path, fps, src.offset_s, src.duration_s)
    found = motion.analyse(frames, fps, sensitivity=sensitivity, night=night, ignore=ignore,
                           light_change_fraction=settings.motion.light_change_fraction)
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is not None:
            seg.motion_score, seg.motion_boxes = found.score, found.boxes
            seg.motion_faint = found.faint
    return found
