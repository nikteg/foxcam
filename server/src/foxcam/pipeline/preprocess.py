"""Turn a newly registered segment into a worker job.

1. Tag light conditions (IR night mode => grayscale => ~0 saturation; plus sun elevation).
2. Make a thumbnail.
3. Private camera (or no detection profile) => stop here. Otherwise burn the current
   censor rectangles into a low-fps derivative and enqueue one job per detection profile.
4. Measure motion in the derivative (pipeline/motion.py). Profiles with
   ``skip_without_motion`` get their jobs marked skipped when nothing moved.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from .. import changes, ffmpeg, footage, hwaccel, sprites
from ..config import Settings
from ..db import session_scope
from ..models import CameraState, Job, JobState, MaskVersion, Segment, SegmentStatus, utcnow
from ..profiles import camera_profiles
from . import motion
from .compaction import playable_file
from .queue import settle_segment

log = logging.getLogger(__name__)


def _vaapi_device(settings: Settings) -> str | None:
    """The GPU for decoding and scaling, unless hardware video is switched off."""
    if settings.mediamtx.hwaccel in ("none", "software"):
        return None
    return settings.mediamtx.hw_device or hwaccel.DEFAULT_DEVICE


def sun_elevation(settings: Settings, seg: Segment) -> float | None:
    loc = settings.location
    if loc.latitude is None or loc.longitude is None:
        return None
    from astral import Observer
    from astral.sun import elevation

    return float(elevation(Observer(loc.latitude, loc.longitude),
                           seg.start_at.replace(tzinfo=UTC)))


def current_mask(camera_id: str) -> tuple[bool, MaskVersion | None]:
    with session_scope() as s:
        state = s.get(CameraState, camera_id)
        if state is None:
            return False, None
        mask = s.get(MaskVersion, state.mask_version_id) if state.mask_version_id else None
        return state.private, mask


def motion_settings(settings: Settings, camera_id: str) -> tuple[str, list]:
    """The camera's motion sensitivity and motion-ignore areas."""
    with session_scope() as s:
        state = s.get(CameraState, camera_id)
        sensitivity = (state and state.motion_sensitivity) or settings.motion.sensitivity
        return sensitivity, list((state and state.motion_rects) or [])


def pending_segment_ids(limit: int = 50) -> list[int]:
    with session_scope() as s:
        return list(s.scalars(
            select(Segment.id).where(Segment.status == SegmentStatus.NEW)
            .order_by(Segment.start_at.desc()).limit(limit)
        ))


class Activity:
    """What preprocessing is doing and has done lately (in memory, since the server started):
    for the Jobs page, where recordings wait before they get any job."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running: dict[int, datetime] = {}
        # (finished at, seconds, prepared without an error)
        self.recent: deque[tuple[datetime, float, bool]] = deque(maxlen=2000)

    def started(self, segment_id: int) -> None:
        with self.lock:
            self.running[segment_id] = utcnow()
        changes.notify()

    def finished(self, segment_id: int, seconds: float, ok: bool | None) -> None:
        with self.lock:
            self.running.pop(segment_id, None)
            if ok is not None:  # None: nothing to do (no longer new)
                self.recent.append((utcnow(), seconds, ok))
        changes.notify()  # also: its jobs are queued now


activity = Activity()


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"  # stored as naive UTC


def status(settings: Settings, limit: int = 100) -> dict[str, Any]:
    """Recordings waiting for preprocessing, the ones being prepared, and the pace."""
    now = utcnow()
    with activity.lock:
        running = dict(activity.running)
        recent = [r for r in activity.recent if r[0] >= now - timedelta(hours=1)]
    waiting = (Segment.status == SegmentStatus.NEW) & Segment.deleted_at.is_(None)
    with session_scope() as s:
        count = s.scalar(select(func.count()).select_from(Segment).where(waiting)) or 0
        oldest = s.scalar(select(func.min(Segment.start_at)).where(waiting))
        failed = s.scalar(select(func.count()).select_from(Segment).where(
            Segment.status == SegmentStatus.FAILED, Segment.deleted_at.is_(None))) or 0
        # In the order they are prepared (pending_segment_ids): the newest first.
        rows = s.execute(select(Segment.id, Segment.camera_id, Segment.start_at)
                         .where(waiting | Segment.id.in_(running))
                         .order_by(Segment.start_at.desc()).limit(limit + len(running))).all()
    segments = [{"id": i, "camera_id": c, "start_at": _iso(t),
                 "started_at": _iso(running[i]) if i in running else None} for i, c, t in rows]
    segments.sort(key=lambda g: g["started_at"] is None)  # the ones being prepared first
    took = [sec for _, sec, ok in recent if ok]
    return {
        "waiting": count,
        "oldest_waiting_age_s": (now - oldest).total_seconds() if oldest else None,
        "preparing": len(running),
        "concurrency": settings.processing.concurrency,
        "done_last_hour": len(took),
        "failed_last_hour": len(recent) - len(took),
        "failed": failed,
        "avg_seconds_last_hour": round(sum(took) / len(took), 1) if took else None,
        "segments": segments[:limit],
    }


def preprocess_segment(settings: Settings, segment_id: int) -> None:
    activity.started(segment_id)
    t0 = time.monotonic()
    ok = None
    try:
        ok = _preprocess(settings, segment_id)
    finally:
        activity.finished(segment_id, time.monotonic() - t0, ok)


def _preprocess(settings: Settings, segment_id: int) -> bool | None:
    """Prepare the recording; whether that went without an error (None: not new)."""
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None or seg.status != SegmentStatus.NEW:
            return None
        camera_id, rel = seg.camera_id, seg.path
    updates: dict = {}
    try:
        # Its own file, or (analysed again after compaction) cut out of its hourly file.
        src = playable_file(settings, segment_id)
        if src is None:
            raise FileNotFoundError("the recording file is gone")
        stats = ffmpeg.light_stats(src, seg.duration_s)
        elev = sun_elevation(settings, seg)
        if stats:
            updates.update(saturation_avg=stats.saturation_avg, luma_avg=stats.luma_avg)
            updates["is_night"] = stats.saturation_avg < settings.processing.night_saturation_threshold
        elif elev is not None:
            updates["is_night"] = elev < -6
        updates["sun_elevation"] = elev

        try:
            updates["skips"] = footage.find_skips(src, camera_id, seg.start_at, seg.duration_s)
        except Exception as e:  # noqa: BLE001 - only the player's marks
            log.warning("segment %s: no skips found: %s", segment_id, e)

        thumb = settings.thumbs_dir / Path(rel).with_suffix(".jpg")
        ffmpeg.thumbnail(src, thumb, at_s=min(1.0, seg.duration_s / 2))
        updates["thumb_path"] = str(thumb.relative_to(settings.data_dir))
        try:  # the player's timeline previews, ready before anyone plays it (<1 s)
            sprites.get(settings, "segment", segment_id)
        except Exception as e:  # noqa: BLE001 - made on first play instead
            log.warning("segment %s: no thumbnail sprite: %s", segment_id, e)

        private, mask = current_mask(camera_id)
        profiles = [] if private else camera_profiles(settings, camera_id)
        if private:
            updates["status"] = SegmentStatus.PRIVATE
        elif not profiles:
            updates["status"] = SegmentStatus.DONE  # nothing to look for on this camera
        else:
            # One derivative for all profiles and detectors, at the highest frame rate any
            # of them needs.
            fps = max(min(p.fps_for(d.id) or settings.processing.sample_fps,
                          settings.processing.sample_fps)
                      for p in profiles for d in p.detectors)
            derived = settings.derived_dir / rel
            ffmpeg.make_masked_derivative(src, derived, mask.rects if mask else [], fps,
                                          settings.processing.max_width,
                                          vaapi_device=_vaapi_device(settings))
            updates.update(derived_path=str(derived.relative_to(settings.data_dir)),
                           derived_fps=fps, mask_version_id=mask.id if mask else None,
                           status=SegmentStatus.QUEUED)
            try:
                sensitivity, ignore = motion_settings(settings, camera_id)
                found = motion.measure(derived, fps, sensitivity=sensitivity,
                                       night=bool(updates.get("is_night")), ignore=ignore,
                                       light_change_fraction=settings.motion.light_change_fraction)
                updates.update(motion_score=found.score, motion_boxes=found.boxes,
                               motion_faint=found.faint)
            except Exception as e:  # noqa: BLE001 - unmeasured counts as moving
                log.warning("segment %s: no motion measured: %s", segment_id, e)
                updates.update(motion_score=None, motion_boxes=None, motion_faint=None)
    except Exception as e:
        log.exception("preprocessing segment %s failed", rel)
        updates.update(status=SegmentStatus.FAILED, error=f"preprocess: {e}"[:500])

    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None:
            return None
        for k, v in updates.items():
            setattr(seg, k, v)
        if seg.status == SegmentStatus.QUEUED:
            still = seg.motion_score == 0  # measured, and nothing moved
            for p in profiles:
                # Cascade detectors (run: on_hit) get theirs when the one before finds
                # something (queue.complete).
                for d in p.always:
                    job = Job(segment_id=seg.id, profile_id=p.id, detector_id=d.id)
                    if still and p.skip_without_motion:
                        job.state, job.finished_at = JobState.SKIPPED, utcnow()
                        job.error = "skipped – no motion"
                    s.add(job)
            if still:
                settle_segment(settings, seg)  # done already when every job was skipped
    return updates.get("status") != SegmentStatus.FAILED
