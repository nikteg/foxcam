"""Delete old recordings.

Rules, in order:
- Events older than ``events_days`` (if > 0) are deleted and their segments unpinned.
- Unpinned segments older than ``segments_days`` are deleted, except segments still
  waiting for a worker, which are kept until ``unprocessed_max_days``.
- If ``max_disk_gb`` is set, the oldest unpinned segments are deleted until under the cap.

Segment rows are kept (``deleted_at`` set) so detection history remains browsable.

The day counts and the disk cap can also be changed in the web UI (runtime_settings.py).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select

from .. import notify, sprites
from ..config import Settings
from ..db import session_scope
from ..models import (
    Archive,
    Event,
    Job,
    JobState,
    Segment,
    SegmentStatus,
    event_segments,
    utcnow,
)
from ..storage import StorageError, get_storage

log = logging.getLogger(__name__)

def _remove_files(settings: Settings, seg: Segment) -> None:
    # A compacted segment's footage lives in its archive, which goes once all its
    # segments are gone (_remove_empty_archives).
    (settings.recordings_dir / seg.path).unlink(missing_ok=True)
    for rel in (seg.derived_path, seg.thumb_path):
        if rel:
            (settings.data_dir / rel).unlink(missing_ok=True)
    seg.derived_path = None
    seg.thumb_path = None
    seg.deleted_at = utcnow()


def _events_days(settings: Settings, profile_id: str | None, default: float) -> float:
    profile = settings.profile(profile_id)
    if profile is not None and profile.events_days is not None:
        return profile.events_days
    return default


def _expire_events(settings: Settings) -> int:
    """Events older than their profile's `events_days` (or `retention.events_days`)."""
    default = settings.retention.events_days
    limits = [default,
              *(p.events_days for p in settings.all_profiles(include_disabled=True)
                if p.events_days is not None)]
    positive = [d for d in limits if d > 0]
    if not positive:
        return 0
    now = utcnow()
    with session_scope() as s:
        candidates = s.scalars(select(Event).where(
            Event.last_ts < now - timedelta(days=min(positive)))).all()
        events = [ev for ev in candidates
                  if (days := _events_days(settings, ev.profile_id, default)) > 0
                  and ev.last_ts < now - timedelta(days=days)]
        store = get_storage(settings)
        for ev in events:
            for rel in (ev.clip_path, ev.thumb_path):
                if rel:
                    (settings.data_dir / rel).unlink(missing_ok=True)
            for key in (ev.clip_remote_key, ev.thumb_remote_key):
                if key and store:
                    _delete_remote(store, key)
            seg_ids = [g.id for g in ev.segments]
            s.delete(ev)
            s.flush()
            # Unpin segments no other event refers to; age-based retention then removes them.
            still = set(s.scalars(select(event_segments.c.segment_id)
                                  .where(event_segments.c.segment_id.in_(seg_ids))))
            for g in s.scalars(select(Segment).where(Segment.id.in_(seg_ids))):
                if g.id not in still:
                    g.pinned = False
        return len(events)


def _days_by_camera(settings: Settings) -> dict[str, float]:
    """Cameras whose recording profile keeps recordings longer or shorter than the default."""
    from ..recording import camera_recording_profile

    out = {}
    for cam in settings.all_cameras(include_disabled=True):
        profile, _ = camera_recording_profile(settings, cam)
        if profile.keep_days is not None:
            out[cam.id] = profile.keep_days
    return out


def _expire_by_age(settings: Settings) -> int:
    r = settings.retention
    per_camera = _days_by_camera(settings)
    # (days, camera filter); 0 days = keep forever.
    groups = [(r.segments_days, Segment.camera_id.not_in(per_camera) if per_camera else None)]
    for days in sorted(set(per_camera.values())):
        cams = [c for c, d in per_camera.items() if d == days]
        groups.append((days, Segment.camera_id.in_(cams)))
    now = utcnow()
    waiting = select(Job.segment_id).where(
        Job.state.in_([JobState.PENDING, JobState.FAILED, JobState.LEASED]))
    removed = 0
    with session_scope() as s:
        for days, cameras in groups:
            if not days:
                continue
            cutoff = now - timedelta(days=days)
            hard_cutoff = now - timedelta(days=max(r.unprocessed_max_days, days))
            query = select(Segment).where(
                Segment.deleted_at.is_(None), Segment.pinned.is_(False),
                Segment.start_at < cutoff,
                # Unprocessed footage gets extra time, so a GPU box that is offline for a
                # few days does not lose data.
                (Segment.start_at < hard_cutoff)
                | (Segment.id.not_in(waiting) & (Segment.status != SegmentStatus.NEW)))
            if cameras is not None:
                query = query.where(cameras)
            for seg in s.scalars(query).all():
                _remove_files(settings, seg)
                removed += 1
    return removed


def _enforce_disk_cap(settings: Settings) -> int:
    cap = settings.retention.max_disk_gb
    if not cap:
        return 0
    cap_bytes = cap * 1024**3
    deleted = 0
    with session_scope() as s:
        used = s.scalar(select(func.coalesce(func.sum(Segment.size_bytes), 0))
                        .where(Segment.deleted_at.is_(None))) or 0
        if used <= cap_bytes:
            return 0
        for seg in s.scalars(select(Segment).where(Segment.deleted_at.is_(None),
                                                   Segment.pinned.is_(False))
                             .order_by(Segment.start_at)):
            if used <= cap_bytes:
                break
            used -= seg.size_bytes
            _remove_files(settings, seg)
            deleted += 1
    if deleted:
        log.warning("disk cap %.2f GB reached: deleted %d oldest segment(s)", cap, deleted)
    return deleted


def _delete_remote(store, key: str) -> None:
    try:
        store.delete(key)
    except StorageError as e:
        log.warning("%s", e)


def _remove_empty_archives(settings: Settings) -> int:
    """Delete archive files whose segments have all been removed."""
    live = select(Segment.archive_id).where(Segment.archive_id.is_not(None),
                                            Segment.deleted_at.is_(None))
    with session_scope() as s:
        archives = s.scalars(select(Archive).where(Archive.deleted_at.is_(None),
                                                   Archive.id.not_in(live))).all()
        for arc in archives:
            (settings.recordings_dir / arc.path).unlink(missing_ok=True)
            sprites.remove_archive(settings, arc.id)
            arc.deleted_at = utcnow()
        return len(archives)


def _expire_remote_recordings(settings: Settings) -> int:
    """Archived recordings in S3 older than storage.s3.recordings_days (0 = keep)."""
    store = get_storage(settings)
    days = settings.storage.s3.recordings_days
    if store is None or not days:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as s:
        segs = s.scalars(select(Segment).where(Segment.remote_key.is_not(None),
                                               Segment.pinned.is_(False),
                                               Segment.start_at < cutoff)).all()
        for seg in segs:
            _delete_remote(store, seg.remote_key)
            seg.remote_key = None
        pinned = select(Segment.archive_id).where(Segment.archive_id.is_not(None),
                                                  Segment.pinned.is_(True))
        archives = s.scalars(select(Archive).where(Archive.remote_key.is_not(None),
                                                   Archive.id.not_in(pinned),
                                                   Archive.start_at < cutoff)).all()
        for arc in archives:
            _delete_remote(store, arc.remote_key)
            arc.remote_key = None
        return len(segs) + len(archives)


def run_retention(settings: Settings) -> dict[str, int]:
    result = {
        "events": _expire_events(settings),
        "segments_age": _expire_by_age(settings),
        "segments_disk": _enforce_disk_cap(settings),
        "archives": _remove_empty_archives(settings),
        "remote_recordings": _expire_remote_recordings(settings),
        "notifications": notify.cleanup(),
    }
    if any(result.values()):
        log.info("retention: %s", result)
    return result
