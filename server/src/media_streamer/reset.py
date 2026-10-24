"""Partial resets for development (`media-streamer reset <what>`, `mise run reset:<what>`).

Each scope removes one kind of data and keeps the rest. Stop the server first: the
pipeline would otherwise keep writing while rows are deleted. Objects already uploaded to
S3 are not touched.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

from sqlalchemy import delete, select, update

from .config import Settings
from .db import session_scope
from .models import (
    CameraState,
    Detection,
    DetectionProfile,
    Event,
    Job,
    JobState,
    ManagedCamera,
    Segment,
    SegmentStatus,
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
    _clear_dir(settings.clips_dir)
    return n


def recordings(settings: Settings) -> str:
    """All footage and everything derived from it (segments, jobs, detections, events)."""
    with session_scope() as s:
        n = s.query(Segment).count()
        s.execute(delete(event_segments))
        for model in (Event, Detection, Job, Segment):
            s.execute(delete(model))
    for d in (settings.recordings_dir, settings.derived_dir, settings.thumbs_dir,
              settings.clips_dir):
        _clear_dir(d)
    return f"{n} recordings removed (cameras, masks, profiles and workers kept)"


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
    """Known workers and all access tokens (the worker API is open again)."""
    with session_scope() as s:
        n_w = s.query(Worker).count()
        n_t = s.query(WorkerToken).count()
        s.execute(delete(Worker))
        s.execute(delete(WorkerToken))
    return f"{n_w} workers and {n_t} tokens removed; worker access is open"


def cameras(settings: Settings) -> str:
    """Cameras added in the UI, plus every camera's masks, private flag and profile choice."""
    with session_scope() as s:
        n = s.query(ManagedCamera).count()
        s.execute(delete(ManagedCamera))
        s.execute(delete(CameraState))  # mask versions stay: old segments reference them
    return f"{n} UI cameras removed; masks, private flags and profile choices reset"


def profiles(settings: Settings) -> str:
    """Profiles added in the UI and per-camera profile choices."""
    from .profiles import cancel_jobs_without_profile

    with session_scope() as s:
        n = s.query(DetectionProfile).count()
        s.execute(delete(DetectionProfile))
        s.execute(update(CameraState).values(profiles=None))
    cancelled = cancel_jobs_without_profile(settings)
    return f"{n} UI profiles removed ({cancelled} of their jobs cancelled)"


def tapo(settings: Settings) -> str:
    """The saved Tapo cloud session."""
    settings.tapo_session_file.unlink(missing_ok=True)
    return "Tapo session removed"


SCOPES: dict[str, Callable[[Settings], str]] = {
    "recordings": recordings, "events": events, "detections": detections, "queue": queue,
    "workers": workers, "cameras": cameras, "profiles": profiles, "tapo": tapo,
}
