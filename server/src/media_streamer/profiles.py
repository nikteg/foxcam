"""Detection profiles: what to look for on which camera, and which workers can do it.

Profiles come from ``config.yaml`` (read-only in the UI) or are added in the web UI
(stored in SQLite). Each camera runs its UI selection, else its config ``profiles``,
else every profile marked ``default``. Each recorded segment gets one job per profile;
a worker may lease a job when it has all the profile's ``requires`` tags.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from .config import TAG, ProfileConfig, Settings
from .db import db_ready, session_scope
from .models import CameraState, DetectionProfile, Job, JobState

_TAG_RE = re.compile(TAG)


class ProfileError(ValueError):
    pass


def ui_profiles() -> list[ProfileConfig]:
    if not db_ready():
        return []
    with session_scope() as s:
        rows = s.scalars(select(DetectionProfile).order_by(DetectionProfile.created_at)).all()
        return [ProfileConfig.model_validate({**r.config, "id": r.id, "managed": "ui"})
                for r in rows]


def normalize_tags(tags: list[str] | str | None) -> list[str]:
    """Lower-case, de-duplicated, valid tags (invalid ones are dropped)."""
    if isinstance(tags, str):
        tags = tags.split(",")
    out: list[str] = []
    for t in tags or []:
        t = t.strip().lower()
        if t and _TAG_RE.match(t) and t not in out:
            out.append(t)
    return out


def _validate(data: dict[str, Any]) -> ProfileConfig:
    try:
        return ProfileConfig.model_validate({**data, "managed": "ui"})
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(p) for p in first["loc"])
        raise ProfileError(f"{field}: {first['msg']}") from e


def save_profile(settings: Settings, data: dict[str, Any], *, create: bool) -> ProfileConfig:
    data = {**data, "requires": normalize_tags(data.get("requires"))}
    profile = _validate(data)
    existing = settings.profile(profile.id)
    if create and existing:
        raise ProfileError(f"a profile with id '{profile.id}' already exists")
    if existing and existing.managed == "config":
        raise ProfileError("profiles from config.yaml can only be changed in config.yaml")
    stored = profile.model_dump(exclude={"id", "managed"})
    with session_scope() as s:
        row = s.get(DetectionProfile, profile.id)
        if row is None:
            if not create:
                raise ProfileError("unknown profile")
            s.add(DetectionProfile(id=profile.id, config=stored))
        else:
            row.config = stored
    return profile


def delete_profile(settings: Settings, profile_id: str) -> int:
    """Removes a UI profile and cancels its waiting jobs. Detections and events stay."""
    with session_scope() as s:
        row = s.get(DetectionProfile, profile_id)
        if row is None:
            raise ProfileError("only profiles added in the web UI can be removed here")
        s.delete(row)
    return cancel_jobs_without_profile(settings)


def cancel_jobs_without_profile(settings: Settings) -> int:
    """Waiting jobs whose profile was removed or disabled can never run: give up on them."""
    from .pipeline.queue import settle_segment  # deferred: queue imports this module

    active = {p.id for p in settings.all_profiles()}
    with session_scope() as s:
        jobs = s.scalars(select(Job).where(
            Job.state.in_([JobState.PENDING, JobState.FAILED]),
            Job.profile_id.not_in(active) if active else Job.id.is_not(None))).all()
        for job in jobs:
            job.state = JobState.DEAD
            job.error = f"profile '{job.profile_id}' was removed or disabled"
            settle_segment(settings, job.segment)
        return len(jobs)


def camera_profiles(settings: Settings, camera_id: str,
                    override: list[str] | None | type[...] = ...) -> list[ProfileConfig]:
    """Enabled profiles that run on a camera."""
    if override is ...:
        with session_scope() as s:
            state = s.get(CameraState, camera_id)
            override = state.profiles if state else None
    by_id = {p.id: p for p in settings.all_profiles()}
    if override is not None:
        return [by_id[i] for i in override if i in by_id]
    cam = settings.camera(camera_id)
    if cam is not None and cam.profiles is not None:
        return [by_id[i] for i in cam.profiles if i in by_id]
    return [p for p in by_id.values() if p.default]


def set_camera_profiles(settings: Settings, camera_id: str,
                        profile_ids: list[str] | None) -> None:
    """UI selection for a camera; None = back to the config / default profiles."""
    if profile_ids is not None:
        known = {p.id for p in settings.all_profiles(include_disabled=True)}
        unknown = [i for i in profile_ids if i not in known]
        if unknown:
            raise ProfileError(f"unknown profile(s): {', '.join(unknown)}")
        profile_ids = list(dict.fromkeys(profile_ids))
    with session_scope() as s:
        state = s.get(CameraState, camera_id) or CameraState(camera_id=camera_id)
        s.add(state)
        state.profiles = profile_ids


def eligible_profiles(settings: Settings, tags: list[str],
                      labels: list[str] | None = None) -> list[str]:
    """Profiles whose jobs a worker with these tags may lease.

    ``labels``: what the worker can detect (e.g. the classes of a fine-tuned or COCO
    YOLO model). None = anything (open vocabulary, broad models). A profile is only
    offered when at least one of its targets (after aliases) is among them.
    """
    have = set(tags)
    out = []
    for p in settings.all_profiles():
        if not set(p.requires) <= have:
            continue
        if labels is not None:
            can = {x.lower() for x in labels} | {p.canonical(x).lower() for x in labels}
            if not can & {t.lower() for t in p.targets}:
                continue
        out.append(p.id)
    return out
