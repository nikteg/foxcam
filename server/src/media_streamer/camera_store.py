"""Cameras added from the web UI. Stored in SQLite and merged with config.yaml cameras
by ``Settings.all_cameras()``; the server and the ingest process both read from here, so
changes take effect without a restart."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import select

from .config import CameraConfig, Settings
from .db import db_ready, session_scope
from .models import ManagedCamera


class CameraError(ValueError):
    pass


def ui_cameras() -> list[CameraConfig]:
    if not db_ready():
        return []
    with session_scope() as s:
        rows = s.scalars(select(ManagedCamera).order_by(ManagedCamera.created_at)).all()
        return [_to_config(r) for r in rows]


def _to_config(row: ManagedCamera) -> CameraConfig:
    return CameraConfig.model_validate({
        "id": row.id, "name": row.name, "source": row.source,
        "segment_seconds": row.segment_seconds, "enabled": row.enabled, "managed": "ui",
    })


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "camera"


def unique_id(settings: Settings, wanted: str) -> str:
    taken = {c.id for c in settings.all_cameras(include_disabled=True)}
    base = slugify(wanted)
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def add_camera(settings: Settings, *, name: str, source: dict[str, Any],
               camera_id: str | None = None, segment_seconds: int | None = None) -> CameraConfig:
    cam_id = slugify(camera_id) if camera_id else unique_id(settings, name)
    if settings.camera(cam_id):
        raise CameraError(f"a camera with id '{cam_id}' already exists")
    # Validate through the same model config.yaml uses.
    cam = CameraConfig.model_validate({"id": cam_id, "name": name, "source": source,
                                       "segment_seconds": segment_seconds, "managed": "ui"})
    _check_source(settings, cam)
    with session_scope() as s:
        s.add(ManagedCamera(id=cam.id, name=name, source=cam.source.model_dump(),
                            segment_seconds=segment_seconds, enabled=True))
    return cam


def update_camera(camera_id: str, *, name: str | None = None,
                  segment_seconds: int | None | type[...] = ...,
                  enabled: bool | None = None) -> CameraConfig:
    with session_scope() as s:
        row = s.get(ManagedCamera, camera_id)
        if row is None:
            raise CameraError("only cameras added in the web UI can be edited here")
        if name is not None:
            row.name = name
        if segment_seconds is not ...:
            row.segment_seconds = segment_seconds
        if enabled is not None:
            row.enabled = enabled
        cam = _to_config(row)
    return cam


def delete_camera(camera_id: str) -> None:
    """Removes the camera; its recordings, detections and events are kept."""
    with session_scope() as s:
        row = s.get(ManagedCamera, camera_id)
        if row is None:
            raise CameraError("only cameras added in the web UI can be removed here")
        s.delete(row)


def _check_source(settings: Settings, cam: CameraConfig) -> None:
    if cam.source.type == "file":
        # The UI may only loop files from the samples folder, not arbitrary server paths.
        path = (settings.samples_dir / cam.source.path).resolve()
        if not path.is_relative_to(settings.samples_dir) or not path.is_file():
            raise CameraError("file sources must be a video in the samples folder")
        cam.source.path = str(path)
    if cam.source.type == "rtsp" and not cam.source.url.startswith(("rtsp://", "rtsps://")):
        raise CameraError("RTSP URLs start with rtsp:// or rtsps://")


def sample_files(settings: Settings) -> list[str]:
    if not settings.samples_dir.is_dir():
        return []
    exts = {".mp4", ".mkv", ".mov", ".webm", ".ts"}
    return sorted(p.name for p in settings.samples_dir.iterdir() if p.suffix.lower() in exts)
