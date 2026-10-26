"""Settings export and import (Settings → Configuration): what was set up in the web UI, as
one JSON file, without passwords and tokens.

The file has a ``config_format_version``. An import upgrades older formats one step at a
time (``MIGRATIONS``) and refuses newer ones. Change the format only together with a
migration from the version before.

Secrets are left out: a camera stream URL's login, notification tokens, passwords and
signing secrets, header values and Apprise URLs (they embed tokens). On import, things that
already exist here keep their stored secrets; a new camera whose URL had a login cannot be
added without it, and a new channel may need its token added under Notifications.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from . import camera_store, notify, profiles, recording, runtime_settings
from .config import Settings
from .db import session_scope
from .models import (
    CameraState,
    DetectionProfile,
    ManagedCamera,
    MaskVersion,
    NotificationChannel,
    utcnow,
)
from .web.queries import _redact_url, save_camera_state, save_motion

CONFIG_FORMAT_VERSION = 1

# Upgrade a file of version n to n + 1 (keyed by n).
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}

REDACTED = "***"


class BackupError(ValueError):
    pass


# --- export --------------------------------------------------------------------------


def _camera(row: ManagedCamera) -> dict[str, Any]:
    source = dict(row.source)
    url = source.get("url")
    login_removed = bool(url) and _redact_url(url) != url
    if login_removed:
        source["url"] = _redact_url(url)
    return {"id": row.id, "name": row.name, "source": source,
            "segment_seconds": row.segment_seconds, "enabled": row.enabled,
            "login_removed": login_removed}


def _channel(row: NotificationChannel) -> dict[str, Any]:
    cfg = dict(row.config)
    removed = [k for k in notify.SECRET_FIELDS if cfg.get(k)]
    for key in notify.SECRET_FIELDS:
        cfg.pop(key, None)
    if cfg.get("headers"):  # e.g. Authorization
        removed.append("headers")
        cfg["headers"] = {k: REDACTED for k in cfg["headers"]}
    if cfg.get("type") == "apprise" and cfg.get("url"):
        removed.append("url")
        cfg.pop("url")
    elif cfg.get("url") and "@" in cfg["url"]:
        cfg["url"] = _redact_url(cfg["url"])
    return {"id": row.id, **cfg, "secrets_removed": removed}


def export(settings: Settings) -> dict[str, Any]:
    with session_scope() as s:
        masks = {m.id: m.rects for m in s.scalars(select(MaskVersion))}
        return {
            "config_format_version": CONFIG_FORMAT_VERSION,
            "exported_at": utcnow().isoformat() + "Z",
            "settings": runtime_settings.stored(),
            "profiles": [{"id": r.id, **r.config} for r in s.scalars(
                select(DetectionProfile).order_by(DetectionProfile.created_at))],
            "cameras": [_camera(r) for r in s.scalars(
                select(ManagedCamera).order_by(ManagedCamera.created_at))],
            "camera_settings": [{
                "camera_id": st.camera_id, "private": st.private,
                "mask": masks.get(st.mask_version_id, []) if st.mask_version_id else [],
                "profiles": st.profiles, "recording_profile": st.recording_profile,
                "burn_masks": bool(st.burn_masks),
                "motion_sensitivity": st.motion_sensitivity,
                "motion_rects": st.motion_rects or [],
            } for st in s.scalars(select(CameraState).order_by(CameraState.camera_id))],
            "notifications": [_channel(r) for r in s.scalars(
                select(NotificationChannel).order_by(NotificationChannel.created_at))],
        }


# --- import --------------------------------------------------------------------------


def upgrade(data: Any) -> dict[str, Any]:
    """The file in the current format (older ones migrated), or BackupError."""
    if not isinstance(data, dict) or "config_format_version" not in data:
        raise BackupError("not a Foxcam settings file")
    version = data["config_format_version"]
    if not isinstance(version, int) or version < 1:
        raise BackupError("not a Foxcam settings file")
    if version > CONFIG_FORMAT_VERSION:
        raise BackupError(f"made by a newer Foxcam (format {version}, this server reads up "
                          f"to {CONFIG_FORMAT_VERSION}): update this server first")
    while version < CONFIG_FORMAT_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
        data["config_format_version"] = version
    return data


class Report:
    """What an import did: counts per kind, and what was skipped or needs a look (``reason``
    is a code the UI translates; ``detail`` a validation message)."""

    def __init__(self) -> None:
        self.imported: dict[str, int] = dict.fromkeys(
            ("settings", "profiles", "cameras", "camera_settings", "notifications"), 0)
        self.skipped: list[dict[str, str]] = []
        self.attention: list[dict[str, str]] = []

    def skip(self, kind: str, item: str, reason: str, detail: str = "") -> None:
        self.skipped.append({"kind": kind, "id": item, "reason": reason, "detail": detail})

    def as_dict(self) -> dict[str, Any]:
        return {"imported": self.imported, "skipped": self.skipped,
                "attention": self.attention}


def _settings(settings: Settings, values: dict[str, Any], report: Report) -> None:
    for key, value in values.items():
        try:
            runtime_settings.update(settings, {key: value}, [])
            report.imported["settings"] += 1
        except runtime_settings.SettingError as e:
            report.skip("settings", key, "invalid", str(e))


def _profiles(settings: Settings, items: list[dict[str, Any]], report: Report) -> None:
    ui = {p.id for p in profiles.ui_profiles()}
    for item in items:
        pid = str(item.get("id", ""))
        existing = settings.profile(pid)
        if existing is not None and pid not in ui:
            report.skip("profiles", pid, "config")
            continue
        try:
            profiles.save_profile(settings, dict(item), create=existing is None)
            report.imported["profiles"] += 1
        except profiles.ProfileError as e:
            report.skip("profiles", pid, "invalid", str(e))


def _cameras(settings: Settings, items: list[dict[str, Any]], report: Report) -> None:
    ui = {c.id for c in camera_store.ui_cameras()}
    for item in items:
        cid = str(item.get("id", ""))
        try:
            if settings.camera(cid) is not None:
                if cid not in ui:
                    report.skip("cameras", cid, "config")
                    continue
                # Here already: name and options from the file, its stream (and login) kept.
                camera_store.update_camera(cid, name=item.get("name"),
                                           segment_seconds=item.get("segment_seconds"),
                                           enabled=item.get("enabled"))
            elif item.get("login_removed"):
                report.skip("cameras", cid, "login")
                continue
            else:
                camera_store.add_camera(settings, name=item["name"], camera_id=cid,
                                        source=item["source"],
                                        segment_seconds=item.get("segment_seconds"))
                if item.get("enabled") is False:
                    camera_store.update_camera(cid, enabled=False)
            report.imported["cameras"] += 1
        except (camera_store.CameraError, KeyError, ValueError) as e:
            report.skip("cameras", cid, "invalid", str(e))


def _camera_settings(settings: Settings, items: list[dict[str, Any]],
                     report: Report) -> None:
    for item in items:
        cid = str(item.get("camera_id", ""))
        try:
            save_camera_state(cid, item.get("mask") or [], bool(item.get("private")))
            save_motion(cid, item.get("motion_sensitivity"), item.get("motion_rects") or [])
            recording.set_burn_masks(cid, bool(item.get("burn_masks")))
            recording.set_camera_recording_profile(settings, cid,
                                                   item.get("recording_profile"))
            profiles.set_camera_profiles(settings, cid, item.get("profiles"))
            report.imported["camera_settings"] += 1
        except (recording.RecordingError, profiles.ProfileError) as e:
            report.skip("camera_settings", cid, "invalid", str(e))


def _notifications(settings: Settings, items: list[dict[str, Any]], report: Report) -> None:
    for item in items:
        data = {k: v for k, v in item.items() if k != "secrets_removed"}
        removed = item.get("secrets_removed") or []
        cid = str(data.get("id", ""))
        existing = notify.channel(settings, cid)
        if existing is not None and existing.managed == "config":
            report.skip("notifications", cid, "config")
            continue
        if data.get("headers") and REDACTED in data["headers"].values():
            # Header values were left out: keep this server's, or drop them.
            data["headers"] = existing.headers if existing is not None else {}
        try:
            notify.save_channel(settings, data, create=existing is None)
        except notify.NotifyError as e:
            reason = "secret" if existing is None and "url" in removed else "invalid"
            report.skip("notifications", cid, reason, str(e))
            continue
        report.imported["notifications"] += 1
        if existing is None and removed:
            report.attention.append({"kind": "notifications", "id": cid, "reason": "secret"})


def import_(settings: Settings, data: Any) -> dict[str, Any]:
    """Apply a settings file over what is here: same ids are updated, new ones added,
    nothing removed. Profiles come before cameras and their settings (which name them)."""
    data = upgrade(data)
    report = Report()
    _settings(settings, data.get("settings") or {}, report)
    _profiles(settings, data.get("profiles") or [], report)
    _cameras(settings, data.get("cameras") or [], report)
    _camera_settings(settings, data.get("camera_settings") or [], report)
    _notifications(settings, data.get("notifications") or [], report)
    return report.as_dict()
