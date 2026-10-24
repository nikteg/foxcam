"""SQLite-backed job queue with leases.

Workers pull: ``lease`` atomically claims the next available job and returns a lease
token. The token must accompany heartbeats, input downloads and the final result.
Expired leases are reclaimed by ``reap``; a job is retried up to ``max_attempts``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update

from ..config import Settings
from ..db import session_scope
from ..models import Detection, Job, JobState, SegmentStatus, Worker, utcnow


class LeaseError(Exception):
    """Unknown job, wrong token or lease already expired."""


@dataclass
class DetectionIn:
    offset_ms: int
    label: str
    confidence: float
    bbox: list[float] | None = None
    extra: dict[str, Any] | None = None


def touch_worker(worker_id: str, info: dict[str, Any] | None = None,
                 current_job_id: int | None | type[...] = ...) -> None:
    with session_scope() as s:
        w = s.get(Worker, worker_id)
        if w is None:
            w = Worker(id=worker_id, info=info or {})
            s.add(w)
        w.last_seen = utcnow()
        if info:
            w.info = info
        if current_job_id is not ...:
            w.current_job_id = current_job_id


def lease(settings: Settings, worker_id: str, info: dict[str, Any] | None = None) -> Job | None:
    now = utcnow()
    token = secrets.token_urlsafe(24)
    expires = now + timedelta(seconds=settings.queue.lease_seconds)
    touch_worker(worker_id, info)
    with session_scope() as s:
        # Single UPDATE ... RETURNING => atomic claim even with several workers.
        # Newest first: fresh footage is the most interesting when catching up on a backlog.
        next_id = (
            select(Job.id)
            .where(Job.state.in_([JobState.PENDING, JobState.FAILED]), Job.available_at <= now)
            .order_by(Job.priority.desc(), Job.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        row = s.execute(
            update(Job).where(Job.id == next_id)
            .values(state=JobState.LEASED, lease_token=token, leased_by=worker_id, leased_at=now,
                    lease_expires_at=expires, attempts=Job.attempts + 1, error=None)
            .returning(Job.id)
        ).first()
        job_id = row[0] if row else None
    touch_worker(worker_id, current_job_id=job_id)
    if job_id is None:
        return None
    with session_scope() as s:
        job = s.get(Job, job_id)
        _ = job.segment  # load relationship before session closes
        return job


def _get_leased(s, job_id: int, token: str) -> Job:
    job = s.get(Job, job_id)
    if job is None or job.state != JobState.LEASED or job.lease_token != token:
        raise LeaseError("job is not leased with this token")
    if job.lease_expires_at and job.lease_expires_at < utcnow():
        raise LeaseError("lease expired")
    return job


def verify_lease(job_id: int, token: str) -> Job:
    with session_scope() as s:
        job = _get_leased(s, job_id, token)
        _ = job.segment
        return job


def heartbeat(settings: Settings, job_id: int, token: str) -> datetime:
    with session_scope() as s:
        job = _get_leased(s, job_id, token)
        job.lease_expires_at = utcnow() + timedelta(seconds=settings.queue.lease_seconds)
        worker_id = job.leased_by
        expires = job.lease_expires_at
    if worker_id:
        touch_worker(worker_id)
    return expires


def complete(settings: Settings, job_id: int, token: str, detections: list[DetectionIn],
             model: str | None, processing_ms: int | None) -> None:
    targets = {t.lower() for t in settings.detection.target_labels}
    min_conf = settings.detection.min_confidence
    with session_scope() as s:
        job = _get_leased(s, job_id, token)
        seg = job.segment
        max_conf = None
        has_target = False
        for d in detections:
            is_target = d.label.lower() in targets and d.confidence >= min_conf
            has_target |= is_target
            if is_target:
                max_conf = max(max_conf or 0.0, d.confidence)
            bbox = d.bbox or [None] * 4
            s.add(Detection(
                segment_id=seg.id, job_id=job.id, camera_id=seg.camera_id,
                ts=seg.start_at + timedelta(milliseconds=d.offset_ms), offset_ms=d.offset_ms,
                label=d.label, confidence=d.confidence,
                bbox_x=bbox[0], bbox_y=bbox[1], bbox_w=bbox[2], bbox_h=bbox[3],
                is_target=is_target, model=model, worker_id=job.leased_by, extra=d.extra,
            ))
        job.state = JobState.DONE
        job.finished_at = utcnow()
        job.model = model
        job.processing_ms = processing_ms
        job.lease_token = None
        seg.status = SegmentStatus.DONE
        seg.has_target = has_target
        seg.max_confidence = max_conf
        worker = s.get(Worker, job.leased_by) if job.leased_by else None
        if worker:
            worker.jobs_done += 1
            worker.current_job_id = None
            worker.last_seen = utcnow()
        derived = seg.derived_path
        if settings.retention.delete_derived_after_done:
            seg.derived_path = None
    if derived and settings.retention.delete_derived_after_done:
        (settings.data_dir / derived).unlink(missing_ok=True)


def fail(settings: Settings, job_id: int, token: str, error: str, retryable: bool = True) -> None:
    with session_scope() as s:
        job = _get_leased(s, job_id, token)
        _fail_job(settings, job, error, retryable)
        worker = s.get(Worker, job.leased_by) if job.leased_by else None
        if worker:
            worker.jobs_failed += 1
            worker.current_job_id = None


def _fail_job(settings: Settings, job: Job, error: str, retryable: bool) -> None:
    job.error = error[:1000]
    job.lease_token = None
    if retryable and job.attempts < settings.queue.max_attempts:
        job.state = JobState.FAILED
        job.available_at = utcnow() + timedelta(
            seconds=settings.queue.retry_backoff_seconds * job.attempts)
    else:
        job.state = JobState.DEAD
        job.finished_at = utcnow()
        job.segment.status = SegmentStatus.FAILED
        job.segment.error = f"job dead: {error}"[:500]


def reap(settings: Settings) -> int:
    """Return expired leases to the queue (or mark dead after too many attempts)."""
    now = utcnow()
    with session_scope() as s:
        expired = s.scalars(select(Job).where(Job.state == JobState.LEASED,
                                              Job.lease_expires_at < now)).all()
        for job in expired:
            _fail_job(settings, job, f"lease expired (worker {job.leased_by})", retryable=True)
            s.execute(update(Worker).where(Worker.current_job_id == job.id)
                      .values(current_job_id=None))
        return len(expired)


def requeue(job_id: int) -> None:
    """Manual retry from the UI."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None or job.state == JobState.LEASED:
            return
        job.state = JobState.PENDING
        job.attempts = 0
        job.available_at = utcnow()
        job.error = None
        job.finished_at = None
        if job.segment.status == SegmentStatus.FAILED and job.segment.derived_path:
            job.segment.status = SegmentStatus.QUEUED
            job.segment.error = None


def stats() -> dict[str, Any]:
    now = utcnow()
    with session_scope() as s:
        counts = dict(s.execute(select(Job.state, func.count()).group_by(Job.state)).all())
        oldest = s.scalar(select(func.min(Job.created_at))
                          .where(Job.state.in_([JobState.PENDING, JobState.FAILED])))
        hour_ago = now - timedelta(hours=1)
        done_last_hour = s.scalar(select(func.count()).select_from(Job)
                                  .where(Job.state == JobState.DONE, Job.finished_at >= hour_ago))
        avg_ms = s.scalar(select(func.avg(Job.processing_ms))
                          .where(Job.state == JobState.DONE, Job.finished_at >= hour_ago))
    return {
        "counts": {st: counts.get(st, 0) for st in JobState.ALL},
        "oldest_pending_age_s": (now - oldest).total_seconds() if oldest else None,
        "done_last_hour": done_last_hour or 0,
        "avg_processing_ms_last_hour": round(avg_ms) if avg_ms else None,
    }
