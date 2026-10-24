"""Read models shared by the JSON API and the HTML pages."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, exists, func, select

from .. import recording
from ..accounts import Principal
from ..config import Settings
from ..db import session_scope
from ..models import (
    Archive,
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
from .live import live_url

WORKER_ONLINE_SECONDS = 120


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def camera_state(camera_id: str) -> dict[str, Any]:
    with session_scope() as s:
        st = s.get(CameraState, camera_id)
        mask = s.get(MaskVersion, st.mask_version_id) if st and st.mask_version_id else None
        return {"private": bool(st and st.private), "rects": mask.rects if mask else [],
                "mask_version_id": mask.id if mask else None,
                "burn_masks": bool(st and st.burn_masks)}


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


def cameras(settings: Settings, live: dict[str, dict[str, Any]],
            p: Principal) -> list[dict[str, Any]]:
    """The cameras `p` may see, with the user's role on each."""
    out = []
    with session_scope() as s:
        for cam in settings.all_cameras(include_disabled=True):
            if not p.can(cam.id):
                continue
            last = s.scalars(select(Segment).where(Segment.camera_id == cam.id)
                             .order_by(Segment.start_at.desc()).limit(1)).first()
            path = live.get(cam.id, {})
            out.append({
                "id": cam.id, "name": cam.display_name, "source": cam.source.type,
                "source_config": _public_source(cam.source.model_dump()),
                "managed": cam.managed, "enabled": cam.enabled,
                "segment_seconds_override": cam.segment_seconds,
                "role": p.role(cam.id),
                "webrtc_url": live_url(cam.id, "main", "webrtc"),
                "hls_url": live_url(cam.id, "main", "hls"),
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
                **_recording(settings, cam),
                **camera_state(cam.id),
            })
    return out


def _recording(settings: Settings, cam) -> dict[str, Any]:
    profile, source = recording.camera_recording_profile(settings, cam)
    return {"recording_profile": profile.id, "recording_profile_source": source,
            "processed": recording.processing(settings, cam) is not None}


def _low_urls(settings: Settings, camera_id: str) -> dict[str, str | None]:
    if not settings.mediamtx.low_data.enabled:
        return {"webrtc_low_url": None, "hls_low_url": None}
    return {"webrtc_low_url": live_url(camera_id, "low", "webrtc"),
            "hls_low_url": live_url(camera_id, "low", "hls")}


def _profiles_source(cam) -> str:
    """Where the camera's profile selection comes from: ui, config or default."""
    with session_scope() as s:
        st = s.get(CameraState, cam.id)
        if st is not None and st.profiles is not None:
            return "ui"
    return "config" if cam.profiles is not None else "default"


def _only(q, column, camera_ids: set[str] | None):
    """Restrict a query to the cameras a user may see (None = all)."""
    return q if camera_ids is None else q.where(column.in_(camera_ids))


def _feedback():
    """(wrong, correct) EXISTS clauses. Feedback is per detection: an event is "wrong" once
    one of its detections is marked wrong and none correct (a wrong box on a real visit does
    not hide the visit)."""
    return (exists().where(Detection.event_id == Event.id, Detection.feedback.is_(False)),
            exists().where(Detection.event_id == Event.id, Detection.feedback.is_(True)))


def overview(settings: Settings, camera_ids: set[str] | None = None) -> dict[str, Any]:
    now = utcnow()
    with session_scope() as s:
        seg_counts = dict(s.execute(_only(select(Segment.status, func.count())
                                          .where(Segment.deleted_at.is_(None)),
                                          Segment.camera_id, camera_ids)
                                    .group_by(Segment.status)).all())
        stored = s.scalar(_only(select(func.coalesce(func.sum(Segment.size_bytes), 0))
                                .where(Segment.deleted_at.is_(None)), Segment.camera_id,
                                camera_ids))
        # Like the Status page's event list: events marked wrong are left out.
        wrong, correct = _feedback()
        events_24h = s.scalar(_only(select(func.count()).select_from(Event)
                                    .where(Event.last_ts >= now - timedelta(hours=24),
                                           ~(wrong & ~correct)),
                                    Event.camera_id, camera_ids))
        events_total = s.scalar(_only(select(func.count()).select_from(Event),
                                      Event.camera_id, camera_ids))
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


def segment_jobs(settings: Settings, segment_id: int,
                 raw: bool = False) -> list[dict[str, Any]]:
    """One row per detection run (job) of a segment, with its detection counts, so "ran
    and found nothing" can be told apart from "never ran"."""
    with session_scope() as s:
        counts = {job_id: (total, targets) for job_id, total, targets in s.execute(
            select(Detection.job_id, func.count(),
                   func.coalesce(func.sum(case((queue.counts_as_match(), 1), else_=0)), 0))
            .where(Detection.segment_id == segment_id).group_by(Detection.job_id))}
        out = []
        for j in s.scalars(select(Job).where(Job.segment_id == segment_id).order_by(Job.id)):
            profile = settings.profile(j.profile_id)
            det = profile.detector(j.detector_id) if profile else None
            total, targets = counts.get(j.id, (0, 0))
            out.append({
                "id": j.id, "profile_id": j.profile_id,
                "profile_name": profile.display_name if profile else j.profile_id,
                "detector_id": j.detector_id,
                "detector_name": det.display_name if det else j.detector_id,
                "state": j.state, "model": j.model, "worker_id": j.leased_by,
                "attempts": j.attempts, "error": j.error, "processing_ms": j.processing_ms,
                "finished_at": iso(j.finished_at), "detections": total,
                "targets": int(targets),
                # Admins only: what the worker posted (debugging).
                **({"raw_result": j.raw_result} if raw else {}),
            })
        return out


def segment_dict(g: Segment) -> dict[str, Any]:
    return {
        "id": g.id, "camera_id": g.camera_id, "start_at": iso(g.start_at),
        "duration_s": round(g.duration_s, 1), "size_bytes": g.size_bytes, "status": g.status,
        "error": g.error, "is_night": g.is_night, "saturation_avg": g.saturation_avg,
        "sun_elevation": g.sun_elevation, "has_target": g.has_target,
        "max_confidence": g.max_confidence, "pinned": g.pinned,
        "deleted": g.deleted_at is not None, "mask_version_id": g.mask_version_id,
        "remote": g.remote_key is not None,
        # Joined into an hourly file by compaction (served cut back out of it).
        "archived": g.archive_id is not None, "archive_id": g.archive_id,
        "thumb_url": f"/api/segments/{g.id}/thumb" if g.thumb_path else None,
        "download_url": None if g.deleted_at and not g.remote_key
        else f"/api/segments/{g.id}/download",
    }


def segments(camera_id: str | None = None, day: str | None = None, only_targets: bool = False,
             include_deleted: bool = False, limit: int = 200,
             camera_ids: set[str] | None = None,
             status: str | None = None) -> list[dict[str, Any]]:
    q = _only(select(Segment), Segment.camera_id, camera_ids) \
        .order_by(Segment.start_at.desc()).limit(limit)
    if camera_id:
        q = q.where(Segment.camera_id == camera_id)
    if day:
        start = datetime.fromisoformat(day)
        q = q.where(Segment.start_at >= start, Segment.start_at < start + timedelta(days=1))
    if only_targets:
        q = q.where(Segment.has_target.is_(True))
    if status:
        q = q.where(Segment.status == status)
    if not include_deleted:
        q = q.where(Segment.deleted_at.is_(None))
    with session_scope() as s:
        return [segment_dict(g) for g in s.scalars(q)]


def archives(camera_id: str | None = None, day: str | None = None, limit: int = 200,
             camera_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Hourly files made by compaction, newest first, with their segment counts."""
    q = _only(select(Archive), Archive.camera_id, camera_ids) \
        .order_by(Archive.start_at.desc()).limit(limit)
    if camera_id:
        q = q.where(Archive.camera_id == camera_id)
    if day:
        start = datetime.fromisoformat(day)
        q = q.where(Archive.start_at >= start, Archive.start_at < start + timedelta(days=1))
    with session_scope() as s:
        rows = s.scalars(q).all()
        counts = dict(s.execute(
            select(Segment.archive_id, func.count()).where(
                Segment.archive_id.in_([a.id for a in rows])).group_by(Segment.archive_id)).all())
        first = dict(s.execute(
            select(Segment.archive_id, func.min(Segment.id)).where(
                Segment.archive_id.in_([a.id for a in rows])).group_by(Segment.archive_id)).all())
        thumbs = dict(s.execute(
            select(Segment.archive_id, func.min(Segment.id)).where(
                Segment.archive_id.in_([a.id for a in rows]), Segment.thumb_path.is_not(None))
            .group_by(Segment.archive_id)).all())
        targets = dict(s.execute(
            select(Segment.archive_id, func.count()).where(
                Segment.archive_id.in_([a.id for a in rows]), Segment.has_target.is_(True))
            .group_by(Segment.archive_id)).all())
        return [{
            "id": a.id, "camera_id": a.camera_id, "start_at": iso(a.start_at),
            "duration_s": round(a.duration_s, 1), "size_bytes": a.size_bytes,
            "profile_id": a.profile_id, "encoder": a.encoder,
            "segments": counts.get(a.id, 0), "first_segment_id": first.get(a.id),
            "segments_with_match": targets.get(a.id, 0),
            "thumb_url": f"/api/segments/{thumbs[a.id]}/thumb" if a.id in thumbs else None,
            "deleted": a.deleted_at is not None, "remote": a.remote_key is not None,
            "play_url": None if a.deleted_at and not a.remote_key
            else f"/api/archives/{a.id}/play",
            "download_url": None if a.deleted_at and not a.remote_key
            else f"/api/archives/{a.id}/download",
        } for a in rows]


def detection_dict(d: Detection) -> dict[str, Any]:
    return {
        "id": d.id, "segment_id": d.segment_id, "camera_id": d.camera_id, "ts": iso(d.ts),
        "profile_id": d.profile_id, "detector_id": d.detector_id, "event_id": d.event_id,
        "offset_ms": d.offset_ms, "label": d.label, "confidence": round(d.confidence, 3),
        "bbox": d.bbox, "is_target": d.is_target, "model": d.model, "worker_id": d.worker_id,
        "feedback": d.feedback, "is_night": d.segment.is_night if d.segment else None,
        # Detector specifics, e.g. SpeciesNet's box label and top guesses.
        "extra": d.extra or {},
    }


def detections(camera_id: str | None = None, label: str | None = None,
               only_targets: bool = False, min_confidence: float = 0.0,
               limit: int = 200, segment_id: int | None = None,
               profile_id: str | None = None,
               camera_ids: set[str] | None = None,
               detector_id: str | None = None,
               event_id: int | None = None) -> list[dict[str, Any]]:
    q = _only(select(Detection), Detection.camera_id, camera_ids) \
        .where(Detection.confidence >= min_confidence) \
        .order_by(Detection.ts.desc()).limit(limit)
    if segment_id is not None:
        q = q.where(Detection.segment_id == segment_id)
    if camera_id:
        q = q.where(Detection.camera_id == camera_id)
    if label:
        q = q.where(func.lower(Detection.label) == label.lower())
    if profile_id:
        q = q.where(Detection.profile_id == profile_id)
    if detector_id:
        q = q.where(Detection.detector_id == detector_id)
    if event_id is not None:
        q = q.where(Detection.event_id == event_id)
    if only_targets:
        q = q.where(Detection.is_target.is_(True))
    with session_scope() as s:
        return [detection_dict(d) for d in s.scalars(q)]


def set_feedback(settings: Settings, detection_id: int, correct: bool | None) -> bool:
    with session_scope() as s:
        d = s.get(Detection, detection_id)
        if d is None:
            return False
        d.feedback = correct
        d.feedback_at = utcnow() if correct is not None else None
        # A detection marked wrong is no match: the recording's "with a match" (and the
        # archive's count) follow.
        seg = s.get(Segment, d.segment_id)
        if seg is not None:
            queue.refresh_match(settings, seg)
        return True


def events(camera_id: str | None = None, limit: int = 100,
           profile_id: str | None = None, label: str | None = None,
           camera_ids: set[str] | None = None,
           min_agreement: int = 1, hide_wrong: bool = False) -> list[dict[str, Any]]:
    wrong, correct = _feedback()
    q = _only(select(Event, wrong, correct), Event.camera_id, camera_ids) \
        .order_by(Event.first_ts.desc()).limit(limit)
    if hide_wrong:
        q = q.where(~(wrong & ~correct))
    if camera_id:
        q = q.where(Event.camera_id == camera_id)
    if profile_id:
        q = q.where(Event.profile_id == profile_id)
    if label:
        q = q.where(func.lower(Event.label) == label.lower())
    if min_agreement > 1:
        q = q.where(func.coalesce(Event.agreement, 1) >= min_agreement)
    with session_scope() as s:
        return [{
            "id": e.id, "camera_id": e.camera_id, "profile_id": e.profile_id, "label": e.label,
            "best_detection_id": e.best_detection_id,
            "feedback": "correct" if is_correct else "wrong" if is_wrong else None,
            "first_ts": iso(e.first_ts),
            "last_ts": iso(e.last_ts), "max_confidence": round(e.max_confidence, 3),
            "detection_count": e.detection_count, "is_night": e.is_night,
            "detectors": e.detectors or [], "agreement": e.agreement or 1,
            "confirmed": e.confirmed is not False,
            "clip_url": f"/api/events/{e.id}/clip" if e.clip_path or e.clip_remote_key else None,
            "thumb_url": f"/api/events/{e.id}/thumb" if e.thumb_path or e.thumb_remote_key
            else None,
            "remote": e.clip_remote_key is not None,
            "clip_duration_s": e.clip_duration_s, "building": e.clip_dirty,
            "clip_error": e.clip_error,
        } for e, is_wrong, is_correct in s.execute(q)]


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
            "profile_id": j.profile_id, "detector_id": j.detector_id,
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


def latest_segment_id(camera_id: str) -> int | None:
    with session_scope() as s:
        return s.scalar(select(Segment.id).where(
            Segment.camera_id == camera_id, Segment.deleted_at.is_(None),
            Segment.status != SegmentStatus.FAILED).order_by(Segment.start_at.desc()).limit(1))


def _redact_url(url: str) -> str:
    """Without the login. Passwords may contain unencoded @ and /: the login runs to the
    last @ before the first / that follows an @ (the start of the path)."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    first_at = rest.find("@")
    if first_at < 0:
        return url
    slash = rest.find("/", first_at)
    authority, path = (rest, "") if slash < 0 else (rest[:slash], rest[slash:])
    return f"{scheme}://***@{authority.rsplit('@', 1)[1]}{path}"


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    if "url" in source:
        source = {**source, "url": _redact_url(source["url"])}
    return source


def public_settings(settings: Settings) -> dict[str, Any]:
    """Config as shown in the UI, with secrets removed."""
    cfg = settings.model_dump(mode="json")
    cfg["worker_token"] = "***" if settings.worker_token else None
    cfg["mediamtx"]["password"] = "***" if settings.mediamtx.password else None
    for ch in cfg["notifications"]["channels"]:
        for secret in ("token", "password", "secret"):
            ch[secret] = "***" if ch.get(secret) else None
        if ch.get("headers"):  # e.g. Authorization
            ch["headers"] = {k: "***" for k in ch["headers"]}
        if ch.get("url"):
            ch["url"] = _redact_url(ch["url"])
        if ch.get("type") == "apprise" and ch.get("url"):
            ch["url"] = ch["url"].split("://", 1)[0] + "://…"
    s3 = cfg["storage"]["s3"]
    for secret in ("access_key_id", "secret_access_key"):
        s3[secret] = "***" if s3[secret] else None
    for prov in cfg["auth"]["oauth"]:
        prov["client_secret"] = "***"
    for cam in cfg["cameras"]:
        if "url" in cam["source"]:
            cam["source"]["url"] = _redact_url(cam["source"]["url"])
    return cfg
