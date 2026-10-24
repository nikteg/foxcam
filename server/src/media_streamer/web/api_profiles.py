"""Detection profiles (what to look for) and which cameras run them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import labels, profiles
from ..accounts import Principal
from ..config import PROFILE_ID, Settings
from ..db import session_scope
from ..models import CameraRole, Job, JobState, Worker, WorkerToken, utcnow
from .deps import camera_for, principal, require_admin, settings_dep
from .queries import WORKER_ONLINE_SECONDS

router = APIRouter(prefix="/api", tags=["profiles"])


class ProfileIn(BaseModel):
    id: str = Field(pattern=PROFILE_ID, max_length=40)
    name: str | None = Field(default=None, max_length=80)
    targets: list[str] = Field(min_length=1, max_length=50)
    aliases: dict[str, str] = {}
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    requires: list[str] = Field(default=[], max_length=20)
    fps: float | None = Field(default=None, gt=0, le=30)
    default: bool = False
    events_days: float | None = Field(default=None, ge=0)
    enabled: bool = True


class CameraProfilesIn(BaseModel):
    # None = back to the camera's config.yaml / default profiles.
    profiles: list[str] | None


def _error(e: profiles.ProfileError) -> HTTPException:
    return HTTPException(400, str(e))


def profile_list(settings: Settings, viewer: Principal) -> list[dict[str, Any]]:
    now = utcnow()
    with session_scope() as s:
        workers = [(w.id, set(w.tags or [])) for w in s.scalars(select(Worker))
                   if (now - w.last_seen).total_seconds() < WORKER_ONLINE_SECONDS]
        waiting: dict[str, int] = {}
        for pid in s.scalars(select(Job.profile_id).where(
                Job.state.in_([JobState.PENDING, JobState.FAILED]))):
            waiting[pid] = waiting.get(pid, 0) + 1
    cams = [c for c in settings.all_cameras(include_disabled=True) if viewer.can(c.id)]
    by_camera = {c.id: {p.id for p in profiles.camera_profiles(settings, c.id)} for c in cams}
    out = []
    for p in settings.all_profiles(include_disabled=True):
        need = set(p.requires)
        out.append({
            **p.model_dump(), "display_name": p.display_name,
            "cameras": [c for c, ids in by_camera.items() if p.id in ids],
            "workers_online": [w for w, tags in workers if need <= tags],
            "waiting_jobs": waiting.get(p.id, 0),
        })
    return out


@router.get("/profiles")
def list_profiles(settings: Settings = Depends(settings_dep),
                  p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return profile_list(settings, p)


@router.post("/profiles", status_code=201, dependencies=[Depends(require_admin)])
def create_profile(body: ProfileIn, settings: Settings = Depends(settings_dep)) -> dict:
    try:
        p = profiles.save_profile(settings, body.model_dump(), create=True)
    except profiles.ProfileError as e:
        raise _error(e) from e
    return {"id": p.id}


@router.put("/profiles/{profile_id}", dependencies=[Depends(require_admin)])
def update_profile(profile_id: str, body: ProfileIn,
                   settings: Settings = Depends(settings_dep)) -> dict:
    if body.id != profile_id:
        raise HTTPException(400, "the id of a profile cannot be changed")
    try:
        p = profiles.save_profile(settings, body.model_dump(), create=False)
    except profiles.ProfileError as e:
        raise _error(e) from e
    if not p.enabled:
        profiles.cancel_jobs_without_profile(settings)
    return {"id": p.id}


@router.delete("/profiles/{profile_id}", dependencies=[Depends(require_admin)])
def delete_profile(profile_id: str, settings: Settings = Depends(settings_dep)) -> dict:
    try:
        cancelled = profiles.delete_profile(settings, profile_id)
    except profiles.ProfileError as e:
        raise _error(e) from e
    return {"status": "ok", "cancelled_jobs": cancelled}


@router.put("/cameras/{camera_id}/profiles")
def set_camera_profiles(camera_id: str, body: CameraProfilesIn,
                        settings: Settings = Depends(settings_dep),
                        p: Principal = Depends(principal)) -> dict[str, Any]:
    camera_for(p, settings, camera_id, CameraRole.EDITOR)
    try:
        profiles.set_camera_profiles(settings, camera_id, body.profiles)
    except profiles.ProfileError as e:
        raise _error(e) from e
    return {"profiles": [p.id for p in profiles.camera_profiles(settings, camera_id)]}


@router.get("/labels", dependencies=[Depends(require_admin)])
def label_suggestions() -> dict[str, Any]:
    """Suggestions for label pickers: per worker its detector, tags and (closed-set models)
    vocabulary; the open-vocabulary list (LVIS, Objects365); labels seen in detections;
    synonym groups (see labels.py)."""
    return labels.suggestions()


@router.get("/worker-tags", dependencies=[Depends(require_admin)])
def known_tags(settings: Settings = Depends(settings_dep)) -> list[str]:
    """Tags seen on workers, tokens and profiles (suggestions for the UI)."""
    tags: set[str] = set()
    with session_scope() as s:
        for row in s.scalars(select(Worker.tags)):
            tags.update(row or [])
        for row in s.scalars(select(WorkerToken.tags).where(WorkerToken.revoked_at.is_(None))):
            tags.update(row or [])
    for p in settings.all_profiles(include_disabled=True):
        tags.update(p.requires)
    return sorted(tags)
