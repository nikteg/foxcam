"""MediaMTX control-API client.

The server is the source of truth for cameras and segment length; it pushes path
configuration to MediaMTX on startup so ``mediamtx.yml`` stays generic.
"""

from __future__ import annotations

import logging
import re
import shlex
from typing import Any

import httpx

from . import mediamtx_auth, recording
from .config import CameraConfig, Settings
from .hwaccel import Encode

log = logging.getLogger(__name__)

# MediaMTX writes local time; %z (e.g. +0200) makes the name unambiguous. TZ=UTC is not
# enough: Go ignores TZ on Windows.
RECORD_NAME = "%Y-%m-%d_%H-%M-%S-%f%z"


def _record_conf(settings: Settings, cam: CameraConfig) -> dict[str, Any]:
    return {
        "record": True,
        "recordFormat": "fmp4",
        "recordPath": f"{settings.mediamtx.record_dir}/%path/{RECORD_NAME}",
        "recordSegmentDuration": f"{settings.segment_seconds_for(cam.id)}s",
        # Retention is handled by this server, never by MediaMTX.
        "recordDeleteAfter": "0s",
    }


def _source_conf(cam: CameraConfig) -> dict[str, Any]:
    if cam.source.type == "rtsp":
        return {"source": cam.source.url, "rtspTransport": "tcp", "sourceOnDemand": False}
    # tapo_cloud / test / file sources are published by `foxcam ingest`.
    return {"source": "publisher"}


def command(args: list[str]) -> str:
    """A command line for MediaMTX's run* hooks (split shell-style by MediaMTX, which
    replaces $RTSP_PORT / $MTX_PATH first)."""
    return " ".join(a if re.fullmatch(r"[\w$:/.,=?@%+-]+", a) else shlex.quote(a)
                    for a in args)


def _path_conf(settings: Settings, cam: CameraConfig,
               proc: recording.Processing | None) -> dict[str, Any]:
    conf = _record_conf(settings, cam)
    if proc is None:
        conf.update(_source_conf(cam))
    else:
        conf.update(source="publisher",
                    runOnInit=command(recording.processor_args(settings, cam, proc)),
                    runOnInitRestart=True)
    return conf


LOW_SUFFIX = "/low"


def low_path(camera_id: str) -> str:
    return f"{camera_id}{LOW_SUFFIX}"


def _low_conf(settings: Settings, cam: CameraConfig) -> dict[str, Any]:
    """`<camera>/low`: transcoded from the camera's path only while someone watches it."""
    low = settings.mediamtx.low_data
    enc = Encode(max_height=low.height, fps=low.fps, bitrate_kbit=low.bitrate_kbit,
                 audio="opus")  # Opus is what browsers play over WebRTC
    args = recording.transcode(settings, enc, cam.id)
    return {"source": "publisher", "record": False, "runOnDemand": command(args),
            "runOnDemandRestart": True, "runOnDemandStartTimeout": "20s",
            "runOnDemandCloseAfter": "15s"}


def desired_paths(settings: Settings) -> dict[str, dict[str, Any]]:
    """Every MediaMTX path this server manages, by name."""
    out: dict[str, dict[str, Any]] = {}
    for cam in settings.all_cameras():
        proc = recording.processing(settings, cam)
        out[cam.id] = _path_conf(settings, cam, proc)
        if proc is not None:
            out[recording.raw_path(cam.id)] = {**_source_conf(cam), "record": False}
        if settings.mediamtx.low_data.enabled:
            out[low_path(cam.id)] = _low_conf(settings, cam)
    return out


class MediaMtx:
    def __init__(self, settings: Settings):
        self.settings = settings
        # The control API needs the internal login too (mediamtx_auth.py).
        self.client = httpx.AsyncClient(base_url=settings.mediamtx.api_url, timeout=5,
                                        auth=mediamtx_auth.api_auth(settings))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def sync_paths(self) -> None:
        """Make MediaMTX's path list match the enabled cameras (adds, updates, removes)."""
        wanted = desired_paths(self.settings)
        listed = await self.client.get("/v3/config/paths/list", params={"itemsPerPage": 1000})
        if listed.status_code == 200:
            for item in listed.json().get("items", []):
                name = item.get("name")
                if name and name != "all_others" and name not in wanted:
                    r = await self.client.delete(f"/v3/config/paths/delete/{name}")
                    if r.status_code >= 400:
                        log.error("mediamtx: removing path %s failed: %s %s", name,
                                  r.status_code, r.text)
                        continue
                    log.info("mediamtx: path %s removed", name)
        for name, conf in wanted.items():
            existing = await self.client.get(f"/v3/config/paths/get/{name}")
            if existing.status_code == 200:
                r = await self.client.post(f"/v3/config/paths/replace/{name}", json=conf)
            else:
                r = await self.client.post(f"/v3/config/paths/add/{name}", json=conf)
            if r.status_code >= 400:
                log.error("mediamtx: configuring path %s failed: %s %s", name,
                          r.status_code, r.text)
            else:
                log.info("mediamtx: path %s configured", name)

    async def paths(self) -> dict[str, dict[str, Any]]:
        """Live status per path name. Empty dict if MediaMTX is unreachable."""
        try:
            r = await self.client.get("/v3/paths/list", params={"itemsPerPage": 1000})
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.debug("mediamtx unreachable: %s", e)
            return {}
        return {item["name"]: item for item in r.json().get("items", [])}
