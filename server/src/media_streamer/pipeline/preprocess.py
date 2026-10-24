"""Turn a newly registered segment into a worker job.

1. Tag light conditions (IR night mode => grayscale => ~0 saturation; plus sun elevation).
2. Make a thumbnail.
3. Private camera (or no detection profile) => stop here. Otherwise burn the current
   censor rectangles into a low-fps derivative and enqueue one job per detection profile.
"""

from __future__ import annotations

import logging
from datetime import UTC
from pathlib import Path

from sqlalchemy import select

from .. import ffmpeg
from ..config import Settings
from ..db import session_scope
from ..models import CameraState, Job, MaskVersion, Segment, SegmentStatus
from ..profiles import camera_profiles
from .compaction import playable_file

log = logging.getLogger(__name__)


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


def pending_segment_ids(limit: int = 50) -> list[int]:
    with session_scope() as s:
        return list(s.scalars(
            select(Segment.id).where(Segment.status == SegmentStatus.NEW)
            .order_by(Segment.start_at.desc()).limit(limit)
        ))


def preprocess_segment(settings: Settings, segment_id: int) -> None:
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None or seg.status != SegmentStatus.NEW:
            return
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

        thumb = settings.thumbs_dir / Path(rel).with_suffix(".jpg")
        ffmpeg.thumbnail(src, thumb, at_s=min(1.0, seg.duration_s / 2))
        updates["thumb_path"] = str(thumb.relative_to(settings.data_dir))

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
                                          settings.processing.max_width)
            updates.update(derived_path=str(derived.relative_to(settings.data_dir)),
                           derived_fps=fps, mask_version_id=mask.id if mask else None,
                           status=SegmentStatus.QUEUED)
    except Exception as e:
        log.exception("preprocessing segment %s failed", rel)
        updates.update(status=SegmentStatus.FAILED, error=f"preprocess: {e}"[:500])

    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None:
            return
        for k, v in updates.items():
            setattr(seg, k, v)
        if seg.status == SegmentStatus.QUEUED:
            for p in profiles:
                for d in p.detectors:
                    s.add(Job(segment_id=seg.id, profile_id=p.id, detector_id=d.id))
