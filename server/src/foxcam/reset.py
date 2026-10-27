"""Partial resets for development (`foxcam reset <what>`, `mise run reset:<what>`).

Each scope removes one kind of data and keeps the rest. From a shell, stop the server
first: the pipeline would otherwise keep writing while rows are deleted. Settings → Danger
zone runs the same scopes in the server, with its background work paused
(``runner.gate``). Objects already uploaded to S3 are not touched.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

from sqlalchemy import delete, select, update

from .config import Settings
from .db import session_scope
from .models import (
    ApiKey,
    Archive,
    CameraAccess,
    CameraState,
    Detection,
    DetectionProfile,
    Event,
    FootageSkip,
    Invite,
    Job,
    JobState,
    ManagedCamera,
    MaskVersion,
    OAuthIdentity,
    Segment,
    SegmentStatus,
    User,
    UserSession,
    Worker,
    WorkerToken,
    event_segments,
)


def _clear_dir(path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _drop_events(settings: Settings) -> int:
    with session_scope() as s:
        n = s.query(Event).count()
        s.execute(delete(event_segments))
        s.execute(delete(Event))
        s.execute(update(Segment).values(pinned=False))
    from . import sprites  # deferred, like in footage()

    _clear_dir(settings.clips_dir)
    sprites.remove_kind(settings, "clip")
    return n


def recordings(settings: Settings) -> str:
    """All footage and everything derived from it (segments, jobs, detections, events)."""
    with session_scope() as s:
        n = s.query(Segment).count()
        s.execute(delete(event_segments))
        for model in (Event, Detection, Job, Segment, Archive, FootageSkip):
            s.execute(delete(model))
    for d in (settings.recordings_dir, settings.derived_dir, settings.thumbs_dir,
              settings.clips_dir, settings.sprites_dir):
        _clear_dir(d)
    return f"{n} recordings removed (cameras, masks, profiles and workers kept)"


def footage(settings: Settings) -> str:
    """The video files (recordings, archives, event clips), like retention does it for all of
    them at once: recordings stay listed as deleted, and detections, events, their marks and
    the reports stay. Recordings not analysed yet are given up."""
    from . import sprites
    from .models import utcnow
    from .pipeline.retention import _remove_files

    with session_scope() as s:
        n = 0
        for seg in s.scalars(select(Segment).where(Segment.deleted_at.is_(None))):
            _remove_files(settings, seg)
            if seg.status in (SegmentStatus.NEW, SegmentStatus.QUEUED):
                seg.status = SegmentStatus.DONE  # nothing left to analyse
            seg.pinned = False
            n += 1
        s.execute(delete(Job).where(
            Job.state.in_([JobState.PENDING, JobState.FAILED, JobState.LEASED])))
        s.execute(update(Worker).values(current_job_id=None))
        for arc in s.scalars(select(Archive).where(Archive.deleted_at.is_(None))):
            (settings.recordings_dir / arc.path).unlink(missing_ok=True)
            sprites.remove(settings, "archive", arc.id)
            arc.deleted_at = utcnow()
        for ev in s.scalars(select(Event).where(Event.clip_path.is_not(None))):
            (settings.data_dir / ev.clip_path).unlink(missing_ok=True)
            sprites.remove(settings, "clip", ev.id)
            ev.clip_path = None
        s.execute(update(Event).values(clip_dirty=False))  # nothing to cut them from
    return f"footage of {n} recordings removed (detections, events and marks kept)"


def events(settings: Settings) -> str:
    """Events and clips; the running server rebuilds them from the detections."""
    n = _drop_events(settings)
    with session_scope() as s:
        s.execute(update(Detection).values(event_id=None))
    return f"{n} events removed; they are rebuilt from the detections on the next start"


def detections(settings: Settings) -> str:
    """Detections, events and jobs; recordings still on disk are analysed again."""
    n_events = _drop_events(settings)
    with session_scope() as s:
        n_det = s.query(Detection).count()
        s.execute(delete(Detection))
        s.execute(delete(Job))
        derived = [p for p in s.scalars(select(Segment.derived_path)) if p]
        requeued = s.execute(
            update(Segment).where(Segment.deleted_at.is_(None),
                                  Segment.status != SegmentStatus.NEW)
            .values(status=SegmentStatus.NEW, has_target=False, max_confidence=None,
                    derived_path=None, derived_fps=None, error=None)).rowcount
    for rel in derived:
        (settings.data_dir / rel).unlink(missing_ok=True)
    return (f"{n_det} detections and {n_events} events removed; {requeued} recordings "
            "will be analysed again with the current profiles")


def queue(settings: Settings) -> str:
    """Waiting and running jobs (the backlog); their recordings count as analysed."""
    from .pipeline.queue import settle_segment

    with session_scope() as s:
        jobs = s.scalars(select(Job).where(
            Job.state.in_([JobState.PENDING, JobState.FAILED, JobState.LEASED]))).all()
        segments = {j.segment for j in jobs}
        for j in jobs:
            s.delete(j)
        s.execute(update(Worker).values(current_job_id=None))
        s.flush()
        for seg in segments:
            settle_segment(settings, seg)
    return f"{len(jobs)} waiting jobs removed"


def workers(settings: Settings) -> str:
    """Known workers and all access tokens (workers need a new token afterwards)."""
    with session_scope() as s:
        n_w = s.query(Worker).count()
        n_t = s.query(WorkerToken).count()
        s.execute(delete(Worker))
        s.execute(delete(WorkerToken))
    return f"{n_w} workers and {n_t} tokens removed; workers need a new token"


def cameras(settings: Settings) -> str:
    """Cameras added in the UI, plus every camera's masks, private flag and profile choice."""
    with session_scope() as s:
        n = s.query(ManagedCamera).count()
        s.execute(delete(ManagedCamera))
        s.execute(delete(CameraState))  # mask versions stay: old segments reference them
        s.execute(delete(CameraAccess))
    return (f"{n} UI cameras removed; masks, private flags, profile choices and shares "
            "reset")


def profiles(settings: Settings) -> str:
    """Profiles added in the UI and per-camera profile choices."""
    from .profiles import cancel_jobs_without_profile

    with session_scope() as s:
        n = s.query(DetectionProfile).count()
        s.execute(delete(DetectionProfile))
        s.execute(update(CameraState).values(profiles=None))
    cancelled = cancel_jobs_without_profile(settings)
    return f"{n} UI profiles removed ({cancelled} of their jobs cancelled)"


def users(settings: Settings) -> str:
    """All accounts, sessions, API keys, shares and invites (the next start asks for a new
    admin)."""
    with session_scope() as s:
        n = s.query(User).count()
        for model in (UserSession, ApiKey, OAuthIdentity, CameraAccess, Invite, User):
            s.execute(delete(model))
    return f"{n} accounts removed; create the first admin again with the setup code"


def tapo(settings: Settings) -> str:
    """The saved Tapo cloud session."""
    from .tapo_account import logout

    logout(settings)
    return "Tapo session removed"


SCOPES: dict[str, Callable[[Settings], str]] = {
    "recordings": recordings, "footage": footage, "events": events, "detections": detections, "queue": queue,
    "workers": workers, "cameras": cameras, "profiles": profiles, "users": users,
    "tapo": tapo,
}


# --- cameras -------------------------------------------------------------------------

TEST_SOURCES = ("file", "test")  # looping sample videos and test patterns


def camera_ids_with_data() -> set[str]:
    with session_scope() as s:
        return set(s.scalars(select(Segment.camera_id).distinct()))


def purge_camera(settings: Settings, camera_id: str) -> dict[str, int]:
    """Delete a camera's recordings, events, detections and settings. Removes the camera
    itself when it was added in the UI (config.yaml cameras must be removed there)."""
    from . import sprites  # deferred, like in footage()

    with session_scope() as s:
        seg_ids = list(s.scalars(select(Segment.id).where(Segment.camera_id == camera_id)))
        derived = [p for p in s.scalars(select(Segment.derived_path)
                                         .where(Segment.camera_id == camera_id)) if p]
        thumbs = [p for p in s.scalars(select(Segment.thumb_path)
                                        .where(Segment.camera_id == camera_id)) if p]
        events = s.scalars(select(Event).where(Event.camera_id == camera_id)).all()
        clips = [p for ev in events for p in (ev.clip_path, ev.thumb_path) if p]
        previews = [*(("segment", i) for i in seg_ids), *(("clip", ev.id) for ev in events),
                    *(("archive", i) for i in s.scalars(
                        select(Archive.id).where(Archive.camera_id == camera_id)))]
        n_events = len(events)
        s.execute(delete(event_segments).where(event_segments.c.segment_id.in_(seg_ids)))
        for ev in events:
            s.delete(ev)
        s.execute(delete(Detection).where(Detection.camera_id == camera_id))
        s.execute(delete(Job).where(Job.segment_id.in_(seg_ids)))
        s.execute(delete(Segment).where(Segment.camera_id == camera_id))
        s.execute(delete(Archive).where(Archive.camera_id == camera_id))
        s.execute(delete(CameraState).where(CameraState.camera_id == camera_id))
        s.execute(delete(MaskVersion).where(MaskVersion.camera_id == camera_id))
        s.execute(delete(CameraAccess).where(CameraAccess.camera_id == camera_id))
        removed = s.execute(delete(ManagedCamera).where(ManagedCamera.id == camera_id)).rowcount
    for rel in [*derived, *thumbs, *clips]:
        (settings.data_dir / rel).unlink(missing_ok=True)
    for kind, item_id in previews:
        sprites.remove(settings, kind, item_id)
    for folder in (settings.recordings_dir / camera_id, settings.derived_dir / camera_id,
                   settings.thumbs_dir / camera_id, settings.clips_dir / camera_id):
        if folder.is_dir():
            shutil.rmtree(folder)
    return {"segments": len(seg_ids), "events": n_events, "camera_removed": removed}


def test_cameras(settings: Settings) -> tuple[list[str], list[str]]:
    """(ids added in the UI, ids defined in config.yaml) of cameras with a test source."""
    cams = [c for c in settings.all_cameras(include_disabled=True)
            if c.source.type in TEST_SOURCES]
    return ([c.id for c in cams if c.managed == "ui"],
            [c.id for c in cams if c.managed == "config"])


def orphaned_cameras(settings: Settings) -> list[str]:
    """Camera ids that still have recordings but are no longer defined anywhere."""
    known = {c.id for c in settings.all_cameras(include_disabled=True)}
    return sorted(camera_ids_with_data() - known)
