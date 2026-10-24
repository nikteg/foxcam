"""Configuration: a YAML file (``config.yaml``) with ``${ENV_VAR}`` expansion.

Camera definitions and tunables live here. Runtime-editable state (privacy masks,
private toggle) lives in the database and is edited from the web UI.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        value = os.environ.get(name)
        if value is None:
            if default is None:
                raise ValueError(f"config references ${{{name}}} but it is not set")
            return default
        return value

    return _ENV_RE.sub(repl, text)


class RtspSource(BaseModel):
    """MediaMTX pulls the RTSP stream directly (camera on LAN or reachable over VPN)."""

    type: Literal["rtsp"]
    url: str


class TapoCloudSource(BaseModel):
    """EXPERIMENTAL: pull the live view through the TP-Link cloud relay via ``ontapo``."""

    type: Literal["tapo_cloud"]
    device_name: str | None = None
    device_id: str | None = None
    resolution: Literal["HD", "VGA"] = "HD"
    # Each relay session is restarted after this many seconds (keeps tokens fresh).
    session_seconds: int = 1800


class TestSource(BaseModel):
    """Synthetic ffmpeg test pattern. For development without cameras."""

    type: Literal["test"]
    size: str = "1280x720"
    fps: int = 15


class FileSource(BaseModel):
    """Loop a local video file as if it were a live camera (e.g. a fox clip for testing)."""

    type: Literal["file"]
    path: str


Source = Annotated[
    RtspSource | TapoCloudSource | TestSource | FileSource, Field(discriminator="type")
]


class CameraConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str | None = None
    source: Source
    segment_seconds: int | None = Field(default=None, ge=5, le=3600)  # overrides the global
    enabled: bool = True
    # "config" = defined in config.yaml (read-only in the UI), "ui" = added in the web UI.
    managed: Literal["config", "ui"] = "config"

    @property
    def display_name(self) -> str:
        return self.name or self.id


class MediaMtxConfig(BaseModel):
    api_url: str = "http://localhost:9997"
    # RTSP URL prefix used by ingest publishers and for snapshots (as seen by this server).
    rtsp_url: str = "rtsp://localhost:8554"
    # URLs as seen by the *browser* for live view.
    webrtc_public_url: str = "http://localhost:8889"
    hls_public_url: str = "http://localhost:8888"
    # Recording directory as seen by the MediaMTX process. Defaults to ``recordings_dir``.
    record_dir: str | None = None


class ProcessingConfig(BaseModel):
    # Frames per second kept in the masked derivative handed to workers.
    sample_fps: float = 2.0
    max_width: int = 1280
    concurrency: int = 2
    # Mean saturation below this => frame considered grayscale (IR night mode).
    night_saturation_threshold: float = 4.0


class QueueConfig(BaseModel):
    lease_seconds: int = 300
    max_attempts: int = 3
    retry_backoff_seconds: int = 60


class DetectionConfig(BaseModel):
    target_labels: list[str] = ["fox", "red fox", "vulpes vulpes"]
    min_confidence: float = 0.5


class EventsConfig(BaseModel):
    pre_roll_seconds: int = 10
    post_roll_seconds: int = 10
    # Positive segments closer than this are merged into one event.
    merge_gap_seconds: int = 30
    # Wait this long after the last change before (re)building the clip.
    settle_seconds: int = 90


class RetentionConfig(BaseModel):
    # Delete ordinary segments after this many days. 0 disables age-based deletion.
    segments_days: float = 7
    # Segments still waiting for a worker are kept up to this many days.
    unprocessed_max_days: float = 14
    # Delete fox events (clip + pinned segments) after this many days. 0 = keep forever.
    events_days: float = 0
    # Hard cap on recordings disk usage; oldest unpinned segments go first.
    max_disk_gb: float | None = None
    # Remove the masked worker input once the job is finished.
    delete_derived_after_done: bool = True


class LocationConfig(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    # ISO 3166-1 alpha-3, passed to workers as a hint (SpeciesNet geofencing).
    country: str | None = None


class Settings(BaseModel):
    data_dir: Path = Path("./data")
    recordings_dir: Path | None = None
    database_url: str | None = None
    segment_seconds: int = 60
    # Optional shared secret for worker endpoints (``Authorization: Bearer ...``).
    worker_token: str | None = None
    tapo_session_file: Path | None = None
    # Video files the UI may offer as "file" camera sources (fake cameras for testing).
    samples_dir: Path = Path("./samples")

    cameras: list[CameraConfig] = []
    mediamtx: MediaMtxConfig = MediaMtxConfig()
    processing: ProcessingConfig = ProcessingConfig()
    queue: QueueConfig = QueueConfig()
    detection: DetectionConfig = DetectionConfig()
    events: EventsConfig = EventsConfig()
    retention: RetentionConfig = RetentionConfig()
    location: LocationConfig = LocationConfig()

    def model_post_init(self, _ctx: object) -> None:
        self.data_dir = self.data_dir.resolve()
        if self.recordings_dir is None:
            self.recordings_dir = self.data_dir / "recordings"
        self.recordings_dir = self.recordings_dir.resolve()
        if self.database_url is None:
            self.database_url = f"sqlite:///{self.data_dir / 'media-streamer.db'}"
        if self.tapo_session_file is None:
            self.tapo_session_file = self.data_dir / "tapo-session.json"
        if not self.worker_token:
            self.worker_token = None
        if not self.mediamtx.record_dir:
            self.mediamtx.record_dir = str(self.recordings_dir)
        self.samples_dir = self.samples_dir.resolve()
        ids = [c.id for c in self.cameras]
        if len(ids) != len(set(ids)):
            raise ValueError("camera ids must be unique")

    # Derived directories ---------------------------------------------------
    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.recordings_dir, self.derived_dir, self.thumbs_dir,
                  self.clips_dir):
            d.mkdir(parents=True, exist_ok=True)

    def all_cameras(self, include_disabled: bool = False) -> list[CameraConfig]:
        """Cameras from config.yaml plus the ones added in the web UI (stored in the DB)."""
        from .camera_store import ui_cameras  # deferred: camera_store imports the DB layer

        cams = [*self.cameras, *ui_cameras()]
        return cams if include_disabled else [c for c in cams if c.enabled]

    def camera(self, camera_id: str) -> CameraConfig | None:
        return next((c for c in self.all_cameras(include_disabled=True) if c.id == camera_id),
                    None)

    def segment_seconds_for(self, camera_id: str) -> int:
        cam = self.camera(camera_id)
        return (cam.segment_seconds if cam and cam.segment_seconds else self.segment_seconds)


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    path = Path(path or os.environ.get("MEDIA_STREAMER_CONFIG", "config.yaml"))
    if not path.exists():
        raise FileNotFoundError(
            f"config file {path} not found (copy config.example.yaml to config.yaml)"
        )
    raw = yaml.safe_load(_expand_env(path.read_text())) or {}
    return Settings.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return load_settings()
