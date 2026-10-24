"""Worker protocol (see docs/WORKER_PROTOCOL.md).

lease -> download input -> heartbeat while working -> result | fail
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import Settings
from ..pipeline import queue
from ..profiles import normalize_tags
from ..worker_tokens import Identity
from .deps import require_worker, safe_path, settings_dep

router = APIRouter(prefix="/api/worker", tags=["worker"], dependencies=[Depends(require_worker)])


@router.get("/whoami")
def whoami(identity: Identity = Depends(require_worker)) -> dict[str, Any]:
    """Credential check without side effects (used by `media-worker doctor`)."""
    return {"authenticated": True, "token_name": identity.name, "token_tags": list(identity.tags)}


class LeaseRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    # Free-form: model name/version, host, GPU... shown in the queue tab.
    info: dict[str, Any] = {}
    # Capabilities, e.g. ["speciesnet", "gpu"]. Only jobs of detection profiles whose
    # `requires` tags are all present are handed out.
    tags: list[str] = Field(default=[], max_length=50)
    # What this worker can detect, if limited (e.g. fixed classes). null = anything: then
    # targets are taken from each job's profile (open-vocabulary models are prompted).
    labels: list[str] | None = Field(default=None, max_length=500)
    # Everything the detector can report (e.g. SpeciesNet's ~2,500 taxonomy names), for the
    # label pickers in the UI; not a filter. Sent now and then, not with every lease.
    vocabulary: list[Annotated[str, Field(max_length=120)]] | None = Field(
        default=None, max_length=20000)


class SegmentOut(BaseModel):
    id: int
    camera_id: str
    start_at: datetime
    duration_s: float
    width: int | None
    height: int | None
    is_night: bool | None


class ProfileOut(BaseModel):
    id: str
    name: str
    targets: list[str]
    # The detector's own threshold if it has one, else the profile's.
    min_confidence: float


class DetectorOut(BaseModel):
    id: str
    name: str


class JobOut(BaseModel):
    job_id: int
    lease_token: str
    lease_expires_at: datetime
    attempt: int
    input_url: str
    input_fps: float
    # Frames per second this profile wants analysed (≤ input_fps; subsample to it).
    sample_fps: float
    profile: ProfileOut
    # Which of the profile's detectors (models) this job is for.
    detector: DetectorOut
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
    tags = normalize_tags([*body.tags, *identity.tags])
    job = queue.lease(settings, worker_id, body.info, tags, body.labels, body.vocabulary)
    if job is None:
        return Response(status_code=204)
    seg = job.segment
    loc = settings.location
    profile = settings.profile(job.profile_id)
    input_fps = seg.derived_fps or settings.processing.sample_fps
    targets = profile.targets if profile else []
    min_conf = profile.min_confidence_for(job.detector_id) if profile else 0.5
    det = profile.detector(job.detector_id) if profile else None
    fps = profile.fps_for(job.detector_id) if profile else None
    return JobOut(
        job_id=job.id, lease_token=job.lease_token, lease_expires_at=job.lease_expires_at,
        attempt=job.attempts, input_url=f"/api/worker/jobs/{job.id}/input",
        input_fps=input_fps,
        sample_fps=min(fps or input_fps, input_fps),
        profile=ProfileOut(id=job.profile_id or "default",
                           name=profile.display_name if profile else job.profile_id or "",
                           targets=targets, min_confidence=min_conf),
        detector=DetectorOut(id=job.detector_id or "default",
                             name=det.display_name if det else job.detector_id or ""),
        segment=SegmentOut(id=seg.id, camera_id=seg.camera_id, start_at=seg.start_at,
                           duration_s=seg.duration_s, width=seg.width, height=seg.height,
                           is_night=seg.is_night),
        hints={"target_labels": targets, "min_confidence": min_conf,
               "profile": job.profile_id, "detector": job.detector_id,
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
        queue.complete(settings, job_id, body.lease_token, dets, body.model, body.processing_ms,
                       raw=body.model_dump(mode="json", exclude={"lease_token"}))
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
