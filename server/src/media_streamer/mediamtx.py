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


LOW_SUFFIX = "/low"


def low_path(camera_id: str) -> str:
    return f"{camera_id}{LOW_SUFFIX}"


def encoder_args(encoder: str, bitrate_kbit: int, fps: int) -> list[str]:
    """Live-friendly encoder settings (no B-frames, a keyframe every 2 s)."""
    rate = ["-b:v", f"{bitrate_kbit}k", "-maxrate", f"{bitrate_kbit}k",
            "-bufsize", f"{bitrate_kbit * 2}k", "-g", str(fps * 2), "-bf", "0"]
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", *rate]
    if encoder.endswith("_videotoolbox"):
        return ["-c:v", encoder, "-realtime", "1", "-allow_sw", "1", *rate]
    return ["-c:v", encoder, *rate]


def _low_conf(settings: Settings, cam: CameraConfig) -> dict[str, Any]:
    """`<camera>/low`: transcoded from the camera's path only while someone watches it."""
    low = settings.mediamtx.low_data
    # MediaMTX runs this itself (no shell); $RTSP_PORT and $MTX_PATH are filled in by it.
    cmd = [low.ffmpeg, "-v", "error", "-rtsp_transport", "tcp",
           "-i", f"rtsp://localhost:$RTSP_PORT/{cam.id}", "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", f"fps={low.fps},scale=-2:{low.height},format=yuv420p",
           *encoder_args(low.encoder, low.bitrate_kbit, low.fps),
           # Opus is what browsers play over WebRTC.
           "-c:a", "libopus", "-b:a", "24k", "-ac", "1", "-ar", "48000",
           "-f", "rtsp", "-rtsp_transport", "tcp", "rtsp://localhost:$RTSP_PORT/$MTX_PATH"]
    return {"source": "publisher", "record": False, "runOnDemand": " ".join(cmd),
            "runOnDemandRestart": True, "runOnDemandStartTimeout": "20s",
            "runOnDemandCloseAfter": "15s"}


def desired_paths(settings: Settings) -> dict[str, dict[str, Any]]:
    """Every MediaMTX path this server manages, by name."""
    out: dict[str, dict[str, Any]] = {}
    for cam in settings.all_cameras():
        out[cam.id] = _path_conf(settings, cam)
        if settings.mediamtx.low_data.enabled:
            out[low_path(cam.id)] = _low_conf(settings, cam)
    return out


class MediaMtx:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.mediamtx.api_url, timeout=5)

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
                    await self.client.post(f"/v3/config/paths/delete/{name}")
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
