"""Configuration: a YAML file (``config.yaml``) with ``${ENV_VAR}`` expansion.

Camera definitions and tunables live here. Runtime-editable state (privacy masks,
private toggle) lives in the database and is edited from the web UI, and so do the
tunables in ``runtime_settings.EDITABLE`` when an admin changes them (Settings page).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, model_validator

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
    # Pasted into an ffmpeg filter, so strictly WIDTHxHEIGHT.
    size: str = Field(default="1280x720", pattern=r"^\d{2,5}x\d{2,5}$")
    fps: int = Field(default=15, ge=1, le=60)


class FileSource(BaseModel):
    """Loop a local video file as if it were a live camera (e.g. a fox clip for testing)."""

    type: Literal["file"]
    path: str


Source = Annotated[
    RtspSource | TapoCloudSource | TestSource | FileSource, Field(discriminator="type")
]


PROFILE_ID = r"^[a-z0-9][a-z0-9_-]*$"
TAG = r"^[a-z0-9][a-z0-9_.-]*$"


class CameraConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str | None = None
    source: Source
    segment_seconds: int | None = Field(default=None, ge=5, le=3600)  # overrides the global
    enabled: bool = True
    # Detection profiles to run on this camera. None = every profile marked `default`.
    # The web UI can override this per camera.
    profiles: list[str] | None = None
    # Recording profile (quality / retention). None = recording_profile_default.
    # The web UI can override this per camera.
    recording_profile: str | None = None
    # "config" = defined in config.yaml (read-only in the UI), "ui" = added in the web UI.
    managed: Literal["config", "ui"] = "config"

    @property
    def display_name(self) -> str:
        return self.name or self.id


class RecordingProfileConfig(BaseModel):
    """How a camera is recorded: as the camera sends it, or re-encoded smaller.

    Re-encoding (``transcode``) costs CPU (or a hardware encoder, see
    ``mediamtx.hwaccel``) but can cut storage several times over.
    """

    id: str = Field(pattern=PROFILE_ID, max_length=40)
    name: str | None = None
    # False = store the camera's own stream untouched (no quality loss, no CPU).
    transcode: bool = False
    codec: Literal["h264", "h265"] = "h264"
    # Scale down to at most this many lines (never up). None = keep the resolution.
    max_height: int | None = Field(default=None, ge=144, le=4320)
    fps: int | None = Field(default=None, ge=1, le=60)
    # Target video bitrate. None = constant quality (CRF 23 / encoder default).
    bitrate_kbit: int | None = Field(default=None, ge=100, le=50000)
    # Delete this camera's ordinary recordings after N days (None = retention.segments_days).
    keep_days: float | None = Field(default=None, ge=0)
    managed: Literal["builtin", "config"] = "config"

    @property
    def display_name(self) -> str:
        return self.name or self.id


class LowDataConfig(BaseModel):
    """On-demand low-bitrate copy of every camera for live view on slow connections.

    MediaMTX starts the transcode (``ffmpeg``, run by the MediaMTX process) only while
    someone watches ``<camera>/low`` and stops it shortly after the last viewer leaves.
    """

    enabled: bool = True
    height: int = Field(default=480, ge=144, le=1080)
    bitrate_kbit: int = Field(default=500, ge=100, le=8000)
    fps: int = Field(default=15, ge=1, le=30)


class MediaMtxConfig(BaseModel):
    api_url: str = "http://localhost:9997"
    # RTSP URL prefix used by ingest publishers and for snapshots (as seen by this server).
    rtsp_url: str = "rtsp://localhost:8554"
    # MediaMTX's WebRTC and HLS servers as seen by this server. Browsers never talk to
    # them directly: live view goes through /api/live/..., which checks the login.
    webrtc_url: str = "http://localhost:8889"
    hls_url: str = "http://localhost:8888"
    # Password of the `foxcam` user in mediamtx.yml. None = a random one kept in
    # <data_dir>/mediamtx/password ($FOXCAM_MTX_PASSWORD wins); `foxcam mediamtx`
    # starts MediaMTX with it. Only needed here when MediaMTX runs elsewhere.
    password: str | None = None
    # Recording directory as seen by the MediaMTX process. Defaults to ``recordings_dir``.
    record_dir: str | None = None
    low_data: LowDataConfig = LowDataConfig()
    # Hardware video encoding/decoding for the low-data, mask and recording-profile
    # transcodes (run by the MediaMTX process via `foxcam transcode`):
    # auto (best that works there) | none | videotoolbox | nvenc | qsv | vaapi | amf | mf |
    # vulkan. `foxcam hwaccel` shows what works. See docs/HARDWARE.md.
    hwaccel: Literal["auto", "none", "videotoolbox", "nvenc", "qsv", "vaapi", "amf", "mf",
                     "vulkan"] = "auto"
    # Linux render node for vaapi / qsv (None = /dev/dri/renderD128).
    hw_device: str | None = None
    # ffmpeg, and this installation's `foxcam` command, as seen by MediaMTX.
    ffmpeg: str = "ffmpeg"
    transcoder: str | None = None


class ProcessingConfig(BaseModel):
    # Frames per second kept in the masked derivative handed to workers.
    sample_fps: float = Field(default=2.0, gt=0, le=30)
    max_width: int = Field(default=1280, ge=160, le=7680)
    concurrency: int = Field(default=2, ge=1, le=16)
    # Mean saturation below this => frame considered grayscale (IR night mode).
    night_saturation_threshold: float = Field(default=4.0, ge=0, le=255)
    # Detections that are no match (below the profile's min_confidence, or another label)
    # are only kept from this confidence up; the ones rated on the Detections page always.
    # Matches are always kept. 0 = keep everything the workers report.
    keep_non_targets_from: float = Field(default=0.25, ge=0, le=1)


class QueueConfig(BaseModel):
    lease_seconds: int = Field(default=300, ge=30, le=86400)
    max_attempts: int = Field(default=3, ge=1, le=20)
    retry_backoff_seconds: int = Field(default=60, ge=0, le=86400)


class DetectionConfig(BaseModel):
    """A quick `default` profile: used only when config.yaml has this section and no
    `profiles:`. Without either, profiles come from the web UI."""

    # Open-vocabulary workers are prompted with these: one word per thing (synonyms compete,
    # see docs/MODELS.md). Other models' names for it go in `aliases`.
    target_labels: list[str] = ["fox"]
    aliases: dict[str, str] = {"red fox": "fox", "vulpes vulpes": "fox"}
    min_confidence: float = 0.5


DEFAULT_PROFILE = "default"
DEFAULT_DETECTOR = "default"


class DetectorSpec(BaseModel):
    """One model looking for a profile's targets. Each segment gets one job per profile
    and detector; a worker leases it when it has all of the profile's and the detector's
    `requires` tags."""

    id: str = Field(pattern=PROFILE_ID, max_length=40)
    name: str | None = None
    requires: list[Annotated[str, Field(pattern=TAG)]] = []
    # Overrides of the profile's values for this detector's detections / frames.
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    fps: float | None = Field(default=None, gt=0)
    # Cascade: `always` runs on every segment; `on_hit` only when detector `after` found a
    # target in it (e.g. a cheap model first, SpeciesNet to confirm), else it is skipped.
    run: Literal["always", "on_hit"] = "always"
    after: str | None = Field(default=None, pattern=PROFILE_ID, max_length=40)
    # on_hit only: the profile's events count (notifications, a segment's target flag)
    # once this detector agrees.
    confirm: bool = False

    @property
    def display_name(self) -> str:
        return self.name or self.id


class ProfileConfig(BaseModel):
    """What to look for, and which models look for it.

    Every segment of a camera gets one job per profile and detector. Only workers that
    have all of a detector's tags (the profile's `requires` plus the detector's own) lease
    its jobs. Detections whose (aliased) label is in `targets` with at least
    `min_confidence` become events; detections of several detectors merge into one event.
    Without `detectors` a profile has one, `default`, needing just `requires`.
    """

    id: str = Field(pattern=PROFILE_ID, max_length=40)
    name: str | None = None
    targets: list[str] = Field(min_length=1)
    # Map model labels to one name, e.g. {"red fox": "fox", "vulpes vulpes": "fox"}.
    aliases: dict[str, str] = {}
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    # Per target (its label), a threshold of its own: wins over the detector's and the
    # profile's, e.g. {person: 0.8} where a false person is worse than a missed fox.
    target_min_confidence: dict[str, Annotated[float, Field(ge=0, le=1)]] = {}
    # Tags every detector of the profile needs (with no `detectors`: the one detector's).
    requires: list[Annotated[str, Field(pattern=TAG)]] = []
    # The models that look; empty = one detector `default` (see the class docstring).
    detectors: list[DetectorSpec] = []
    # Frames per second the worker analyses (≤ processing.sample_fps). None = all.
    fps: float | None = Field(default=None, gt=0)
    # Run on cameras that have no explicit profile selection.
    default: bool = False
    # Delete this profile's events after N days (None = retention.events_days).
    events_days: float | None = None
    # A target that stays in the same place this long (a parked car) stops counting: it
    # neither makes nor extends events until it moves or leaves. 0 = off.
    ignore_stationary_minutes: float = Field(default=10, ge=0, le=1440)
    # A target only counts when its box overlaps motion (pipeline/motion.py).
    require_motion: bool = False
    # Recordings without any motion get no jobs of this profile (marked skipped).
    skip_without_motion: bool = False
    enabled: bool = True
    managed: Literal["config", "ui"] = "config"

    @model_validator(mode="after")
    def _detectors(self) -> ProfileConfig:
        if not self.detectors:
            self.detectors = [DetectorSpec(id=DEFAULT_DETECTOR)]
        ids = [d.id for d in self.detectors]
        if len(ids) != len(set(ids)):
            raise ValueError("detector ids must be unique within a profile")
        by_id = {d.id: d for d in self.detectors}
        for d in self.detectors:
            if d.run == "always":
                if d.after or d.confirm:
                    raise ValueError(f"detector {d.id}: `after` and `confirm` are for "
                                     "run: on_hit")
                continue
            if not d.after or d.after not in by_id:
                raise ValueError(f"detector {d.id}: run: on_hit needs `after`, the id of "
                                 "another detector of the profile")
            seen, cur = {d.id}, by_id[d.after]
            while cur.run == "on_hit":  # follow the chain back to a detector that always runs
                if cur.id in seen:
                    raise ValueError(f"detector {d.id}: `after` goes round in a circle")
                seen.add(cur.id)
                cur = by_id[cur.after]
        if not any(d.run == "always" for d in self.detectors):
            raise ValueError("at least one detector must run always")
        return self

    @property
    def display_name(self) -> str:
        return self.name or self.id

    def detector(self, detector_id: str | None) -> DetectorSpec | None:
        """``None`` (jobs from before detectors existed) is the first detector."""
        if detector_id is None:
            return self.detectors[0]
        return next((d for d in self.detectors if d.id == detector_id), None)

    @property
    def always(self) -> list[DetectorSpec]:
        """Detectors that get a job on every segment (the start of any cascade)."""
        return [d for d in self.detectors if d.run == "always"]

    def followers(self, detector_id: str | None) -> list[DetectorSpec]:
        """Detectors that run when this one found a target (run: on_hit, after it)."""
        did = detector_id or self.detectors[0].id
        return [d for d in self.detectors if d.run == "on_hit" and d.after == did]

    @property
    def confirming(self) -> set[str]:
        """Detectors whose agreement makes the profile's events count (empty = any)."""
        return {d.id for d in self.detectors if d.confirm}

    def detector_requires(self, d: DetectorSpec) -> set[str]:
        return set(self.requires) | set(d.requires)

    def min_confidence_for(self, detector_id: str | None, label: str | None = None) -> float:
        """The match threshold: the target's own, else the detector's, else the profile's."""
        if label is not None:
            own = {k.lower(): v for k, v in self.target_min_confidence.items()}
            if (value := own.get(self.canonical(label).lower())) is not None:
                return value
        d = self.detector(detector_id)
        return d.min_confidence if d and d.min_confidence is not None else self.min_confidence

    def fps_for(self, detector_id: str | None) -> float | None:
        d = self.detector(detector_id)
        return d.fps if d and d.fps is not None else self.fps

    def canonical(self, label: str) -> str:
        aliases = {k.lower(): v for k, v in self.aliases.items()}
        return aliases.get(label.lower(), label)

    def is_target(self, label: str, confidence: float, detector_id: str | None = None) -> bool:
        return (confidence >= self.min_confidence_for(detector_id, label)
                and self.canonical(label).lower() in {t.lower() for t in self.targets})


MotionSensitivity = Literal["low", "medium", "high"]


class MotionConfig(BaseModel):
    """Motion detection on the derivative workers get (pipeline/motion.py)."""

    # Cameras without their own (camera settings: Detection → Motion).
    sensitivity: MotionSensitivity = "medium"
    # More of the picture than this changing at once is light (IR switching, exposure),
    # not motion.
    light_change_fraction: float = Field(default=0.5, gt=0, le=1)
    # A detection is moving when its box overlaps motion this close in time.
    window_seconds: float = Field(default=1.0, ge=0, le=10)


class EventsConfig(BaseModel):
    pre_roll_seconds: int = Field(default=10, ge=0, le=600)
    post_roll_seconds: int = Field(default=10, ge=0, le=600)
    # Positive segments closer than this are merged into one event.
    merge_gap_seconds: int = Field(default=30, ge=0, le=3600)
    # Wait this long after the last change before (re)building the clip.
    settle_seconds: int = Field(default=90, ge=0, le=3600)


class CompactionConfig(BaseModel):
    """Join finished segments into one file per camera and period (stream copy, no
    re-encoding): far fewer files on disk and in S3. Segments stay short while recording,
    so detection is not delayed. See docs/ARCHITECTURE.md (Compaction)."""

    enabled: bool = True
    period_minutes: int = Field(default=60, ge=5, le=1440)
    # Wait this long after a period ends before joining it (late segments, preprocessing).
    delay_minutes: int = Field(default=10, ge=1, le=1440)


class RetentionConfig(BaseModel):
    # Delete ordinary segments after this many days. 0 disables age-based deletion.
    segments_days: float = Field(default=7, ge=0)
    # Segments still waiting for a worker are kept up to this many days.
    unprocessed_max_days: float = Field(default=14, ge=0)
    # Delete fox events (clip + pinned segments) after this many days. 0 = keep forever.
    events_days: float = Field(default=0, ge=0)
    # Hard cap on recordings disk usage; oldest unpinned segments go first.
    max_disk_gb: float | None = Field(default=None, gt=0)
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
    prefix: str = "foxcam/"
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
    # Only once this many of the profile's detectors (models) saw the event.
    min_agreement: int = Field(default=1, ge=1, le=10)
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
    # webhook: HMAC-SHA256 of the body in X-Foxcam-Signature: sha256=<hex>
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


class OAuthProviderConfig(BaseModel):
    """Sign in with an external account (see docs/AUTH.md).

    ``google``, ``microsoft`` and ``github`` need only the client id and secret; ``oidc``
    works with any OpenID Connect provider (Authentik, Keycloak, Authelia, Auth0, …) given
    its ``issuer``. The redirect URL to register at the provider is
    ``<base url>/api/auth/oauth/<id>/callback``.
    """

    id: str = Field(pattern=PROFILE_ID, max_length=40)
    name: str | None = None
    type: Literal["google", "microsoft", "github", "oidc"] = "oidc"
    client_id: str
    client_secret: str
    # OpenID Connect issuer (oidc; microsoft: defaults to the `common` tenant).
    issuer: str | None = None
    scopes: list[str] | None = None
    # Only accounts with an e-mail address in these domains may sign in (empty = any).
    allowed_domains: list[str] = []

    @property
    def display_name(self) -> str:
        return self.name or {"google": "Google", "microsoft": "Microsoft",
                             "github": "GitHub"}.get(self.type, self.id)

    def model_post_init(self, _ctx: object) -> None:
        if self.type == "oidc" and not self.issuer:
            raise ValueError(f"oauth provider {self.id!r}: oidc needs an issuer")


class AuthConfig(BaseModel):
    """Accounts and logins. Everything except the worker API needs a signed-in user."""

    # False = no logins at all: everyone who reaches the server is an admin. Only for a
    # trusted network.
    enabled: bool = True
    # Sign in with e-mail and password (accounts can also use OAuth only).
    password_login: bool = True
    # Who may create an account: invite = only with an invite link (default),
    # open = anyone who reaches the server, closed = nobody (admins add users).
    registration: Literal["invite", "open", "closed"] = "invite"
    # These accounts are admins, and may sign up through OAuth even when registration is
    # closed (handy to bootstrap the first admin without a password).
    admin_emails: list[str] = []
    # Stay signed in this long since the last visit.
    session_days: float = Field(default=30, gt=0)
    # Users (not only admins) may add cameras; they own them and can share them.
    users_can_add_cameras: bool = True
    oauth: list[OAuthProviderConfig] = []
    # How browsers reach the server, for OAuth redirect URLs. None = public_url, else the
    # address of the request.
    base_url: str | None = None

    def model_post_init(self, _ctx: object) -> None:
        self.admin_emails = [e.strip().lower() for e in self.admin_emails if e.strip()]
        ids = [p.id for p in self.oauth]
        if len(ids) != len(set(ids)):
            raise ValueError("oauth provider ids must be unique")


class Settings(BaseModel):
    data_dir: Path = Path("./data")
    recordings_dir: Path | None = None
    database_url: str | None = None
    segment_seconds: int = Field(default=60, ge=1, le=3600)
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
    # Recording profiles in addition to the built-in ones (same id replaces a built-in).
    recording_profiles: list[RecordingProfileConfig] = []
    # Recording profile of cameras without one of their own.
    recording_profile_default: str = "original"
    mediamtx: MediaMtxConfig = MediaMtxConfig()
    processing: ProcessingConfig = ProcessingConfig()
    queue: QueueConfig = QueueConfig()
    # None (no `detection:` in config.yaml): no built-in profile.
    detection: DetectionConfig | None = None
    events: EventsConfig = EventsConfig()
    motion: MotionConfig = MotionConfig()
    retention: RetentionConfig = RetentionConfig()
    compaction: CompactionConfig = CompactionConfig()
    location: LocationConfig = LocationConfig()
    storage: StorageConfig = StorageConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    auth: AuthConfig = AuthConfig()
    # How users reach the web UI, e.g. http://192.168.1.10:8000 or https://cams.example.org.
    # Used for links (and image/clip URLs) in notifications. https:// here also makes the
    # session cookie Secure.
    public_url: str | None = Field(default=None, pattern=r"^https?://[^\s/]+(/\S*)?$")
    # Reverse proxies whose X-Forwarded-For / -Proto headers are believed (client address
    # for the login limits, https). None = $FORWARDED_ALLOW_IPS, else 127.0.0.1. In Docker,
    # the proxy's address as the container sees it, e.g. 172.18.0.1. Never "*" when the
    # server can be reached without the proxy: anyone could then fake their address.
    trusted_proxies: list[str] | None = None
    # runtime_settings: config.yaml's values of the web-UI-editable tunables, and when the
    # database values were last applied.
    _runtime: dict[str, Any] = PrivateAttr(default_factory=dict)

    def model_post_init(self, _ctx: object) -> None:
        self.data_dir = self.data_dir.resolve()
        if self.recordings_dir is None:
            self.recordings_dir = self.data_dir / "recordings"
        self.recordings_dir = self.recordings_dir.resolve()
        if self.database_url is None:
            self.database_url = f"sqlite:///{self.data_dir / 'foxcam.db'}"
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
        if not self.profiles and self.detection is not None:
            self.profiles = [ProfileConfig(
                id=DEFAULT_PROFILE, name="Fox", targets=self.detection.target_labels,
                aliases=self.detection.aliases, min_confidence=self.detection.min_confidence, default=True)]
        # FOXCAM_PUBLIC_URL fills in when the config has none (scripts/dev.sh sets it
        # from this machine's LAN address).
        self.public_url = self.public_url or os.environ.get("FOXCAM_PUBLIC_URL") or None
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
    path = Path(path or os.environ.get("FOXCAM_CONFIG", "config.yaml"))
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
