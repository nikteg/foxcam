"""Tunables an admin can change in the web UI (Settings page), without a restart.

Like ``recording_profile_default`` (recording.py): each value set in the UI is one
``AppSetting`` row, keyed by its dotted path in config.yaml (e.g. ``processing.sample_fps``),
and wins over config.yaml until reset. Values are validated with the bounds of the
pydantic models in config.py.

Every process (server, ingest, compaction) keeps one ``Settings`` object and reads it
everywhere, so the database values are applied onto that object in place: ``refresh``
is called by the background loops (at most every few seconds), and right away by the
API after a change. What config.yaml said is remembered when a value is first replaced,
so a reset brings it back.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select

from .config import Settings
from .db import db_ready, session_scope
from .models import AppSetting

log = logging.getLogger(__name__)

# Safe to change while running: read each time they are used. Left out on purpose:
# compaction.period_minutes (archive boundaries), the mediamtx encoder (every transcoder
# would restart), secrets, storage and the rest of auth (sign-in itself).
EDITABLE: tuple[str, ...] = (
    "public_url",
    "segment_seconds",
    "compaction.enabled",
    "compaction.delay_minutes",
    "processing.sample_fps",
    "processing.max_width",
    "processing.night_saturation_threshold",
    "processing.concurrency",
    "processing.keep_non_targets_from",
    "mediamtx.low_data.enabled",
    "mediamtx.low_data.height",
    "mediamtx.low_data.fps",
    "mediamtx.low_data.bitrate_kbit",
    "events.pre_roll_seconds",
    "events.post_roll_seconds",
    "events.merge_gap_seconds",
    "events.settle_seconds",
    "motion.sensitivity",
    "motion.light_change_fraction",
    "motion.window_seconds",
    "queue.lease_seconds",
    "queue.max_attempts",
    "queue.retry_backoff_seconds",
    "retention.segments_days",
    "retention.unprocessed_max_days",
    "retention.events_days",
    "retention.max_disk_gb",
    "notifications.timezone",
    "notifications.max_age_minutes",
    "location.latitude",
    "location.longitude",
    "auth.users_can_add_cameras",
    "auth.max_cameras_per_user",
)

# Part of MediaMTX's path settings: pushed to it when they change (mediamtx.sync_paths).
MEDIAMTX_KEYS = frozenset({"segment_seconds", "mediamtx.low_data.enabled",
                           "mediamtx.low_data.height", "mediamtx.low_data.fps",
                           "mediamtx.low_data.bitrate_kbit"})

REFRESH_SECONDS = 5.0


class SettingError(ValueError):
    pass


def _owner(settings: Settings, path: str) -> tuple[BaseModel, str]:
    """The model holding ``path``'s value and the field name in it."""
    *parents, name = path.split(".")
    model: BaseModel = settings
    for part in parents:
        model = getattr(model, part)
    return model, name


def _adapter(settings: Settings, path: str) -> TypeAdapter[Any]:
    model, name = _owner(settings, path)
    field = type(model).model_fields[name]
    if not field.metadata:
        return TypeAdapter(field.annotation)
    return TypeAdapter(Annotated[field.annotation, *field.metadata])


def _validate(settings: Settings, path: str, value: Any) -> Any:
    if path not in EDITABLE:
        raise SettingError(f"'{path}' cannot be changed in the web UI")
    if path == "public_url" and isinstance(value, str):
        value = value.strip().rstrip("/") or None
    try:
        return _adapter(settings, path).validate_python(value)
    except ValidationError as e:
        msg = e.errors()[0]["msg"]
        raise SettingError(f"{path}: {msg}") from None


def _config_value(settings: Settings, path: str) -> tuple[Any, bool]:
    """What config.yaml (or the built-in default) says: the value and whether the file set it."""
    original = settings._runtime.get("original", {})
    if path in original:
        return original[path]
    model, name = _owner(settings, path)
    return getattr(model, name), name in model.model_fields_set


def stored() -> dict[str, Any]:
    """The values set in the web UI."""
    with session_scope() as s:
        rows = s.scalars(select(AppSetting).where(AppSetting.key.in_(EDITABLE))).all()
        return {r.key: r.value for r in rows}


def refresh(settings: Settings, force: bool = False) -> set[str]:
    """Apply the database values onto ``settings``; put config.yaml's value back where one
    was reset. Returns the paths whose value changed. Cheap: at most one query every few
    seconds. Values nobody changed in the UI are never touched."""
    rt = settings._runtime
    original: dict[str, tuple[Any, bool]] = rt.setdefault("original", {})
    now = time.monotonic()
    if not force and now - rt.get("refreshed", float("-inf")) < REFRESH_SECONDS:
        return set()
    rt["refreshed"] = now
    if not db_ready():
        return set()
    ui = stored()
    changed = set()
    for path in EDITABLE:
        if path in ui:
            try:
                value = _validate(settings, path, ui[path])
            except SettingError as e:  # e.g. bounds tightened since it was saved
                log.warning("ignoring the web UI's %s", e)
                continue
            if path not in original:
                original[path] = _config_value(settings, path)
        elif path in original:
            value, in_file = original.pop(path)
            model, name = _owner(settings, path)
            if getattr(model, name) != value:
                setattr(model, name, value)
                changed.add(path)
            if not in_file:  # a built-in default again, not "set in config.yaml"
                model.model_fields_set.discard(name)
            continue
        else:
            continue
        model, name = _owner(settings, path)
        if getattr(model, name) != value:
            setattr(model, name, value)
            changed.add(path)
    if changed:
        log.info("settings from the web UI: %s", ", ".join(sorted(changed)))
    return changed


def update(settings: Settings, values: dict[str, Any], reset: list[str]) -> set[str]:
    """Save values (validated) and remove reset ones (back to config.yaml), then apply."""
    checked = {path: _validate(settings, path, v) for path, v in values.items()}
    for path in reset:
        if path not in EDITABLE:
            raise SettingError(f"'{path}' cannot be changed in the web UI")
    with session_scope() as s:
        for path, value in checked.items():
            row = s.get(AppSetting, path)
            if row is None:
                s.add(AppSetting(key=path, value=value))
            else:
                row.value = value
        for path in reset:
            if (row := s.get(AppSetting, path)) is not None:
                s.delete(row)
    return refresh(settings, force=True)


def describe(settings: Settings) -> list[dict[str, Any]]:
    """Each setting: its value now, config.yaml's, and where the value comes from."""
    ui = stored()
    out = []
    for path in EDITABLE:
        model, name = _owner(settings, path)
        field = type(model).model_fields[name]
        config_value, in_file = _config_value(settings, path)
        bounds = {k: getattr(m, k) for m in field.metadata
                  for k in ("ge", "gt", "le", "lt") if getattr(m, k, None) is not None}
        out.append({
            "key": path,
            "value": getattr(model, name),
            "config_value": config_value,
            "source": "ui" if path in ui else "config" if in_file else "default",
            # Where a reset goes back to.
            "config_source": "config" if in_file else "default",
            "nullable": _accepts_none(settings, path),
            **bounds,
        })
    return out


def _accepts_none(settings: Settings, path: str) -> bool:
    try:
        _adapter(settings, path).validate_python(None)
    except ValidationError:
        return False
    return True
