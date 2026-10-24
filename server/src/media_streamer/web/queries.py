"""Read models shared by the JSON API and the HTML pages."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from ..config import Settings
from ..db import session_scope
from ..mediamtx import low_path
from ..models import (
    CameraState,
    Detection,
    Event,
    Job,
    MaskVersion,
    Segment,
    SegmentStatus,
    Worker,
    utcnow,
)
from ..pipeline import queue
from ..profiles import camera_profiles

WORKER_ONLINE_SECONDS = 120


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def camera_state(camera_id: str) -> dict[str, Any]:
    with session_scope() as s:
        st = s.get(CameraState, camera_id)
        mask = s.get(MaskVersion, st.mask_version_id) if st and st.mask_version_id else None
        return {"private": bool(st and st.private), "rects": mask.rects if mask else [],
                "mask_version_id": mask.id if mask else None}


def save_camera_state(camera_id: str, rects: list[list[float]], private: bool) -> int | None:
    with session_scope() as s:
        st = s.get(CameraState, camera_id) or CameraState(camera_id=camera_id)
        s.add(st)
        current = s.get(MaskVersion, st.mask_version_id) if st.mask_version_id else None
        if not rects:
            st.mask_version_id = None
        elif (current.rects if current else []) != rects:
            # Versions are immutable so each segment records exactly which mask it got.
            mv = MaskVersion(camera_id=camera_id, rects=rects)
            s.add(mv)
            s.flush()
            st.mask_version_id = mv.id
        st.private = private
        return st.mask_version_id


def cameras(settings: Settings, live: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    with session_scope() as s:
        for cam in settings.all_cameras(include_disabled=True):
            last = s.scalars(select(Segment).where(Segment.camera_id == cam.id)
                             .order_by(Segment.start_at.desc()).limit(1)).first()
            path = live.get(cam.id, {})
            out.append({
                "id": cam.id, "name": cam.display_name, "source": cam.source.type,
                "source_config": _public_source(cam.source.model_dump()),
                "managed": cam.managed, "enabled": cam.enabled,
                "segment_seconds_override": cam.segment_seconds,
                "webrtc_url": f"{settings.mediamtx.webrtc_public_url.rstrip('/')}/{cam.id}/",
                "hls_url": f"{settings.mediamtx.hls_public_url.rstrip('/')}/{cam.id}/",
                **_low_urls(settings, cam.id),
                "segment_seconds": settings.segment_seconds_for(cam.id),
                "online": bool(path.get("ready")),
                "online_since": path.get("readyTime"),
                "readers": len(path.get("readers") or []),
                "bytes_received": path.get("bytesReceived"),
                "tracks": path.get("tracks") or [],
                "last_segment_at": iso(last.start_at) if last else None,
                "last_is_night": last.is_night if last else None,
                "profiles": [p.id for p in camera_profiles(settings, cam.id)],
                "profiles_source": _profiles_source(cam),
                **camera_state(cam.id),
            })
    return out


def _low_urls(settings: Settings, camera_id: str) -> dict[str, str | None]:
    m = settings.mediamtx
    if not m.low_data.enabled:
        return {"webrtc_low_url": None, "hls_low_url": None}
    path = low_path(camera_id)
    return {"webrtc_low_url": f"{m.webrtc_public_url.rstrip('/')}/{path}/",
            "hls_low_url": f"{m.hls_public_url.rstrip('/')}/{path}/"}


def _profiles_source(cam) -> str:
    """Where the camera's profile selection comes from: ui, config or default."""
    with session_scope() as s:
        st = s.get(CameraState, cam.id)
        if st is not None and st.profiles is not None:
            return "ui"
    return "config" if cam.profiles is not None else "default"


def overview(settings: Settings) -> dict[str, Any]:
    now = utcnow()
    with session_scope() as s:
        seg_counts = dict(s.execute(select(Segment.status, func.count())
                                    .where(Segment.deleted_at.is_(None))
                                    .group_by(Segment.status)).all())
        stored = s.scalar(select(func.coalesce(func.sum(Segment.size_bytes), 0))
                          .where(Segment.deleted_at.is_(None)))
        events_24h = s.scalar(select(func.count()).select_from(Event)
                              .where(Event.last_ts >= now - timedelta(hours=24)))
        events_total = s.scalar(select(func.count()).select_from(Event))
    disk = shutil.disk_usage(settings.recordings_dir)
    return {
        "segments": seg_counts,
        "recordings_bytes": stored,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "events_24h": events_24h,
        "events_total": events_total,
        "queue": queue.stats(),
    }


def segment_dict(g: Segment) -> dict[str, Any]:
    return {
        "id": g.id, "camera_id": g.camera_id, "start_at": iso(g.start_at),
        "duration_s": round(g.duration_s, 1), "size_bytes": g.size_bytes, "status": g.status,
        "error": g.error, "is_night": g.is_night, "saturation_avg": g.saturation_avg,
        "sun_elevation": g.sun_elevation, "has_target": g.has_target,
        "max_confidence": g.max_confidence, "pinned": g.pinned,
        "deleted": g.deleted_at is not None, "mask_version_id": g.mask_version_id,
        "remote": g.remote_key is not None,
        "thumb_url": f"/api/segments/{g.id}/thumb" if g.thumb_path else None,
        "download_url": None if g.deleted_at and not g.remote_key
        else f"/api/segments/{g.id}/download",
    }


def segments(camera_id: str | None = None, day: str | None = None, only_targets: bool = False,
             include_deleted: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    q = select(Segment).order_by(Segment.start_at.desc()).limit(limit)
    if camera_id:
        q = q.where(Segment.camera_id == camera_id)
    if day:
        start = datetime.fromisoformat(day)
        q = q.where(Segment.start_at >= start, Segment.start_at < start + timedelta(days=1))
    if only_targets:
        q = q.where(Segment.has_target.is_(True))
    if not include_deleted:
        q = q.where(Segment.deleted_at.is_(None))
    with session_scope() as s:
        return [segment_dict(g) for g in s.scalars(q)]


def detection_dict(d: Detection) -> dict[str, Any]:
    return {
        "id": d.id, "segment_id": d.segment_id, "camera_id": d.camera_id, "ts": iso(d.ts),
        "profile_id": d.profile_id, "event_id": d.event_id,
        "offset_ms": d.offset_ms, "label": d.label, "confidence": round(d.confidence, 3),
        "bbox": d.bbox, "is_target": d.is_target, "model": d.model, "worker_id": d.worker_id,
        "feedback": d.feedback, "is_night": d.segment.is_night if d.segment else None,
    }


def detections(camera_id: str | None = None, label: str | None = None,
               only_targets: bool = False, min_confidence: float = 0.0,
               limit: int = 200, segment_id: int | None = None,
               profile_id: str | None = None) -> list[dict[str, Any]]:
    q = select(Detection).where(Detection.confidence >= min_confidence) \
        .order_by(Detection.ts.desc()).limit(limit)
    if segment_id is not None:
        q = q.where(Detection.segment_id == segment_id)
    if camera_id:
        q = q.where(Detection.camera_id == camera_id)
    if label:
        q = q.where(func.lower(Detection.label) == label.lower())
    if profile_id:
        q = q.where(Detection.profile_id == profile_id)
    if only_targets:
        q = q.where(Detection.is_target.is_(True))
    with session_scope() as s:
        return [detection_dict(d) for d in s.scalars(q)]


def set_feedback(detection_id: int, correct: bool | None) -> bool:
    with session_scope() as s:
        d = s.get(Detection, detection_id)
        if d is None:
            return False
        d.feedback = correct
        d.feedback_at = utcnow() if correct is not None else None
        return True


def events(camera_id: str | None = None, limit: int = 100,
           profile_id: str | None = None, label: str | None = None) -> list[dict[str, Any]]:
    q = select(Event).order_by(Event.first_ts.desc()).limit(limit)
    if camera_id:
        q = q.where(Event.camera_id == camera_id)
    if profile_id:
        q = q.where(Event.profile_id == profile_id)
    if label:
        q = q.where(func.lower(Event.label) == label.lower())
    with session_scope() as s:
        return [{
            "id": e.id, "camera_id": e.camera_id, "profile_id": e.profile_id, "label": e.label,
            "first_ts": iso(e.first_ts),
            "last_ts": iso(e.last_ts), "max_confidence": round(e.max_confidence, 3),
            "detection_count": e.detection_count, "is_night": e.is_night,
            "clip_url": f"/api/events/{e.id}/clip" if e.clip_path or e.clip_remote_key else None,
            "thumb_url": f"/api/events/{e.id}/thumb" if e.thumb_path or e.thumb_remote_key
            else None,
            "remote": e.clip_remote_key is not None,
            "clip_duration_s": e.clip_duration_s, "building": e.clip_dirty,
            "clip_error": e.clip_error,
        } for e in s.scalars(q)]


def queue_view(limit: int = 100, state: str | None = None) -> dict[str, Any]:
    now = utcnow()
    q = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
    if state:
        q = q.where(Job.state == state)
    with session_scope() as s:
        workers = [{
            "id": w.id, "info": w.info, "tags": w.tags or [], "last_seen": iso(w.last_seen),
            "online": (now - w.last_seen).total_seconds() < WORKER_ONLINE_SECONDS,
            "current_job_id": w.current_job_id, "jobs_done": w.jobs_done,
            "jobs_failed": w.jobs_failed,
        } for w in s.scalars(select(Worker).order_by(Worker.last_seen.desc()))]
        jobs = [{
            "id": j.id, "segment_id": j.segment_id, "camera_id": j.segment.camera_id,
            "profile_id": j.profile_id,
            "segment_start": iso(j.segment.start_at), "state": j.state, "attempts": j.attempts,
            "leased_by": j.leased_by, "lease_expires_at": iso(j.lease_expires_at),
            "created_at": iso(j.created_at), "finished_at": iso(j.finished_at),
            "error": j.error, "model": j.model, "processing_ms": j.processing_ms,
        } for j in s.scalars(q)]
    return {"stats": queue.stats(), "workers": workers, "jobs": jobs}


def feedback_export() -> list[dict[str, Any]]:
    """Labelled detections, for building a fine-tuning dataset."""
    with session_scope() as s:
        rows = s.scalars(select(Detection).where(Detection.feedback.is_not(None))
                         .order_by(Detection.ts)).all()
        return [{**detection_dict(d), "segment_path": d.segment.path,
                 "segment_deleted": d.segment.deleted_at is not None} for d in rows]


def latest_segment_path(camera_id: str) -> str | None:
    with session_scope() as s:
        return s.scalar(select(Segment.path).where(
            Segment.camera_id == camera_id, Segment.deleted_at.is_(None),
            Segment.status != SegmentStatus.FAILED).order_by(Segment.start_at.desc()).limit(1))


def _redact_url(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    if "url" in source:
        source = {**source, "url": _redact_url(source["url"])}
    return source


def public_settings(settings: Settings) -> dict[str, Any]:
    """Config as shown in the UI, with secrets removed."""
    cfg = settings.model_dump(mode="json")
    cfg["worker_token"] = "***" if settings.worker_token else None
    for ch in cfg["notifications"]["channels"]:
        for secret in ("token", "password", "secret"):
            ch[secret] = "***" if ch.get(secret) else None
        if ch.get("type") == "apprise" and ch.get("url"):
            ch["url"] = ch["url"].split("://", 1)[0] + "://…"
    s3 = cfg["storage"]["s3"]
    for secret in ("access_key_id", "secret_access_key"):
        s3[secret] = "***" if s3[secret] else None
    for cam in cfg["cameras"]:
        if "url" in cam["source"]:
            cam["source"]["url"] = _redact_url(cam["source"]["url"])
    return cfg
