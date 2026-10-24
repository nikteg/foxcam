"""Camera management (add / edit / remove / test) and the Tapo account integration.

Whoever adds a camera owns it (see accounts.py); editors change its settings, owners
remove it. The Tapo account is the server's, so only admins manage it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import accounts, camera_store, recording, tapo_account
from ..accounts import Principal
from ..config import Settings, Source
from ..mediamtx import MediaMtx
from ..models import CameraRole
from .deps import camera_for, mediamtx_dep, principal, require_admin, settings_dep
from .queries import _redact_url

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["cameras"])


async def resync(mtx: MediaMtx) -> None:
    """Push the new camera list to MediaMTX. Ingest picks up changes on its own."""
    try:
        await mtx.sync_paths()
    except Exception as e:  # noqa: BLE001 - camera is saved; MediaMTX catches up on restart
        log.warning("could not update MediaMTX: %s", e)


class RecordingProfileIn(BaseModel):
    # None = back to the camera's config.yaml / the default profile.
    profile: str | None


@router.get("/recording-profiles")
def list_recording_profiles(settings: Settings = Depends(settings_dep),
                            p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    by_profile: dict[str, list[str]] = {}
    for cam in settings.all_cameras(include_disabled=True):
        if not p.can(cam.id):
            continue
        profile, _ = recording.camera_recording_profile(settings, cam)
        by_profile.setdefault(profile.id, []).append(cam.id)
    return [{**rp.model_dump(), "display_name": rp.display_name,
             "default": rp.id == settings.recording_profile_default,
             "cameras": by_profile.get(rp.id, [])}
            for rp in recording.all_recording_profiles(settings)]


@router.put("/cameras/{camera_id}/recording-profile")
async def set_recording_profile(camera_id: str, body: RecordingProfileIn,
                                settings: Settings = Depends(settings_dep),
                                mtx: MediaMtx = Depends(mediamtx_dep),
                                p: Principal = Depends(principal)) -> dict[str, Any]:
    """Applies to hours compacted from now on (recordings are re-encoded when they are
    joined into hourly files)."""
    camera_for(p, settings, camera_id, CameraRole.EDITOR)
    try:
        await asyncio.to_thread(recording.set_camera_recording_profile, settings, camera_id,
                                body.profile)
    except recording.RecordingError as e:
        raise HTTPException(400, str(e)) from e
    await resync(mtx)
    cam = settings.camera(camera_id)
    p, source = recording.camera_recording_profile(settings, cam)
    return {"recording_profile": p.id, "recording_profile_source": source}


class CameraIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    id: str | None = Field(default=None, max_length=60)
    source: Source
    segment_seconds: int | None = Field(default=None, ge=5, le=3600)


class CameraPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    segment_seconds: int | None = Field(default=None, ge=5, le=3600)
    clear_segment_seconds: bool = False
    enabled: bool | None = None


def may_add_cameras(p: Principal = Depends(principal),
                    settings: Settings = Depends(settings_dep)) -> Principal:
    if not (p.is_admin or settings.auth.users_can_add_cameras):
        raise HTTPException(403, "only admins can add cameras")
    return p


@router.post("/cameras", status_code=201)
async def add_camera(body: CameraIn, settings: Settings = Depends(settings_dep),
                     mtx: MediaMtx = Depends(mediamtx_dep),
                     p: Principal = Depends(may_add_cameras)) -> dict[str, Any]:
    if body.source.type == "tapo_cloud" and not p.is_admin:
        raise HTTPException(403, "only admins can add cameras from the server's Tapo account")
    try:
        cam = await asyncio.to_thread(
            camera_store.add_camera, settings, name=body.name, camera_id=body.id,
            source=body.source.model_dump(), segment_seconds=body.segment_seconds)
    except camera_store.CameraError as e:
        raise HTTPException(400, str(e)) from e
    accounts.forget_camera(cam.id)  # shares left over from an older camera with this id
    if p.user_id is not None:
        accounts.grant(cam.id, p.user_id, CameraRole.OWNER)
    await resync(mtx)
    return {"id": cam.id}


@router.patch("/cameras/{camera_id}")
async def update_camera(camera_id: str, body: CameraPatch,
                        mtx: MediaMtx = Depends(mediamtx_dep),
                        settings: Settings = Depends(settings_dep),
                        p: Principal = Depends(principal)) -> dict[str, Any]:
    camera_for(p, settings, camera_id, CameraRole.EDITOR)
    seg: int | None | type[...] = ...
    if body.clear_segment_seconds:
        seg = None
    elif body.segment_seconds is not None:
        seg = body.segment_seconds
    try:
        cam = await asyncio.to_thread(camera_store.update_camera, camera_id, name=body.name,
                                      segment_seconds=seg, enabled=body.enabled)
    except camera_store.CameraError as e:
        raise HTTPException(400, str(e)) from e
    await resync(mtx)
    return {"id": cam.id}


@router.delete("/cameras/{camera_id}")
async def delete_camera(camera_id: str, mtx: MediaMtx = Depends(mediamtx_dep),
                        settings: Settings = Depends(settings_dep),
                        p: Principal = Depends(principal)) -> dict[str, str]:
    camera_for(p, settings, camera_id, CameraRole.OWNER)
    try:
        await asyncio.to_thread(camera_store.delete_camera, camera_id)
    except camera_store.CameraError as e:
        raise HTTPException(400, str(e)) from e
    accounts.forget_camera(camera_id)
    await resync(mtx)
    return {"status": "ok"}


@router.get("/cameras-sample-files", dependencies=[Depends(may_add_cameras)])
def sample_files(settings: Settings = Depends(settings_dep)) -> list[str]:
    return camera_store.sample_files(settings)


class ProbeIn(BaseModel):
    source: Source


def _ffprobe(url_or_path: str, rtsp: bool) -> dict[str, Any]:
    args = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
            "-select_streams", "v:0"]
    if rtsp:
        args += ["-rtsp_transport", "tcp", "-timeout", "8000000"]
    try:
        proc = subprocess.run([*args, url_or_path], capture_output=True, text=True, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "timed out (is the camera reachable from this server?)"}
    if proc.returncode != 0:
        err = proc.stderr.strip().splitlines()[-1:] or ["no video stream"]
        return {"ok": False, "detail": err[0][:300]}
    streams = json.loads(proc.stdout).get("streams") or []
    if not streams:
        return {"ok": False, "detail": "no video stream found"}
    v = streams[0]
    return {"ok": True, "detail": f"{v.get('codec_name', '?')} {v.get('width')}×{v.get('height')}",
            "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height")}


@router.post("/cameras/probe")
async def probe(body: ProbeIn, settings: Settings = Depends(settings_dep),
                p: Principal = Depends(may_add_cameras)) -> dict[str, Any]:
    """Check that a source works before adding it."""
    src = body.source
    if src.type == "tapo_cloud" and not p.is_admin:
        raise HTTPException(403, "only admins can use the server's Tapo account")
    if src.type == "rtsp":
        result = await asyncio.to_thread(_ffprobe, src.url, True)
        # ffmpeg errors echo the URL; never send the camera password back.
        result["detail"] = result["detail"].replace(src.url, _redact_url(src.url))
        if result["ok"] and result.get("codec") == "hevc":
            result["detail"] += " — H.265: browsers may not play it; pick H.264 in the camera"
        return result
    if src.type == "file":
        path = (settings.samples_dir / src.path).resolve()
        if not path.is_relative_to(settings.samples_dir) or not path.is_file():
            return {"ok": False, "detail": "file not found in the samples folder"}
        return await asyncio.to_thread(_ffprobe, str(path), False)
    if src.type == "tapo_cloud":
        try:
            found = await tapo_account.devices(settings)
        except tapo_account.TapoError as e:
            return {"ok": False, "detail": str(e)}
        match = [d for d in found if d["device_id"] == src.device_id or d["name"] == src.device_name]
        return {"ok": bool(match),
                "detail": f"found {match[0]['model']}" if match else "camera not on this account"}
    return {"ok": True, "detail": "synthetic test pattern"}


# --- Tapo account --------------------------------------------------------------------

class TapoLoginIn(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class TapoMfaIn(BaseModel):
    login_id: str
    code: str = Field(min_length=3, max_length=12)


def _tapo_error(e: tapo_account.TapoError) -> HTTPException:
    return HTTPException(e.status, str(e))


@router.get("/integrations/tapo", dependencies=[Depends(require_admin)])
def tapo_status(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    return tapo_account.status(settings)


@router.post("/integrations/tapo/login", dependencies=[Depends(require_admin)])
async def tapo_login(body: TapoLoginIn, settings: Settings = Depends(settings_dep)) -> dict:
    try:
        return await tapo_account.login(settings, body.email.strip(), body.password)
    except tapo_account.TapoError as e:
        raise _tapo_error(e) from e


@router.post("/integrations/tapo/mfa", dependencies=[Depends(require_admin)])
async def tapo_mfa(body: TapoMfaIn, settings: Settings = Depends(settings_dep)) -> dict:
    try:
        return await tapo_account.submit_mfa(settings, body.login_id, body.code)
    except tapo_account.TapoError as e:
        raise _tapo_error(e) from e


@router.get("/integrations/tapo/devices", dependencies=[Depends(require_admin)])
async def tapo_devices(settings: Settings = Depends(settings_dep)) -> list[dict[str, Any]]:
    try:
        return await tapo_account.devices(settings)
    except tapo_account.TapoError as e:
        raise _tapo_error(e) from e


@router.post("/integrations/tapo/logout", dependencies=[Depends(require_admin)])
def tapo_logout(settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    tapo_account.logout(settings)
    return {"status": "ok"}
