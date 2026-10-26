"""JSON API used by the web UI (and anything else): needs a signed-in user.

Non-admins only see the cameras shared with them, and everything recorded by them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from .. import (
    backup,
    download_links,
    ffmpeg,
    mediamtx_auth,
    model_stats,
    recording,
    reset,
    runtime_settings,
    sprites,
)
from ..accounts import Principal
from ..config import Settings
from ..db import session_scope
from ..mediamtx import MediaMtx
from ..models import Archive, CameraRole, Detection, Event, Segment, utcnow
from ..pipeline import compaction, motion_report, preprocess, queue, runner, uploader
from ..pipeline.events import rebuild_events as rebuild_all_events
from ..pipeline.reanalyse import ReanalyseError, reanalyse_segment
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

log = logging.getLogger(__name__)
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
        # Admins: the compact process (hourly files), see pipeline/compaction.py.
        "compaction": await asyncio.to_thread(compaction.status, settings)
        if p.is_admin else None,
        # Admins: the detection workers, and the server's preparation of recordings.
        "workers": await asyncio.to_thread(queries.worker_summary) if p.is_admin else None,
        "preprocessing": await asyncio.to_thread(preprocess.status, settings, 0)
        if p.is_admin else None,
    }


@router.get("/settings")
def public_settings(settings: Settings = Depends(settings_dep),
                    p: Principal = Depends(principal)) -> dict[str, Any]:
    """The configuration (secrets removed) for admins; what the UI needs for others."""
    if p.is_admin:
        return queries.public_settings(settings)
    return {"segment_seconds": settings.segment_seconds,
            "events": settings.events.model_dump(), "cameras": []}


def _runtime_view(settings: Settings) -> dict[str, Any]:
    overview = queries.overview(settings)
    return {
        "settings": runtime_settings.describe(settings),
        "disk": {k: overview[k] for k in
                 ("recordings_bytes", "disk_free_bytes", "disk_total_bytes")},
    }


@router.get("/settings/runtime", dependencies=[Depends(require_admin)])
def runtime_settings_view(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """The tunables admins can change here (Settings page): value, config.yaml's, source."""
    return _runtime_view(settings)


class RuntimeChange(BaseModel):
    # Dotted config.yaml paths, e.g. {"processing.sample_fps": 1}; null where allowed.
    values: dict[str, Any] = {}
    # Back to config.yaml's value.
    reset: list[str] = []


@router.put("/settings/runtime", dependencies=[Depends(require_admin)])
async def update_runtime_settings(body: RuntimeChange,
                                  settings: Settings = Depends(settings_dep),
                                  mtx: MediaMtx = Depends(mediamtx_dep)) -> dict[str, Any]:
    try:
        changed = await asyncio.to_thread(runtime_settings.update, settings, body.values,
                                          body.reset)
    except runtime_settings.SettingError as e:
        raise HTTPException(422, str(e)) from e
    if changed & runtime_settings.MEDIAMTX_KEYS:
        # MediaMTX cuts the files (recordSegmentDuration, from the next file) and runs the
        # low-data live transcoder (restarted with the new settings).
        try:
            await mtx.sync_paths()
        except Exception as e:  # noqa: BLE001 - saved; applied at the next sync
            log.warning("could not update MediaMTX: %s", e)
    return await asyncio.to_thread(_runtime_view, settings)


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
        url = mediamtx_auth.url(settings, path)
        try:
            await asyncio.to_thread(ffmpeg.snapshot_from_url, url, dst)
        except Exception:  # noqa: BLE001 - camera offline: use the newest recording instead
            seg_id = await asyncio.to_thread(queries.latest_segment_id, camera_id)
            src = seg_id and await asyncio.to_thread(compaction.source_for, settings, seg_id)
            if not src:
                raise HTTPException(503, "camera offline and no recordings yet") from None
            await asyncio.to_thread(ffmpeg.thumbnail, src.path, dst, src.offset_s + 0.5, 1280)
    return FileResponse(dst, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def clean_rects(rects: list[list[float]]) -> list[list[float]]:
    """Normalized [x, y, w, h] rectangles, clipped to the picture; tiny ones dropped."""
    out = []
    for r in rects:
        if len(r) != 4:
            raise ValueError("each rect must be [x, y, w, h]")
        x, y, w, h = (min(max(v, 0.0), 1.0) for v in r)
        w, h = min(w, 1 - x), min(h, 1 - y)
        if w > 0.002 and h > 0.002:
            out.append([round(x, 5), round(y, 5), round(w, 5), round(h, 5)])
    return out


class MaskBody(BaseModel):
    rects: list[list[float]] = Field(default_factory=list)
    private: bool = False
    # Also black the rectangles out in recordings and live view (re-encodes the camera).
    # None = leave as is.
    burn_in: bool | None = None

    _rects = field_validator("rects")(lambda rects: clean_rects(rects))


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


class MotionBody(BaseModel):
    # None = motion.sensitivity
    sensitivity: Literal["low", "medium", "high"] | None = None
    # Areas whose movement is ignored, normalized [x, y, w, h].
    rects: list[list[float]] = Field(default_factory=list)

    _rects = field_validator("rects")(lambda rects: clean_rects(rects))


@router.put("/cameras/{camera_id}/motion")
def put_motion(camera_id: str, body: MotionBody, settings: Settings = Depends(settings_dep),
               p: Principal = Depends(principal)) -> dict[str, Any]:
    """Applies to segments preprocessed from now on."""
    camera_for(p, settings, camera_id, CameraRole.EDITOR)
    queries.save_motion(camera_id, body.sensitivity, body.rects)
    return queries.camera_state(camera_id)


@router.get("/segments")
def segments(camera: str | None = None, day: str | None = Query(None, description="YYYY-MM-DD"),
             only_targets: bool = False, include_deleted: bool = False,
             status: str | None = Query(None, description="new, queued, done, private, failed"),
             motion: Literal["yes", "no"] | None = Query(
                 None, description="yes: something moved; no: measured, nothing moved"),
             limit: int = Query(200, le=2000),
             p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return queries.segments(camera, day, only_targets, include_deleted, limit,
                            _camera_filter(p, camera), status, motion)


def _segment(p: Principal, segment_id: int) -> Segment:
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None or not p.can(seg.camera_id):
            raise HTTPException(404, "unknown segment")
        return seg


def _serve_segment(settings: Settings, seg: Segment, filename: str | None = None) -> Response:
    """The segment's own file, or its part of a compacted archive; from S3 when the local
    footage is gone (an archived segment then comes as its whole archive)."""
    path = compaction.playable_file(settings, seg.id)
    if path is not None:
        return FileResponse(path, media_type="video/mp4", filename=filename)
    remote = seg.remote_key or compaction.archive_remote(seg.id)
    return _serve(settings, settings.recordings_dir, None, remote, "video/mp4", filename)


@router.get("/segments/{segment_id}")
def segment(segment_id: int, p: Principal = Depends(principal)) -> dict[str, Any]:
    """One recording, with where things moved (``motion_boxes``)."""
    seg = _segment(p, segment_id)
    return {**queries.segment_dict(seg), "motion_boxes": seg.motion_boxes}


@router.post("/segments/{segment_id}/reanalyse")
def reanalyse(segment_id: int, settings: Settings = Depends(settings_dep),
              p: Principal = Depends(principal)) -> dict[str, Any]:
    """Run detection on the recording again (current mask and profiles); its detections
    are replaced. Editors of the camera."""
    seg = _segment(p, segment_id)
    check_camera(p, seg.camera_id, CameraRole.EDITOR)
    try:
        removed = reanalyse_segment(settings, segment_id)
    except ReanalyseError as e:
        raise HTTPException(409, str(e)) from e
    return {"status": "queued", "detections_removed": removed}


class DownloadLinkBody(BaseModel):
    path: str


@router.post("/download-links")
def download_link(body: DownloadLinkBody, settings: Settings = Depends(settings_dep),
                  p: Principal = Depends(principal)) -> dict[str, str]:
    """A link to one file that works for 10 minutes without signing in (for Safari, from
    the iOS home screen app). It acts as the caller, so access checks still apply."""
    if p.user_id is None:  # accounts turned off: every link works already
        return {"url": body.path}
    try:
        token = download_links.sign(settings, p.user_id, body.path)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"url": f"{body.path}?dl={token}"}


class ReanalyseRange(BaseModel):
    # None = every camera the caller may edit.
    camera: str | None = None
    start: datetime
    end: datetime


# A time range is a batch of single re-runs: at most this many recordings at once.
REANALYSE_MAX = 500


@router.post("/segments/reanalyse")
def reanalyse_range(body: ReanalyseRange, settings: Settings = Depends(settings_dep),
                    p: Principal = Depends(principal)) -> dict[str, Any]:
    """Run detection again on every recording that starts in [start, end) (current mask and
    profiles), like /segments/{id}/reanalyse for each. Editors of the camera(s). Recordings
    a worker is busy with, or already waiting, are skipped."""
    def naive(dt: datetime) -> datetime:  # stored as naive UTC
        return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt

    start, end = naive(body.start), naive(body.end)
    if end <= start:
        raise HTTPException(422, "end must be after start")
    if body.camera:
        camera_for(p, settings, body.camera, CameraRole.EDITOR)
    with session_scope() as s:
        q = select(Segment.id, Segment.camera_id).where(
            Segment.deleted_at.is_(None), Segment.start_at >= start, Segment.start_at < end)
        if body.camera:
            q = q.where(Segment.camera_id == body.camera)
        rows = [(i, c) for i, c in s.execute(q.order_by(Segment.start_at))
                if p.can(c, CameraRole.EDITOR)]
    if len(rows) > REANALYSE_MAX:
        raise HTTPException(422, f"{len(rows)} recordings: at most {REANALYSE_MAX} at once")
    queued = skipped = removed = 0
    for seg_id, _ in rows:
        try:
            removed += reanalyse_segment(settings, seg_id)
            queued += 1
        except ReanalyseError:
            skipped += 1
    return {"queued": queued, "skipped": skipped, "detections_removed": removed}


@router.get("/admin/settings-export", dependencies=[Depends(require_admin)])
def settings_export(settings: Settings = Depends(settings_dep)) -> Response:
    """What was set up in the web UI as one file, without passwords and tokens (backup.py)."""
    data = backup.export(settings)
    day = data["exported_at"][:10]
    return Response(json.dumps(data, indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="foxcam-settings-{day}.json"'})


@router.post("/admin/settings-import", dependencies=[Depends(require_admin)])
async def settings_import(data: dict[str, Any] = Body(...),
                          settings: Settings = Depends(settings_dep),
                          mtx: MediaMtx = Depends(mediamtx_dep)) -> dict[str, Any]:
    """A settings file over what is here: same ids updated, new ones added, nothing
    removed. Older formats are upgraded; a newer one is refused."""
    try:
        report = await asyncio.to_thread(backup.import_, settings, data)
    except backup.BackupError as e:
        raise HTTPException(400, str(e)) from e
    await resync(mtx)  # cameras added or changed
    return report


# Settings → Danger zone: the `foxcam reset` scopes that make sense from the web (not
# "users": that would lock everyone out).
DangerScope = Literal["recordings", "footage", "detections", "queue", "workers", "profiles",
                      "cameras"]


@router.post("/admin/reset/{scope}", dependencies=[Depends(require_admin)])
def danger_reset(scope: DangerScope, settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    """Delete one kind of data, keep the rest (like `foxcam reset`). The server's background
    work is paused meanwhile. The archiving process (a process of its own) need not be
    stopped: an archive whose recordings are deleted while it is built is dropped."""
    try:
        with runner.gate.exclusive():
            message = reset.SCOPES[scope](settings)
    except TimeoutError as e:
        raise HTTPException(409, f"the server is busy ({e}); try again") from e
    log.warning("reset %s from the web: %s", scope, message)
    return {"status": "ok", "message": message}


@router.post("/admin/rebuild-events", dependencies=[Depends(require_admin)])
async def rebuild_events(settings: Settings = Depends(settings_dep)) -> dict[str, int]:
    """Every event again from the detections, with the profiles' current rules (e.g. the
    standing-still decision, also for older detections). Rebuilt events
    notify nobody; their clips are cut again in the background."""
    return await asyncio.to_thread(rebuild_all_events, settings)


@router.get("/archives")
def archives(camera: str | None = None, day: str | None = Query(None, description="YYYY-MM-DD"),
             limit: int = Query(200, le=2000),
             p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    """Hourly files made by compaction."""
    return queries.archives(camera, day, limit, _camera_filter(p, camera))


def _archive(p: Principal, archive_id: int) -> Archive:
    with session_scope() as s:
        arc = s.get(Archive, archive_id)
        if arc is None or not p.can(arc.camera_id):
            raise HTTPException(404, "unknown hourly file")
        return arc


@router.get("/archives/{archive_id}/play")
def archive_play(archive_id: int, settings: Settings = Depends(settings_dep),
                 p: Principal = Depends(principal)) -> Response:
    arc = _archive(p, archive_id)
    rel = None if arc.deleted_at else arc.path
    return _serve(settings, settings.recordings_dir, rel, arc.remote_key, "video/mp4")


@router.get("/archives/{archive_id}/download")
def archive_download(archive_id: int, settings: Settings = Depends(settings_dep),
                     p: Principal = Depends(principal)) -> Response:
    arc = _archive(p, archive_id)
    rel = None if arc.deleted_at else arc.path
    name = f"{arc.camera_id}_{arc.start_at:%Y%m%d_%H%M%S}_hour{arc.id}.mp4"
    return _serve(settings, settings.recordings_dir, rel, arc.remote_key, "video/mp4", name)


@router.get("/segments/{segment_id}/jobs")
def segment_jobs(segment_id: int, settings: Settings = Depends(settings_dep),
                 p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    """Detection runs of a segment (one per profile) with their detection counts."""
    _segment(p, segment_id)
    return queries.segment_jobs(settings, segment_id, raw=p.is_admin)


@router.get("/segments/{segment_id}/download")
def segment_download(segment_id: int, settings: Settings = Depends(settings_dep),
                     p: Principal = Depends(principal)) -> Response:
    seg = _segment(p, segment_id)
    name = f"{seg.camera_id}_{seg.start_at:%Y%m%d_%H%M%S}_seg{seg.id}.mp4"
    return _serve_segment(settings, seg, name)


@router.get("/segments/{segment_id}/play")
def segment_play(segment_id: int, settings: Settings = Depends(settings_dep),
                 p: Principal = Depends(principal)) -> Response:
    return _serve_segment(settings, _segment(p, segment_id))


async def _thumbnails(make, item_id: int, image_url: str) -> Response:
    """WebVTT thumbnails track for the player (made on first request when missing)."""
    try:
        sprite = await asyncio.to_thread(make, item_id)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    if sprite is None:
        raise HTTPException(404, "no footage")
    return Response(sprites.vtt(sprite, image_url), media_type="text/vtt",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/segments/{segment_id}/thumbnails.vtt")
async def segment_thumbnails(segment_id: int, settings: Settings = Depends(settings_dep),
                             p: Principal = Depends(principal)) -> Response:
    """Timeline previews for the player: which tile of the sprite shows which second."""
    _segment(p, segment_id)
    return await _thumbnails(lambda i: sprites.segment_sprite(settings, i), segment_id,
                             f"/api/segments/{segment_id}/sprite.jpg")


@router.get("/segments/{segment_id}/sprite.jpg")
def segment_sprite_image(segment_id: int, settings: Settings = Depends(settings_dep),
                         p: Principal = Depends(principal)) -> FileResponse:
    _segment(p, segment_id)
    image = sprites.segment_files(settings, segment_id)[0]
    if not image.exists():
        raise HTTPException(404, "load thumbnails.vtt first")
    return FileResponse(image, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/archives/{archive_id}/thumbnails.vtt")
async def archive_thumbnails(archive_id: int, settings: Settings = Depends(settings_dep),
                             p: Principal = Depends(principal)) -> Response:
    _archive(p, archive_id)
    return await _thumbnails(lambda i: sprites.archive_sprite(settings, i), archive_id,
                             f"/api/archives/{archive_id}/sprite.jpg")


@router.get("/archives/{archive_id}/sprite.jpg")
def archive_sprite_image(archive_id: int, settings: Settings = Depends(settings_dep),
                         p: Principal = Depends(principal)) -> FileResponse:
    _archive(p, archive_id)
    image = sprites.archive_files(settings, archive_id)[0]
    if not image.exists():
        raise HTTPException(404, "load thumbnails.vtt first")
    return FileResponse(image, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/segments/{segment_id}/thumb")
def segment_thumb(segment_id: int, settings: Settings = Depends(settings_dep),
                  p: Principal = Depends(principal)) -> FileResponse:
    seg = _segment(p, segment_id)
    return FileResponse(safe_path(settings.data_dir, seg.thumb_path), media_type="image/jpeg")


@router.get("/detections")
def detections(camera: str | None = None, label: str | None = None, only_targets: bool = False,
               min_confidence: float = 0.0, segment: int | None = None,
               profile: str | None = None, detector: str | None = None,
               event: int | None = None,
               hide_still: bool = Query(
                   False, description="leave out targets standing still (no match)"),
               limit: int = Query(200, le=2000),
               p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return queries.detections(camera, label, only_targets, min_confidence, limit, segment,
                              profile, _camera_filter(p, camera), detector, event,
                              hide_still)


@router.get("/detections/{detection_id}/frame.jpg")
async def detection_frame(detection_id: int,
                          size: Literal["full", "small"] = "full",
                          settings: Settings = Depends(settings_dep),
                          p: Principal = Depends(principal)) -> FileResponse:
    """The original (uncensored) frame at the detection time, with its box drawn in.
    ``small``: a preview for table rows (cached separately)."""
    with session_scope() as s:
        det = s.get(Detection, detection_id)
        if det is None or not p.can(det.camera_id):
            raise HTTPException(404, "unknown detection")
        src = compaction.segment_source(settings, det.segment)
        offset_s, bbox, segment_id = det.offset_ms / 1000, det.bbox, det.segment_id
    if src is None:
        raise HTTPException(410, "recording deleted by retention")
    small = size == "small"
    # Keyed by the moment too: a re-analysed recording's detections can get the ids of
    # the ones they replaced.
    name = f"{detection_id}-{segment_id}-{round(offset_s * 1000)}{'-small' if small else ''}"
    dst = settings.thumbs_dir / "detections" / f"{name}.jpg"
    if not dst.exists():
        await asyncio.to_thread(ffmpeg.thumbnail, src.path, dst, src.offset_s + offset_s,
                                240 if small else 960, bbox)
    # Never changes for a URL: the web app puts the recording and moment in it (``at``).
    return FileResponse(dst, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


class FeedbackBody(BaseModel):
    correct: bool | None  # null clears the feedback


@router.post("/detections/{detection_id}/feedback")
def feedback(detection_id: int, body: FeedbackBody,
             settings: Settings = Depends(settings_dep),
             p: Principal = Depends(principal)) -> dict[str, str]:
    """Editors of the camera label detections (training data for all models)."""
    with session_scope() as s:
        det = s.get(Detection, detection_id)
        camera_id = det.camera_id if det else None
    if camera_id is None or not p.can(camera_id):
        raise HTTPException(404, "unknown detection")
    check_camera(p, camera_id, CameraRole.EDITOR)
    if not queries.set_feedback(settings, detection_id, body.correct):
        raise HTTPException(404, "unknown detection")
    return {"status": "ok"}


@router.get("/detections/feedback-export", dependencies=[Depends(require_admin)])
def feedback_export() -> list[dict[str, Any]]:
    return queries.feedback_export()


@router.get("/events")
def events(camera: str | None = None, profile: str | None = None, label: str | None = None,
           min_agreement: int = Query(1, ge=1, le=10,
                                      description="seen by at least this many detectors"),
           hide_wrong: bool = Query(False, description="leave out events marked wrong"),
           limit: int = Query(100, le=1000),
           p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return queries.events(camera, limit, profile, label, _camera_filter(p, camera),
                          min_agreement, hide_wrong)


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


@router.get("/archiving", dependencies=[Depends(require_admin)])
async def archiving(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Jobs → Archiving: the process joining finished periods into archive files."""
    return await asyncio.to_thread(compaction.status, settings)


@router.get("/models/stats", dependencies=[Depends(require_admin)])
def models_stats(days: float | None = Query(None, gt=0, description="last N days; none = all"),
                 settings: Settings = Depends(settings_dep)) -> list[dict[str, Any]]:
    """Per profile, detector and model: detections, targets, feedback precision, average
    processing time, job outcomes and agreement with the profile's other detectors."""
    since = utcnow() - timedelta(days=days) if days else None
    return model_stats.model_stats(settings, since)


@router.get("/motion/report", dependencies=[Depends(require_admin)])
def motion_report_get(days: float = Query(7, gt=0, le=365),
                      camera: str | None = None,
                      settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Per camera and profile, what the motion options would do with the recordings and
    targets of the last ``days`` (shadow mode), split by correct / wrong marks; plus how
    many recordings still have no motion measured, and the backfill's progress."""
    since = utcnow() - timedelta(days=days)
    return {"rows": motion_report.report(settings, since, camera),
            "unmeasured": len(motion_report.pending(since)),
            "backfill": motion_report.backfill.state}


class BackfillBody(BaseModel):
    days: float = Field(default=7, gt=0, le=365)


@router.post("/motion/backfill", dependencies=[Depends(require_admin)])
def motion_backfill(body: BackfillBody,
                    settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Measure motion on the period's recordings that have none yet, in the background
    (from their stored footage; detections and events stay as they are)."""
    started = motion_report.backfill.start(settings, utcnow() - timedelta(days=body.days))
    return {"started": started, **motion_report.backfill.state}


@router.get("/queue", dependencies=[Depends(require_admin)])
def queue_status(state: str | None = None,
                 limit: int = Query(100, le=1000)) -> dict[str, Any]:
    return queries.queue_view(limit, state)


@router.get("/preparation", dependencies=[Depends(require_admin)])
def preparation_status(limit: int = Query(100, le=1000),
                         settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Recordings waiting for the server to prepare them (censored copy, motion,
    thumbnails) before they get detection jobs, the ones being prepared, and the pace."""
    return preprocess.status(settings, limit)


@router.post("/queue/jobs/{job_id}/requeue", dependencies=[Depends(require_admin)])
def requeue(job_id: int, settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    try:
        queue.requeue(settings, job_id)
    except ReanalyseError as e:
        raise HTTPException(409, str(e)) from e
    return {"status": "ok"}
