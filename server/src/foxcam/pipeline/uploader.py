"""Upload finished clips (and optionally recordings) to S3-compatible storage."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select

from ..config import Settings
from ..db import session_scope
from ..models import Archive, Event, Segment, SegmentStatus
from ..storage import StorageError, get_storage

log = logging.getLogger(__name__)

BATCH = 20


@dataclass
class UploadState:
    """Shown in the UI (Settings → Storage)."""

    last_error: str | None = None
    uploaded: int = 0


state = UploadState()


def upload_pending(settings: Settings) -> int:
    store = get_storage(settings)
    if store is None:
        return 0
    cfg = settings.storage.s3
    done = 0
    try:
        if cfg.clips:
            done += _upload_clips(settings, store)
        if cfg.recordings:
            done += _upload_recordings(settings, store)
    except StorageError as e:
        state.last_error = str(e)
        log.warning("%s", e)
        return done
    if done:
        state.last_error = None
        state.uploaded += done
        log.info("uploaded %d file(s) to s3://%s", done, cfg.bucket)
    return done


def _upload_clips(settings: Settings, store) -> int:
    cfg = settings.storage.s3
    with session_scope() as s:
        ids = list(s.scalars(select(Event.id).where(
            Event.clip_dirty.is_(False), Event.clip_path.is_not(None),
            Event.clip_remote_key.is_(None)).limit(BATCH)))
    done = 0
    for event_id in ids:
        with session_scope() as s:
            ev = s.get(Event, event_id)
            clip_rel, thumb_rel = ev.clip_path, ev.thumb_path
        clip = settings.data_dir / clip_rel
        if not clip.exists():
            continue
        clip_key = store.key(clip_rel)
        store.upload(clip, clip_key)
        thumb_key = None
        if thumb_rel and (settings.data_dir / thumb_rel).exists():
            thumb_key = store.key(thumb_rel)
            store.upload(settings.data_dir / thumb_rel, thumb_key)
        with session_scope() as s:
            ev = s.get(Event, event_id)
            if ev.clip_dirty:  # rebuilt while uploading: upload again next round
                continue
            ev.clip_remote_key, ev.thumb_remote_key = clip_key, thumb_key
        if cfg.delete_local_clips:
            clip.unlink(missing_ok=True)
            if thumb_key:
                (settings.data_dir / thumb_rel).unlink(missing_ok=True)
        done += 1
    return done


def _upload_recordings(settings: Settings, store) -> int:
    if settings.compaction.enabled:  # upload the hourly archives, not every segment
        return _upload_archives(settings, store)
    with session_scope() as s:
        rows = s.execute(select(Segment.id, Segment.path).where(
            Segment.status.in_(SegmentStatus.TERMINAL), Segment.deleted_at.is_(None),
            Segment.remote_key.is_(None)).order_by(Segment.start_at).limit(BATCH)).all()
    done = 0
    for seg_id, rel in rows:
        path = settings.recordings_dir / rel
        if not path.exists():
            continue
        key = store.key("recordings", rel)
        store.upload(path, key)
        with session_scope() as s:
            s.get(Segment, seg_id).remote_key = key
        done += 1
    return done


def _upload_archives(settings: Settings, store) -> int:
    with session_scope() as s:
        rows = s.execute(select(Archive.id, Archive.path).where(
            Archive.deleted_at.is_(None), Archive.remote_key.is_(None))
            .order_by(Archive.start_at).limit(BATCH)).all()
    done = 0
    for arc_id, rel in rows:
        path = settings.recordings_dir / rel
        if not path.exists():
            continue
        key = store.key("recordings", rel)
        store.upload(path, key)
        with session_scope() as s:
            s.get(Archive, arc_id).remote_key = key
        done += 1
    return done


def stats(settings: Settings) -> dict:
    cfg = settings.storage.s3
    with session_scope() as s:
        clips_uploaded = s.scalar(select(func.count()).select_from(Event)
                                  .where(Event.clip_remote_key.is_not(None)))
        clips_pending = s.scalar(select(func.count()).select_from(Event).where(
            Event.clip_dirty.is_(False), Event.clip_path.is_not(None),
            Event.clip_remote_key.is_(None)))
        if settings.compaction.enabled:  # recordings go up as hourly archives
            segs_uploaded = s.scalar(select(func.count()).select_from(Archive)
                                     .where(Archive.remote_key.is_not(None)))
            segs_pending = s.scalar(select(func.count()).select_from(Archive).where(
                Archive.deleted_at.is_(None), Archive.remote_key.is_(None))) \
                if cfg.recordings else 0
        else:
            segs_uploaded = s.scalar(select(func.count()).select_from(Segment)
                                     .where(Segment.remote_key.is_not(None)))
            segs_pending = s.scalar(select(func.count()).select_from(Segment).where(
                Segment.status.in_(SegmentStatus.TERMINAL), Segment.deleted_at.is_(None),
                Segment.remote_key.is_(None))) if cfg.recordings else 0
    return {"clips_uploaded": clips_uploaded, "clips_pending": clips_pending if cfg.clips else 0,
            "recordings_uploaded": segs_uploaded, "recordings_pending": segs_pending,
            "last_error": state.last_error}
