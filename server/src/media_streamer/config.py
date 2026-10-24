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

    # Comment lines are left alone so examples like "# ${TAPO_USER}" need no variable.
    return "".join(
        line if line.lstrip().startswith("#") else _ENV_RE.sub(repl, line)
        for line in text.splitlines(keepends=True)
    )


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
    # Start a fresh relay session after this long (0 = only when the relay ends it).
    session_seconds: int = 0
    # Heartbeat interval on the relay connection, like the Tapo app (0 = off: the relay
    # then closes the session after ~2 minutes and we reconnect).
    heartbeat_seconds: float = 20


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
    # Detection profiles to run on this camera. None = every profile marked `default`.
    # The web UI can override this per camera.
    profiles: list[str] | None = None
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
    """Used for the built-in `default` profile when `profiles:` is not configured."""

    target_labels: list[str] = ["fox", "red fox", "vulpes vulpes"]
    min_confidence: float = 0.5


PROFILE_ID = r"^[a-z0-9][a-z0-9_-]*$"
TAG = r"^[a-z0-9][a-z0-9_.-]*$"
DEFAULT_PROFILE = "default"


class ProfileConfig(BaseModel):
    """What to look for, and which workers can look for it.

    Every segment of a camera gets one job per profile. Only workers that have all
    `requires` tags lease a profile's jobs. Detections whose (aliased) label is in
    `targets` with at least `min_confidence` become events.
    """

    id: str = Field(pattern=PROFILE_ID, max_length=40)
    name: str | None = None
    targets: list[str] = Field(min_length=1)
    # Map model labels to one name, e.g. {"red fox": "fox", "vulpes vulpes": "fox"}.
    aliases: dict[str, str] = {}
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    requires: list[Annotated[str, Field(pattern=TAG)]] = []
    # Frames per second the worker analyses (≤ processing.sample_fps). None = all.
    fps: float | None = Field(default=None, gt=0)
    # Run on cameras that have no explicit profile selection.
    default: bool = False
    # Delete this profile's events after N days (None = retention.events_days).
    events_days: float | None = None
    enabled: bool = True
    managed: Literal["config", "ui"] = "config"

    @property
    def display_name(self) -> str:
        return self.name or self.id

    def canonical(self, label: str) -> str:
        aliases = {k.lower(): v for k, v in self.aliases.items()}
        return aliases.get(label.lower(), label)

    def is_target(self, label: str, confidence: float) -> bool:
        return (confidence >= self.min_confidence
                and self.canonical(label).lower() in {t.lower() for t in self.targets})


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


class S3Config(BaseModel):
    """Any S3-compatible object store: AWS S3, Cloudflare R2, Backblaze B2, MinIO, Hetzner…"""

    enabled: bool = False
    bucket: str = ""
    # None = AWS. Otherwise e.g. http://minio:9000 or https://<account>.r2.cloudflarestorage.com
    endpoint_url: str | None = None
    region: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    # Key prefix inside the bucket (lets several installations share a bucket).
    prefix: str = "media-streamer/"
    # "path" is needed by MinIO and most self-hosted stores; "auto" lets boto3 decide.
    addressing_style: Literal["auto", "path", "virtual"] = "auto"
    # What to upload.
    clips: bool = True  # fox event clips + thumbnails
    recordings: bool = False  # every finished segment (archive)
    # Remove the local clip/thumbnail once uploaded (served from S3 afterwards).
    # Recordings always stay local until retention (`segments_days`) removes them.
    delete_local_clips: bool = False
    # Delete archived recordings from the bucket after this many days. 0 = keep (use a
    # bucket lifecycle rule instead).
    recordings_days: float = 0
    # Lifetime of the signed download links the UI redirects to.
    presign_seconds: int = 3600


class StorageConfig(BaseModel):
    s3: S3Config = S3Config()


NOTIFY_TRIGGERS = ("event.new", "event.clip")
_HHMM = r"([01]\d|2[0-3]):[0-5]\d"


class ChannelConfig(BaseModel):
    """Where to send event notifications (see docs/NOTIFICATIONS.md).

    - ``ntfy``: push via an ntfy server (ntfy.sh or self-hosted), image attached.
    - ``webhook``: POST JSON to any URL (Home Assistant, n8n, your code), optionally signed.
    - ``apprise``: any of ~100 services (Telegram, Discord, Pushover, Gotify, Slack, e-mail,
      …) through an Apprise URL. Needs the ``notify`` extra.
    """

    id: str = Field(pattern=PROFILE_ID, max_length=40)
    name: str | None = None
    type: Literal["ntfy", "webhook", "apprise"]
    enabled: bool = True
    # event.new = as soon as an event starts; event.clip = when its clip is ready.
    # (Not called "on": YAML reads a bare `on:` key as the boolean true.)
    when: list[Literal["event.new", "event.clip"]] = Field(default=["event.new"], min_length=1)
    # Filters; None = all.
    cameras: list[str] | None = None
    profiles: list[str] | None = None
    labels: list[str] | None = None
    min_confidence: float = Field(default=0, ge=0, le=1)
    # e.g. "22:00-07:00": no notifications in that window (notifications.timezone).
    quiet_hours: str | None = Field(default=None, pattern=rf"^{_HHMM}-{_HHMM}$")
    # At most one notification per camera within this many minutes.
    cooldown_minutes: float = Field(default=0, ge=0)
    # Attach the frame with the detection box (censor areas blacked out).
    include_image: bool = True
    # ntfy
    server: str = "https://ntfy.sh"
    topic: str | None = None
    token: str | None = None
    username: str | None = None
    password: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    tags: list[str] = []
    # webhook (url) / apprise (url)
    url: str | None = None
    # webhook: HMAC-SHA256 of the body in X-Media-Streamer-Signature: sha256=<hex>
    secret: str | None = None
    headers: dict[str, str] = {}
    managed: Literal["config", "ui"] = "config"

    @property
    def display_name(self) -> str:
        return self.name or self.id

    def model_post_init(self, _ctx: object) -> None:
        if self.type == "ntfy" and not self.topic:
            raise ValueError("ntfy channels need a topic")
        if self.type in ("webhook", "apprise") and not self.url:
            raise ValueError(f"{self.type} channels need a url")
        if self.type == "webhook" and not self.url.startswith(("http://", "https://")):
            raise ValueError("webhook url must start with http:// or https://")


class NotificationsConfig(BaseModel):
    channels: list[ChannelConfig] = []
    # IANA zone for quiet hours, e.g. Europe/Stockholm. None = the server's local time.
    timezone: str | None = None


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
    # Let any client act as a worker without a token. Only for a trusted test network.
    open_worker_access: bool = False
    tapo_session_file: Path | None = None
    # Video files the UI may offer as "file" camera sources (fake cameras for testing).
    samples_dir: Path = Path("./samples")

    cameras: list[CameraConfig] = []
    # Detection profiles. Empty = one built-in `default` profile from `detection`.
    profiles: list[ProfileConfig] = []
    mediamtx: MediaMtxConfig = MediaMtxConfig()
    processing: ProcessingConfig = ProcessingConfig()
    queue: QueueConfig = QueueConfig()
    detection: DetectionConfig = DetectionConfig()
    events: EventsConfig = EventsConfig()
    retention: RetentionConfig = RetentionConfig()
    location: LocationConfig = LocationConfig()
    storage: StorageConfig = StorageConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    # How users reach the web UI, e.g. http://192.168.1.10:8000 or https://cams.example.org.
    # Used for links (and image/clip URLs) in notifications.
    public_url: str | None = None

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
        s3 = self.storage.s3
        s3.endpoint_url = s3.endpoint_url or None
        s3.access_key_id = s3.access_key_id or None
        s3.secret_access_key = s3.secret_access_key or None
        if s3.enabled and not s3.bucket:
            raise ValueError("storage.s3.bucket is required when storage.s3.enabled is true")
        if not self.mediamtx.record_dir:
            self.mediamtx.record_dir = str(self.recordings_dir)
        self.samples_dir = self.samples_dir.resolve()
        ids = [c.id for c in self.cameras]
        if len(ids) != len(set(ids)):
            raise ValueError("camera ids must be unique")
        if not self.profiles:
            self.profiles = [ProfileConfig(
                id=DEFAULT_PROFILE, name="Fox", targets=self.detection.target_labels,
                min_confidence=self.detection.min_confidence, default=True)]
        # MEDIA_STREAMER_PUBLIC_URL fills in when the config has none (scripts/dev.sh sets it
        # from this machine's LAN address).
        self.public_url = self.public_url or os.environ.get("MEDIA_STREAMER_PUBLIC_URL") or None
        self.public_url = self.public_url.rstrip("/") if self.public_url else None
        cids = [c.id for c in self.notifications.channels]
        if len(cids) != len(set(cids)):
            raise ValueError("notification channel ids must be unique")
        pids = [p.id for p in self.profiles]
        if len(pids) != len(set(pids)):
            raise ValueError("profile ids must be unique")

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

    def all_profiles(self, include_disabled: bool = False) -> list[ProfileConfig]:
        """Profiles from config.yaml plus the ones added in the web UI."""
        from .profiles import ui_profiles  # deferred: imports the DB layer

        profiles = [*self.profiles, *ui_profiles()]
        return profiles if include_disabled else [p for p in profiles if p.enabled]

    def profile(self, profile_id: str | None) -> ProfileConfig | None:
        return next((p for p in self.all_profiles(include_disabled=True)
                     if p.id == profile_id), None)

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
    for ch in (raw.get("notifications") or {}).get("channels") or []:
        if isinstance(ch, dict) and True in ch:  # YAML 1.1 reads a bare `on:` key as true
            raise ValueError(f"notification channel {ch.get('id')!r}: use `when:` instead of "
                             "`on:` (YAML reads `on` as true)")
    return Settings.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return load_settings()
