"""Worker protocol (see docs/WORKER_PROTOCOL.md).

lease -> download input -> heartbeat while working -> result | fail
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import Settings
from ..pipeline import queue
from ..worker_tokens import Identity
from .deps import require_worker, safe_path, settings_dep

router = APIRouter(prefix="/api/worker", tags=["worker"], dependencies=[Depends(require_worker)])


class LeaseRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    # Free-form: model name/version, host, GPU... shown in the queue tab.
    info: dict[str, Any] = {}


class SegmentOut(BaseModel):
    id: int
    camera_id: str
    start_at: datetime
    duration_s: float
    width: int | None
    height: int | None
    is_night: bool | None


class JobOut(BaseModel):
    job_id: int
    lease_token: str
    lease_expires_at: datetime
    attempt: int
    input_url: str
    input_fps: float
    segment: SegmentOut
    hints: dict[str, Any]


class TokenBody(BaseModel):
    lease_token: str


class DetectionBody(BaseModel):
    offset_ms: int = Field(ge=0, description="Milliseconds from the start of the segment")
    label: str
    confidence: float = Field(ge=0, le=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4,
                                     description="Normalized [x, y, w, h], 0..1")
    extra: dict[str, Any] | None = None


class ResultBody(TokenBody):
    model: str | None = None
    processing_ms: int | None = None
    detections: list[DetectionBody] = []


class FailBody(TokenBody):
    error: str
    retryable: bool = True


def _lease_error(e: queue.LeaseError) -> HTTPException:
    return HTTPException(409, str(e))


@router.post("/lease", response_model=JobOut, responses={204: {"description": "No work"}})
def lease(body: LeaseRequest, settings: Settings = Depends(settings_dep),
          identity: Identity = Depends(require_worker)):
    # A per-worker token decides who the worker is; the client's worker_id is ignored then.
    worker_id = identity.name or body.worker_id
    job = queue.lease(settings, worker_id, body.info)
    if job is None:
        return Response(status_code=204)
    seg = job.segment
    loc = settings.location
    return JobOut(
        job_id=job.id, lease_token=job.lease_token, lease_expires_at=job.lease_expires_at,
        attempt=job.attempts, input_url=f"/api/worker/jobs/{job.id}/input",
        input_fps=settings.processing.sample_fps,
        segment=SegmentOut(id=seg.id, camera_id=seg.camera_id, start_at=seg.start_at,
                           duration_s=seg.duration_s, width=seg.width, height=seg.height,
                           is_night=seg.is_night),
        hints={"target_labels": settings.detection.target_labels,
               "min_confidence": settings.detection.min_confidence,
               "country": loc.country, "latitude": loc.latitude, "longitude": loc.longitude},
    )


@router.get("/jobs/{job_id}/input")
def job_input(job_id: int, lease_token: str | None = Query(default=None),
              x_lease_token: str | None = Header(default=None),
              settings: Settings = Depends(settings_dep)) -> FileResponse:
    try:
        job = queue.verify_lease(job_id, lease_token or x_lease_token or "")
    except queue.LeaseError as e:
        raise _lease_error(e) from e
    path = safe_path(settings.data_dir, job.segment.derived_path)
    return FileResponse(path, media_type="video/mp4", filename=f"job-{job_id}.mp4")


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat(job_id: int, body: TokenBody,
              settings: Settings = Depends(settings_dep)) -> dict[str, datetime]:
    try:
        return {"lease_expires_at": queue.heartbeat(settings, job_id, body.lease_token)}
    except queue.LeaseError as e:
        raise _lease_error(e) from e


@router.post("/jobs/{job_id}/result")
def result(job_id: int, body: ResultBody,
           settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    dets = [queue.DetectionIn(**d.model_dump()) for d in body.detections]
    try:
        queue.complete(settings, job_id, body.lease_token, dets, body.model, body.processing_ms)
    except queue.LeaseError as e:
        raise _lease_error(e) from e
    return {"status": "ok"}


@router.post("/jobs/{job_id}/fail")
def fail(job_id: int, body: FailBody, settings: Settings = Depends(settings_dep)) -> dict:
    try:
        queue.fail(settings, job_id, body.lease_token, body.error, body.retryable)
    except queue.LeaseError as e:
        raise _lease_error(e) from e
    return {"status": "ok"}
