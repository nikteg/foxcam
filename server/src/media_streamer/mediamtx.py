"""MediaMTX control-API client.

The server is the source of truth for cameras and segment length; it pushes path
configuration to MediaMTX on startup so ``mediamtx.yml`` stays generic.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import CameraConfig, Settings

log = logging.getLogger(__name__)

# MediaMTX writes local time; %z (e.g. +0200) makes the name unambiguous. TZ=UTC is not
# enough: Go ignores TZ on Windows.
RECORD_NAME = "%Y-%m-%d_%H-%M-%S-%f%z"


def _path_conf(settings: Settings, cam: CameraConfig) -> dict[str, Any]:
    conf: dict[str, Any] = {
        "record": True,
        "recordFormat": "fmp4",
        "recordPath": f"{settings.mediamtx.record_dir}/%path/{RECORD_NAME}",
        "recordSegmentDuration": f"{settings.segment_seconds_for(cam.id)}s",
        # Retention is handled by this server, never by MediaMTX.
        "recordDeleteAfter": "0s",
    }
    if cam.source.type == "rtsp":
        conf.update(source=cam.source.url, rtspTransport="tcp", sourceOnDemand=False)
    else:
        # tapo_cloud / test / file sources are published by `media-streamer ingest`.
        conf["source"] = "publisher"
    return conf


class MediaMtx:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.mediamtx.api_url, timeout=5)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def sync_paths(self) -> None:
        """Make MediaMTX's path list match the enabled cameras (adds, updates, removes)."""
        wanted = {cam.id for cam in self.settings.all_cameras()}
        listed = await self.client.get("/v3/config/paths/list", params={"itemsPerPage": 1000})
        if listed.status_code == 200:
            for item in listed.json().get("items", []):
                name = item.get("name")
                if name and name != "all_others" and name not in wanted:
                    await self.client.post(f"/v3/config/paths/delete/{name}")
                    log.info("mediamtx: path %s removed", name)
        for cam in self.settings.all_cameras():
            conf = _path_conf(self.settings, cam)
            existing = await self.client.get(f"/v3/config/paths/get/{cam.id}")
            if existing.status_code == 200:
                r = await self.client.post(f"/v3/config/paths/replace/{cam.id}", json=conf)
            else:
                r = await self.client.post(f"/v3/config/paths/add/{cam.id}", json=conf)
            if r.status_code >= 400:
                log.error("mediamtx: configuring path %s failed: %s %s", cam.id,
                          r.status_code, r.text)
            else:
                log.info("mediamtx: path %s configured (%s)", cam.id, cam.source.type)

    async def paths(self) -> dict[str, dict[str, Any]]:
        """Live status per path name. Empty dict if MediaMTX is unreachable."""
        try:
            r = await self.client.get("/v3/paths/list", params={"itemsPerPage": 1000})
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.debug("mediamtx unreachable: %s", e)
            return {}
        return {item["name"]: item for item in r.json().get("items", [])}
