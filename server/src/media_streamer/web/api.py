"""JSON API used by the web UI (and anything else): needs a signed-in user.

Non-admins only see the cameras shared with them, and everything recorded by them.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

from .. import ffmpeg, recording
from ..accounts import Principal
from ..config import Settings
from ..db import session_scope
from ..mediamtx import MediaMtx
from ..models import CameraRole, Detection, Event, Segment
from ..pipeline import queue, uploader
from ..storage import StorageError, boto3_available, get_storage
from . import queries
from .api_cameras import resync
from .deps import (
    camera_for,
    check_camera,
    mediamtx_dep,
    principal,
    require_admin,
    safe_path,
    settings_dep,
)

router = APIRouter(prefix="/api", tags=["public"])


def _serve(settings: Settings, base: Path, rel: str | None, remote_key: str | None,
           media_type: str, filename: str | None = None) -> Response:
    """The local file if it still exists, otherwise a redirect to a signed S3 URL."""
    if rel:
        path = (base / rel).resolve()
        if path.is_relative_to(base.resolve()) and path.is_file():
            return FileResponse(path, media_type=media_type, filename=filename)
    store = get_storage(settings)
    if remote_key and store:
        return RedirectResponse(store.url(remote_key, filename), status_code=307)
    raise HTTPException(404, "file not available")


def _camera_filter(p: Principal, camera: str | None) -> set[str] | None:
    """Camera ids a list query may return: the user's cameras (None = all), and only
    `camera` when one is asked for."""
    allowed = p.camera_ids()
    if camera is None:
        return allowed
    return {camera} if allowed is None or camera in allowed else set()


@router.get("/status")
async def status(settings: Settings = Depends(settings_dep),
                 mtx: MediaMtx = Depends(mediamtx_dep),
                 p: Principal = Depends(principal)) -> dict[str, Any]:
    live = await mtx.paths()
    return {
        "mediamtx_reachable": bool(live) or not settings.all_cameras(),
        "cameras": await asyncio.to_thread(queries.cameras, settings, live, p),
        **await asyncio.to_thread(queries.overview, settings, p.camera_ids()),
    }


@router.get("/settings")
def public_settings(settings: Settings = Depends(settings_dep),
                    p: Principal = Depends(principal)) -> dict[str, Any]:
    """The configuration (secrets removed) for admins; what the UI needs for others."""
    if p.is_admin:
        return queries.public_settings(settings)
    return {"segment_seconds": settings.segment_seconds,
            "events": settings.events.model_dump(), "cameras": []}


@router.get("/storage", dependencies=[Depends(require_admin)])
def storage_status(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    cfg = settings.storage.s3
    out: dict[str, Any] = {
        "enabled": cfg.enabled, "boto3": boto3_available(), "bucket": cfg.bucket,
        "endpoint_url": cfg.endpoint_url, "prefix": cfg.prefix, "clips": cfg.clips,
        "recordings": cfg.recordings, "delete_local_clips": cfg.delete_local_clips,
        "recordings_days": cfg.recordings_days,
    }
    if cfg.enabled:
        out.update(uploader.stats(settings))
    return out


@router.post("/storage/test", dependencies=[Depends(require_admin)])
async def storage_test(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    try:
        store = get_storage(settings)
    except StorageError as e:
        return {"ok": False, "detail": str(e)}
    if store is None:
        raise HTTPException(409, "S3 storage is not enabled in config.yaml")
    return await asyncio.to_thread(store.check)


@router.get("/cameras")
async def cameras(settings: Settings = Depends(settings_dep),
                  mtx: MediaMtx = Depends(mediamtx_dep),
                  p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(queries.cameras, settings, await mtx.paths(), p)


@router.get("/cameras/{camera_id}/snapshot.jpg")
async def snapshot(camera_id: str, fresh: bool = False, source: bool = False,
                   settings: Settings = Depends(settings_dep),
                   p: Principal = Depends(principal)) -> FileResponse:
    """Current frame (from the live stream, falling back to the latest recording).

    ``source``: the camera's own picture, before burned-in masks (for the mask editor;
    editors only).
    """
    cam = camera_for(p, settings, camera_id, CameraRole.EDITOR if source else CameraRole.VIEWER)
    path = await asyncio.to_thread(recording.source_path, settings, cam) if source else cam.id
    name = camera_id if path == cam.id else f"{camera_id}.source"
    dst = settings.thumbs_dir / "snapshots" / f"{name}.jpg"
    if fresh or not dst.exists() or time.time() - dst.stat().st_mtime > 10:
        url = f"{settings.mediamtx.rtsp_url.rstrip('/')}/{path}"
        try:
            await asyncio.to_thread(ffmpeg.snapshot_from_url, url, dst)
        except Exception:  # noqa: BLE001 - camera offline: use the newest recording instead
            rel = await asyncio.to_thread(queries.latest_segment_path, camera_id)
            if not rel:
                raise HTTPException(503, "camera offline and no recordings yet") from None
            await asyncio.to_thread(ffmpeg.thumbnail, settings.recordings_dir / rel, dst, 0.5,
                                    1280)
    return FileResponse(dst, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


class MaskBody(BaseModel):
    rects: list[list[float]] = Field(default_factory=list)
    private: bool = False
    # Also black the rectangles out in recordings and live view (re-encodes the camera).
    # None = leave as is.
    burn_in: bool | None = None

    @field_validator("rects")
    @classmethod
    def _validate(cls, rects: list[list[float]]) -> list[list[float]]:
        out = []
        for r in rects:
            if len(r) != 4:
                raise ValueError("each rect must be [x, y, w, h]")
            x, y, w, h = (min(max(v, 0.0), 1.0) for v in r)
            w, h = min(w, 1 - x), min(h, 1 - y)
            if w > 0.002 and h > 0.002:
                out.append([round(x, 5), round(y, 5), round(w, 5), round(h, 5)])
        return out


@router.get("/cameras/{camera_id}/mask")
def get_mask(camera_id: str, settings: Settings = Depends(settings_dep),
             p: Principal = Depends(principal)) -> dict[str, Any]:
    camera_for(p, settings, camera_id)
    return queries.camera_state(camera_id)


@router.put("/cameras/{camera_id}/mask")
async def put_mask(camera_id: str, body: MaskBody, settings: Settings = Depends(settings_dep),
                   mtx: MediaMtx = Depends(mediamtx_dep),
                   p: Principal = Depends(principal)) -> dict[str, Any]:
    """Applies to segments preprocessed from now on (already queued jobs keep their mask).

    With burned-in masks, recording and live view switch to the new mask within seconds.
    """
    camera_for(p, settings, camera_id, CameraRole.EDITOR)
    before = await asyncio.to_thread(queries.camera_state, camera_id)
    await asyncio.to_thread(queries.save_camera_state, camera_id, body.rects, body.private)
    if body.burn_in is not None:
        await asyncio.to_thread(recording.set_burn_masks, camera_id, body.burn_in)
    after = await asyncio.to_thread(queries.camera_state, camera_id)
    if after["burn_masks"] or before["burn_masks"]:
        await resync(mtx)
    return after


@router.get("/segments")
def segments(camera: str | None = None, day: str | None = Query(None, description="YYYY-MM-DD"),
             only_targets: bool = False, include_deleted: bool = False,
             limit: int = Query(200, le=2000),
             p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return queries.segments(camera, day, only_targets, include_deleted, limit,
                            _camera_filter(p, camera))


def _segment(p: Principal, segment_id: int) -> Segment:
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None or not p.can(seg.camera_id):
            raise HTTPException(404, "unknown segment")
        return seg


@router.get("/segments/{segment_id}")
def segment(segment_id: int, p: Principal = Depends(principal)) -> dict[str, Any]:
    return queries.segment_dict(_segment(p, segment_id))


@router.get("/segments/{segment_id}/jobs")
def segment_jobs(segment_id: int, settings: Settings = Depends(settings_dep),
                 p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    """Detection runs of a segment (one per profile) with their detection counts."""
    _segment(p, segment_id)
    return queries.segment_jobs(settings, segment_id)


@router.get("/segments/{segment_id}/download")
def segment_download(segment_id: int, settings: Settings = Depends(settings_dep),
                     p: Principal = Depends(principal)) -> Response:
    seg = _segment(p, segment_id)
    name = f"{seg.camera_id}_{seg.start_at:%Y%m%d_%H%M%S}.mp4"
    return _serve(settings, settings.recordings_dir, seg.path, seg.remote_key, "video/mp4", name)


@router.get("/segments/{segment_id}/play")
def segment_play(segment_id: int, settings: Settings = Depends(settings_dep),
                 p: Principal = Depends(principal)) -> Response:
    seg = _segment(p, segment_id)
    return _serve(settings, settings.recordings_dir, seg.path, seg.remote_key, "video/mp4")


@router.get("/segments/{segment_id}/thumb")
def segment_thumb(segment_id: int, settings: Settings = Depends(settings_dep),
                  p: Principal = Depends(principal)) -> FileResponse:
    seg = _segment(p, segment_id)
    return FileResponse(safe_path(settings.data_dir, seg.thumb_path), media_type="image/jpeg")


@router.get("/detections")
def detections(camera: str | None = None, label: str | None = None, only_targets: bool = False,
               min_confidence: float = 0.0, segment: int | None = None,
               profile: str | None = None,
               limit: int = Query(200, le=2000),
               p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return queries.detections(camera, label, only_targets, min_confidence, limit, segment,
                              profile, _camera_filter(p, camera))


@router.get("/detections/{detection_id}/frame.jpg")
async def detection_frame(detection_id: int, settings: Settings = Depends(settings_dep),
                          p: Principal = Depends(principal)) -> FileResponse:
    """The original (uncensored) frame at the detection time, with its box drawn in."""
    with session_scope() as s:
        det = s.get(Detection, detection_id)
        if det is None or not p.can(det.camera_id):
            raise HTTPException(404, "unknown detection")
        seg_path, deleted = det.segment.path, det.segment.deleted_at is not None
        offset_s, bbox = det.offset_ms / 1000, det.bbox
    if deleted:
        raise HTTPException(410, "recording deleted by retention")
    dst = settings.thumbs_dir / "detections" / f"{detection_id}.jpg"
    if not dst.exists():
        await asyncio.to_thread(ffmpeg.thumbnail, safe_path(settings.recordings_dir, seg_path),
                                dst, offset_s, 960, bbox)
    return FileResponse(dst, media_type="image/jpeg")


class FeedbackBody(BaseModel):
    correct: bool | None  # null clears the feedback


@router.post("/detections/{detection_id}/feedback")
def feedback(detection_id: int, body: FeedbackBody,
             p: Principal = Depends(principal)) -> dict[str, str]:
    """Editors of the camera label detections (training data for all models)."""
    with session_scope() as s:
        det = s.get(Detection, detection_id)
        camera_id = det.camera_id if det else None
    if camera_id is None or not p.can(camera_id):
        raise HTTPException(404, "unknown detection")
    check_camera(p, camera_id, CameraRole.EDITOR)
    if not queries.set_feedback(detection_id, body.correct):
        raise HTTPException(404, "unknown detection")
    return {"status": "ok"}


@router.get("/detections/feedback-export", dependencies=[Depends(require_admin)])
def feedback_export() -> list[dict[str, Any]]:
    return queries.feedback_export()


@router.get("/events")
def events(camera: str | None = None, profile: str | None = None, label: str | None = None,
           limit: int = Query(100, le=1000),
           p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return queries.events(camera, limit, profile, label, _camera_filter(p, camera))


def _event(p: Principal, event_id: int) -> Event:
    with session_scope() as s:
        ev = s.get(Event, event_id)
        if ev is None or not p.can(ev.camera_id):
            raise HTTPException(404, "unknown event")
        return ev


@router.get("/events/{event_id}/clip")
def event_clip(event_id: int, download: bool = False,
               settings: Settings = Depends(settings_dep),
               p: Principal = Depends(principal)) -> Response:
    ev = _event(p, event_id)
    name = f"{ev.camera_id}_{ev.label}_{ev.first_ts:%Y%m%d_%H%M%S}.mp4".replace(" ", "-")
    return _serve(settings, settings.data_dir, ev.clip_path, ev.clip_remote_key, "video/mp4",
                  name if download else None)


@router.get("/events/{event_id}/thumb")
def event_thumb(event_id: int, settings: Settings = Depends(settings_dep),
                p: Principal = Depends(principal)) -> Response:
    ev = _event(p, event_id)
    return _serve(settings, settings.data_dir, ev.thumb_path, ev.thumb_remote_key, "image/jpeg")


@router.get("/queue", dependencies=[Depends(require_admin)])
def queue_status(state: str | None = None,
                 limit: int = Query(100, le=1000)) -> dict[str, Any]:
    return queries.queue_view(limit, state)


@router.post("/queue/jobs/{job_id}/requeue", dependencies=[Depends(require_admin)])
def requeue(job_id: int) -> dict[str, str]:
    queue.requeue(job_id)
    return {"status": "ok"}
