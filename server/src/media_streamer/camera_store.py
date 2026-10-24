"""Cameras added from the web UI. Stored in SQLite and merged with config.yaml cameras
by ``Settings.all_cameras()``; the server and the ingest process both read from here, so
changes take effect without a restart."""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from .config import CameraConfig, Settings, Source
from .db import db_ready, session_scope
from .models import Archive, CameraState, Event, Invite, ManagedCamera, MaskVersion, Segment


class CameraError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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


def used_ids() -> set[str]:
    """Ids with history: recordings, events or settings of a removed camera, or open
    invites. A new camera with such an id would inherit them."""
    if not db_ready():
        return set()
    out: set[str] = set()
    with session_scope() as s:
        for model in (Segment, Archive, Event, CameraState, MaskVersion):
            out.update(s.scalars(select(model.camera_id).distinct()))
        for cams in s.scalars(select(Invite.cameras).where(Invite.used_at.is_(None))):
            out.update(c["camera_id"] for c in cams or [])
    return out


def unique_id(settings: Settings, wanted: str) -> str:
    taken = {c.id for c in settings.all_cameras(include_disabled=True)} | used_ids()
    base = slugify(wanted)
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def add_camera(settings: Settings, *, name: str, source: dict[str, Any],
               camera_id: str | None = None, segment_seconds: int | None = None,
               admin: bool = True) -> CameraConfig:
    """``admin``: added by an admin. Others may only add RTSP cameras elsewhere on the
    network (see check_source)."""
    cam_id = slugify(camera_id) if camera_id else unique_id(settings, name)
    if settings.camera(cam_id):
        raise CameraError(f"a camera with id '{cam_id}' already exists")
    if camera_id and not admin and cam_id in used_ids():
        # Admins may reuse it on purpose, to continue a removed camera's history.
        raise CameraError(f"the id '{cam_id}' belonged to a removed camera whose recordings "
                          "are kept; pick another id", 409)
    # Validate through the same model config.yaml uses.
    cam = CameraConfig.model_validate({"id": cam_id, "name": name, "source": source,
                                       "segment_seconds": segment_seconds, "managed": "ui"})
    check_source(settings, cam.source, admin)
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


def check_source(settings: Settings, source: Source, admin: bool) -> None:
    """Refuses sources the user may not add. Test patterns and sample files run ffmpeg on
    this server, so they are for admins (and tapo_cloud uses the server's account, see
    api_cameras). An RTSP camera added by anyone else must be somewhere else on the
    network: not this machine (MediaMTX, the server), not link-local addresses (cloud
    metadata services)."""
    if source.type in ("test", "file") and not admin:
        raise CameraError("only admins can add test cameras and sample files", 403)
    if source.type == "file":
        # The UI may only loop files from the samples folder, not arbitrary server paths.
        path = (settings.samples_dir / source.path).resolve()
        if not path.is_relative_to(settings.samples_dir) or not path.is_file():
            raise CameraError("file sources must be a video in the samples folder")
        source.path = str(path)
    if source.type == "rtsp":
        if not source.url.startswith(("rtsp://", "rtsps://")):
            raise CameraError("RTSP URLs start with rtsp:// or rtsps://")
        if not admin:
            _check_host(settings, source.url)


def _addresses(host: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return set()
    out = set()
    for info in infos:
        addr = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        out.add(addr)
    return out


def _own_addresses(settings: Settings) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """This machine and MediaMTX's (its RTSP / API hosts, e.g. a Docker service name)."""
    hosts = {socket.gethostname(), "localhost"}
    for u in (settings.mediamtx.rtsp_url, settings.mediamtx.api_url,
              settings.mediamtx.webrtc_url, settings.mediamtx.hls_url):
        if h := urlsplit(u).hostname:
            hosts.add(h)
    return set().union(*(_addresses(h) for h in hosts))


def _check_host(settings: Settings, url: str) -> None:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    if not host:
        raise CameraError("the RTSP URL needs a host")
    addrs = _addresses(host)
    if not addrs:
        raise CameraError(f"cannot resolve {host}")
    own = _own_addresses(settings)
    for a in addrs:
        if a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast \
                or a.is_reserved or a in own:
            raise CameraError("cameras on this server itself (or link-local addresses) "
                              "can only be added by an admin", 403)


def sample_files(settings: Settings) -> list[str]:
    if not settings.samples_dir.is_dir():
        return []
    exts = {".mp4", ".mkv", ".mov", ".webm", ".ts"}
    return sorted(p.name for p in settings.samples_dir.iterdir() if p.suffix.lower() in exts)
