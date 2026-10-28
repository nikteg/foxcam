"""Read models shared by the JSON API and the HTML pages."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, exists, func, select, true
from sqlalchemy.orm import load_only, object_session, selectinload

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
    JobState,
    MaskVersion,
    Segment,
    SegmentStatus,
    Worker,
    utcnow,
)
from ..pipeline import queue, still
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
                "burn_masks": bool(st and st.burn_masks),
                # None = motion.sensitivity
                "motion_sensitivity": st.motion_sensitivity if st else None,
                "motion_rects": (st.motion_rects if st else None) or [],
                # Degrees; None = off (pipeline/daylight.py).
                "sun_limit": st.sun_limit if st else None,
                # None = location.latitude / longitude.
                "latitude": st.latitude if st else None,
                "longitude": st.longitude if st else None}


def save_sun(camera_id: str, limit: float | None, latitude: float | None,
             longitude: float | None) -> None:
    with session_scope() as s:
        st = s.get(CameraState, camera_id) or CameraState(camera_id=camera_id)
        s.add(st)
        st.sun_limit, st.latitude, st.longitude = limit, latitude, longitude


def save_motion(camera_id: str, sensitivity: str | None, rects: list[list[float]]) -> None:
    with session_scope() as s:
        st = s.get(CameraState, camera_id) or CameraState(camera_id=camera_id)
        s.add(st)
        st.motion_sensitivity = sensitivity
        st.motion_rects = rects


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


def _favorite():
    """An event is a favorite when one of its detections is."""
    return exists().where(Detection.event_id == Event.id, Detection.favorite.is_(True))


def overview(settings: Settings, camera_ids: set[str] | None = None) -> dict[str, Any]:
    now = utcnow()
    with session_scope() as s:
        by_status = s.execute(_only(select(Segment.status, func.count(),
                                           func.coalesce(func.sum(Segment.size_bytes), 0))
                                    .where(Segment.deleted_at.is_(None)),
                                    Segment.camera_id, camera_ids)
                              .group_by(Segment.status)).all()
        seg_counts = {status: n for status, n, _ in by_status}
        stored = sum(size for _, _, size in by_status)
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
        # Largest share of the picture that moved (0 = none, None = not measured).
        "motion_score": g.motion_score,
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
             status: str | None = None, motion: str | None = None) -> list[dict[str, Any]]:
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
    if motion == "yes":
        q = q.where(Segment.motion_score > 0)
    elif motion == "no":
        q = q.where(Segment.motion_score == 0)
    if not include_deleted:
        q = q.where(Segment.deleted_at.is_(None))
    with session_scope() as s:
        return [segment_dict(g) for g in s.scalars(q)]


def archives(camera_id: str | None = None, day: str | None = None, limit: int = 200,
             camera_ids: set[str] | None = None,
             archive_id: int | None = None) -> list[dict[str, Any]]:
    """Hourly files made by compaction, newest first, with their segment counts."""
    q = _only(select(Archive), Archive.camera_id, camera_ids) \
        .order_by(Archive.start_at.desc()).limit(limit)
    if archive_id is not None:
        q = q.where(Archive.id == archive_id)
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
        moving = dict(s.execute(
            select(Segment.archive_id, func.count()).where(
                Segment.archive_id.in_([a.id for a in rows]), Segment.motion_score > 0)
            .group_by(Segment.archive_id)).all())
        return [{
            "id": a.id, "camera_id": a.camera_id, "start_at": iso(a.start_at),
            "duration_s": round(a.duration_s, 1), "size_bytes": a.size_bytes,
            "profile_id": a.profile_id, "encoder": a.encoder,
            "segments": counts.get(a.id, 0), "first_segment_id": first.get(a.id),
            "segments_with_match": targets.get(a.id, 0),
            # Recordings where something moved (motion detection).
            "segments_with_motion": moving.get(a.id, 0),
            "thumb_url": f"/api/segments/{thumbs[a.id]}/thumb" if a.id in thumbs else None,
            "deleted": a.deleted_at is not None, "remote": a.remote_key is not None,
            "play_url": None if a.deleted_at and not a.remote_key
            else f"/api/archives/{a.id}/play",
            "download_url": None if a.deleted_at and not a.remote_key
            else f"/api/archives/{a.id}/download",
        } for a in rows]


def _moving(settings: Settings) -> Callable[[Detection], bool | None]:
    """Whether a detection's box had motion at that moment, within ``motion.window_seconds``
    (None: not measured), the same way standing still is decided (pipeline/still.py). Each
    camera's motion history is loaded once for a list."""
    window = settings.motion.window_seconds
    cameras: dict[str, still.Frames] = {}

    def moving(d: Detection) -> bool | None:
        seg = d.segment
        if seg is None or seg.motion_score is None:
            return None
        frames = cameras.setdefault(d.camera_id, still.Frames(object_session(d), d.camera_id))
        return not frames.still(d.ts, still.bbox_of(d), 0, window)

    return moving


def detection_dict(d: Detection, moving: bool | None, match: bool) -> dict[str, Any]:
    return {
        "id": d.id, "segment_id": d.segment_id, "camera_id": d.camera_id, "ts": iso(d.ts),
        "profile_id": d.profile_id, "detector_id": d.detector_id, "event_id": d.event_id,
        "offset_ms": d.offset_ms, "label": d.label, "confidence": round(d.confidence, 3),
        "bbox": rect(d.bbox), "is_target": d.is_target, "model": d.model, "worker_id": d.worker_id,
        "feedback": d.feedback, "favorite": bool(d.favorite),
        "is_night": d.segment.is_night if d.segment else None,
        # The recording's picture size, which the frame has too: the web app keeps room
        # for the frame before it has loaded.
        "width": d.segment.width if d.segment else None,
        "height": d.segment.height if d.segment else None,
        # Standing still for the profile's still_minutes: no match.
        "still": bool(d.still),
        # Seen while the sun was below the camera's limit: no match.
        "below_sun_limit": bool(d.below_sun_limit),
        # The box overlapped motion at that moment (None: not measured).
        "moving": moving,
        # A target that counts (queue.counts_as_match): not still, not wrong, nor its event.
        "match": bool(match),
        # Detector specifics, e.g. SpeciesNet's box label and top guesses.
        "extra": d.extra or {},
    }


def detections(settings: Settings, camera_id: str | None = None, label: str | None = None,
               only_targets: bool = False, min_confidence: float = 0.0,
               limit: int = 200, segment_id: int | None = None,
               profile_id: str | None = None,
               camera_ids: set[str] | None = None,
               detector_id: str | None = None,
               event_id: int | None = None,
               hide_still: bool = False,
               only_favorites: bool = False,
               since: datetime | None = None, until: datetime | None = None,
               light: str | None = None,
               feedback: str | None = None,
               detection_id: int | None = None) -> list[dict[str, Any]]:
    # The recordings' few columns used here, in one query (not their motion data).
    q = _only(select(Detection, queue.counts_as_match()), Detection.camera_id, camera_ids) \
        .options(selectinload(Detection.segment).options(
            load_only(Segment.is_night, Segment.motion_score, Segment.width,
                      Segment.height))) \
        .where(Detection.confidence >= min_confidence) \
        .order_by(Detection.ts.desc()).limit(limit)
    if segment_id is not None:
        q = q.where(Detection.segment_id == segment_id)
    if detection_id is not None:
        q = q.where(Detection.id == detection_id)
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
    if hide_still:
        q = q.where(Detection.still.is_not(True))
    if only_favorites:
        q = q.where(Detection.favorite.is_(True))
    if since:
        q = q.where(Detection.ts >= since)
    if until:
        q = q.where(Detection.ts < until)
    if light:
        q = q.where(Detection.segment.has(Segment.is_night.is_(light == "night")))
    if feedback == "correct":
        q = q.where(Detection.feedback.is_(True))
    elif feedback == "wrong":
        q = q.where(Detection.feedback.is_(False))
    elif feedback == "none":
        q = q.where(Detection.feedback.is_(None))
    moving = _moving(settings)
    with session_scope() as s:
        return [detection_dict(d, moving(d), match) for d, match in s.execute(q)]


def set_feedback(settings: Settings, detection_id: int, correct: bool | None) -> bool:
    return set_detections(settings, [detection_id], feedback=correct) == 1


_UNSET: Any = object()


def set_detections(settings: Settings, ids: list[int], feedback: bool | None = _UNSET,
                   favorite: bool | None = None) -> int:
    """Feedback (None clears it) and/or the favorite star for detections. Returns how many
    were found."""
    with session_scope() as s:
        dets = s.scalars(select(Detection).where(Detection.id.in_(ids))).all()
        for d in dets:
            if favorite is not None:
                d.favorite = favorite or None
            if feedback is not _UNSET:
                d.feedback = feedback
                d.feedback_at = utcnow() if feedback is not None else None
        if feedback is not _UNSET:
            _refresh_matches(settings, s, dets)
        return len(dets)


def _refresh_matches(settings: Settings, s, dets: list[Detection]) -> None:
    """A detection marked wrong is no match, nor are the others of an event marked wrong:
    the recordings' "with a match" (and the archives' counts) follow."""
    for event_id in {d.event_id for d in dets if d.event_id is not None}:
        queue.refresh_event_matches(settings, s, event_id)
    seg_ids = {d.segment_id for d in dets if d.event_id is None}
    for seg in s.scalars(select(Segment).where(Segment.id.in_(seg_ids))):
        queue.refresh_match(settings, seg)


def set_events(settings: Settings, ids: list[int], feedback: bool | None = _UNSET,
               favorite: bool | None = None) -> int:
    """Feedback and/or the star for events, through their detections (where both live):
    the best detection gets the mark, like in the event's drawer. Marking an event wrong
    also turns its detections marked correct to wrong (else it would still count), and
    taking the star off clears it from all of them. Returns how many were found."""
    with session_scope() as s:
        events = s.scalars(select(Event).where(Event.id.in_(ids))).all()
        for ev in events:
            dets = s.scalars(select(Detection).where(Detection.event_id == ev.id)).all()
            best = next((d for d in dets if d.id == ev.best_detection_id),
                        max(dets, key=lambda d: d.confidence, default=None))
            if best is None:
                continue
            if favorite is True:
                best.favorite = True
            elif favorite is False:
                for d in dets:
                    d.favorite = None
            if feedback is not _UNSET:
                marked = [best] + [d for d in dets if feedback is False and d.feedback is True]
                for d in marked:
                    d.feedback = feedback
                    d.feedback_at = utcnow() if feedback is not None else None
                _refresh_matches(settings, s, [best])
        return len(events)


def events(camera_id: str | None = None, limit: int = 100,
           profile_id: str | None = None, label: str | None = None,
           camera_ids: set[str] | None = None,
           min_agreement: int = 1, hide_wrong: bool = False,
           only_favorites: bool = False,
           since: datetime | None = None, until: datetime | None = None,
           light: str | None = None,
           feedback: str | None = None,
           event_id: int | None = None) -> list[dict[str, Any]]:
    wrong, correct = _feedback()
    favorite = _favorite()
    q = _only(select(Event, wrong, correct, favorite), Event.camera_id, camera_ids) \
        .order_by(Event.first_ts.desc()).limit(limit)
    if event_id is not None:
        q = q.where(Event.id == event_id)
    if hide_wrong:
        q = q.where(~(wrong & ~correct))
    if only_favorites:
        q = q.where(favorite)
    if camera_id:
        q = q.where(Event.camera_id == camera_id)
    if profile_id:
        q = q.where(Event.profile_id == profile_id)
    if label:
        q = q.where(func.lower(Event.label) == label.lower())
    if min_agreement > 1:
        q = q.where(func.coalesce(Event.agreement, 1) >= min_agreement)
    # By when it started, like the list shows it.
    if since:
        q = q.where(Event.first_ts >= since)
    if until:
        q = q.where(Event.first_ts < until)
    if light:
        q = q.where(Event.is_night.is_(light == "night"))
    # Like the event's own mark: correct wins over wrong.
    if feedback == "correct":
        q = q.where(correct)
    elif feedback == "wrong":
        q = q.where(wrong & ~correct)
    elif feedback == "none":
        q = q.where(~wrong & ~correct)
    with session_scope() as s:
        return [{
            "id": e.id, "camera_id": e.camera_id, "profile_id": e.profile_id, "label": e.label,
            "best_detection_id": e.best_detection_id,
            "feedback": "correct" if is_correct else "wrong" if is_wrong else None,
            "favorite": bool(is_favorite),
            "first_ts": iso(e.first_ts),
            "last_ts": iso(e.last_ts), "max_confidence": round(e.max_confidence, 3),
            "detection_count": e.detection_count, "is_night": e.is_night,
            "detectors": e.detectors or [], "agreement": e.agreement or 1,
            "confirmed": e.confirmed is not False,
            "clip_url": f"/api/events/{e.id}/clip" if e.clip_path or e.clip_remote_key else None,
            "thumb_url": f"/api/events/{e.id}/thumb" if e.thumb_path or e.thumb_remote_key
            else None,
            # Drawn over the thumbnail (None: none, or burned into an older one).
            "thumb_box": rect(e.thumb_box),
            "remote": e.clip_remote_key is not None,
            "clip_duration_s": e.clip_duration_s, "building": e.clip_dirty,
            "clip_error": e.clip_error,
        } for e, is_wrong, is_correct, is_favorite in s.execute(q)]


#: Decimals of the box coordinates (0..1) sent to the web app: under a pixel at 4K.
BOX_DECIMALS = 4


def rect(box: list[float] | None) -> list[float] | None:
    """A box ([x, y, w, h], 0..1) as sent to the web app: rounded (stored unrounded)."""
    return None if box is None else [round(v, BOX_DECIMALS) for v in box]


def motion_frames(frames: list[list[Any]] | None) -> list[list[Any]]:
    """Motion frames (``[ms, [box, …]]``) with their boxes rounded like ``rect``."""
    return [[at, [rect(b) for b in boxes]] for at, boxes in frames or []]


def _motion_in(parts: list[tuple[Segment, float]], length_s: float) -> list[list[Any]]:
    """Where things moved in a video made of recordings: each recording's motion frames,
    their times (ms) moved by where the recording starts in the video (``parts``: the
    recording and that offset in seconds, may be negative) and cut to its length."""
    frames = []
    for seg, offset_s in sorted(parts, key=lambda p: p[1]):
        for at, boxes in motion_frames(seg.motion_boxes):
            t = round(at + offset_s * 1000)
            if 0 <= t <= length_s * 1000:
                frames.append([t, boxes])
    return frames


def event_motion(event_id: int) -> list[list[Any]]:
    """Motion in an event's clip (it starts with the pre-roll, inside a recording)."""
    with session_scope() as s:
        ev = s.get(Event, event_id)
        if ev is None or ev.clip_start is None:
            return []
        return _motion_in([(g, (g.start_at - ev.clip_start).total_seconds())
                           for g in ev.segments], ev.clip_duration_s or 0)


def _detections_in(s, parts: list[tuple[Segment, float]], length_s: float,
                   where: Any) -> list[dict[str, Any]]:
    """The detections (``where``) of a video made of recordings, for drawing their boxes
    over the playback: times (ms) moved like ``_motion_in``'s, cut to its length."""
    offsets = {seg.id: offset_s for seg, offset_s in parts}
    if not offsets:
        return []
    q = select(Detection, queue.counts_as_match()).where(
        Detection.segment_id.in_(offsets), Detection.bbox_x.is_not(None), where)
    out = []
    for d, match in s.execute(q):
        at = round(d.offset_ms + offsets[d.segment_id] * 1000)
        if 0 <= at <= length_s * 1000:
            out.append({"id": d.id, "at_ms": at, "bbox": rect(d.bbox), "label": d.label,
                        "confidence": round(d.confidence, 3), "detector_id": d.detector_id,
                        "match": bool(match)})
    return sorted(out, key=lambda d: d["at_ms"])


def event_detections(event_id: int) -> list[dict[str, Any]]:
    """The event's detections, in its clip's time."""
    with session_scope() as s:
        ev = s.get(Event, event_id)
        if ev is None or ev.clip_start is None:
            return []
        return _detections_in(s, [(g, (g.start_at - ev.clip_start).total_seconds())
                                  for g in ev.segments], ev.clip_duration_s or 0,
                              Detection.event_id == event_id)


def archive_detections(archive_id: int) -> list[dict[str, Any]]:
    """What was found in an archive's recordings, in the archive's time: every detection,
    like a recording's player (the player draws the matches strong, the rest faint)."""
    with session_scope() as s:
        arc = s.get(Archive, archive_id)
        if arc is None:
            return []
        segs = s.scalars(select(Segment).where(Segment.archive_id == archive_id)).all()
        return _detections_in(s, [(g, g.archive_offset_s or 0.0) for g in segs],
                              arc.duration_s, true())


def archive_motion(archive_id: int) -> list[list[Any]]:
    """Motion in an archive (its recordings, each from its offset in the file)."""
    with session_scope() as s:
        arc = s.get(Archive, archive_id)
        if arc is None:
            return []
        segs = s.scalars(select(Segment).where(Segment.archive_id == archive_id)).all()
        return _motion_in([(g, g.archive_offset_s or 0.0) for g in segs], arc.duration_s)


def worker_summary() -> dict[str, int]:
    """Detection workers at a glance (Status page): connected lately, and busy with a job."""
    now = utcnow()
    with session_scope() as s:
        workers = s.scalars(select(Worker)).all()
        online = [w for w in workers
                  if (now - w.last_seen).total_seconds() < WORKER_ONLINE_SECONDS]
        return {"total": len(workers), "online": len(online),
                "busy": sum(1 for w in online if w.current_job_id is not None)}


def queue_view(limit: int = 100, state: str | None = None) -> dict[str, Any]:
    now = utcnow()
    q = select(Job).options(selectinload(Job.segment).options(
        load_only(Segment.camera_id, Segment.start_at, Segment.motion_score))) \
        .order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
    if state:
        q = q.where(Job.state == state)
    with session_scope() as s:
        workers = [{
            "id": w.id, "info": w.info, "tags": w.tags or [], "last_seen": iso(w.last_seen),
            "online": (now - w.last_seen).total_seconds() < WORKER_ONLINE_SECONDS,
            "current_job_id": w.current_job_id, "jobs_done": w.jobs_done,
            "jobs_failed": w.jobs_failed,
        } for w in s.scalars(select(Worker).order_by(Worker.last_seen.desc()))]
        page = s.scalars(q).all()
        counts = dict(s.execute(
            select(Detection.job_id, func.count()).where(
                Detection.job_id.in_([j.id for j in page])).group_by(Detection.job_id)).all())
        jobs = [{
            "id": j.id, "segment_id": j.segment_id, "camera_id": j.segment.camera_id,
            "profile_id": j.profile_id, "detector_id": j.detector_id,
            "segment_start": iso(j.segment.start_at), "state": j.state, "attempts": j.attempts,
            "leased_by": j.leased_by, "lease_expires_at": iso(j.lease_expires_at),
            "created_at": iso(j.created_at), "finished_at": iso(j.finished_at),
            "error": j.error, "model": j.model, "processing_ms": j.processing_ms,
            "motion_score": j.segment.motion_score,
            # Found by this job (None until it is done).
            "detections": counts.get(j.id, 0) if j.state == JobState.DONE else None,
        } for j in page]
    return {"stats": queue.stats(), "workers": workers, "jobs": jobs}


def feedback_export(settings: Settings) -> list[dict[str, Any]]:
    """Labelled detections, for building a fine-tuning dataset."""
    moving = _moving(settings)
    with session_scope() as s:
        rows = s.execute(select(Detection, queue.counts_as_match())
                         .where(Detection.feedback.is_not(None)).order_by(Detection.ts)).all()
        return [{**detection_dict(d, moving(d), match), "segment_path": d.segment.path,
                 "segment_deleted": d.segment.deleted_at is not None} for d, match in rows]


def latest_segment_id(camera_id: str) -> int | None:
    with session_scope() as s:
        return s.scalar(select(Segment.id).where(
            Segment.camera_id == camera_id, Segment.deleted_at.is_(None),
            Segment.status != SegmentStatus.FAILED).order_by(Segment.start_at.desc()).limit(1))


def _with_footage():
    """Segments whose footage can still be read (their own file or their archive)."""
    return ((Segment.status != SegmentStatus.FAILED)
            & (Segment.deleted_at.is_(None) | Segment.archive_id.is_not(None)))


def segment_at(camera_id: str, at: datetime) -> tuple[int, float] | None:
    """The recording that has ``at`` (naive UTC) and the second in it."""
    with session_scope() as s:
        seg = s.scalars(select(Segment).where(
            Segment.camera_id == camera_id, Segment.start_at <= at, _with_footage())
            .order_by(Segment.start_at.desc()).limit(1)).first()
        if seg is None:
            return None
        offset = (at - seg.start_at).total_seconds()
        return (seg.id, offset) if offset <= seg.duration_s else None


def snapshot_moments(camera_id: str, hours: int = 48, step_h: int = 2) -> list[dict[str, Any]]:
    """Moments of the last ``hours`` with footage, newest first, for choosing the mask
    editor's picture (day and night look different): one per ``step_h`` hours, as close
    to the whole hour as the footage allows (a camera that was off skips a few)."""
    now = utcnow()
    half = timedelta(hours=step_h / 2)
    since = now - timedelta(hours=hours)
    with session_scope() as s:
        segs = s.execute(select(Segment.start_at, Segment.duration_s, Segment.is_night,
                                Segment.id).where(
            Segment.camera_id == camera_id, _with_footage(), Segment.start_at >= since - half)
            .order_by(Segment.start_at)).all()
    out = []
    # Even whole hours: the same moments (and cached pictures) from one visit to the next.
    target = now.replace(minute=0, second=0, microsecond=0)
    target -= timedelta(hours=target.hour % step_h)
    while target > since:
        best: tuple[timedelta, datetime, bool | None, int] | None = None
        for start, duration, night, seg_id in segs:
            end = start + timedelta(seconds=max(duration - 1, 0))
            at = min(max(target, start), end)
            gap = abs(at - target)
            if gap < half and at <= now and (best is None or gap < best[0]):
                # Whole seconds (up: still in the recording) for short URLs.
                up = timedelta(seconds=1) if at.microsecond else timedelta()
                best = (gap, at.replace(microsecond=0) + up, night, seg_id)
        if best:
            out.append({"at": iso(best[1]), "is_night": best[2], "segment_id": best[3]})
        target -= timedelta(hours=step_h)
    return out


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
