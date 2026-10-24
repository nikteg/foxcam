"""Database schema (SQLite via SQLAlchemy). All timestamps are naive UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {dict[str, Any]: JSON, list[Any]: JSON}


class CameraState(Base):
    """UI-editable per-camera state. Camera definitions themselves come from config."""

    __tablename__ = "camera_state"

    camera_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Private cameras never produce worker jobs.
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    mask_version_id: Mapped[int | None] = mapped_column(ForeignKey("mask_version.id"))
    # Detection profiles chosen in the UI; None = the camera's config / default profiles.
    profiles: Mapped[list[Any] | None] = mapped_column(JSON)
    # Recording profile chosen in the UI; None = the camera's config / the default.
    recording_profile: Mapped[str | None] = mapped_column(String)
    # Burn the censor rectangles into the recording and live view too (re-encodes).
    burn_masks: Mapped[bool | None] = mapped_column(Boolean, default=False)


class ManagedCamera(Base):
    """A camera added from the web UI (config.yaml cameras are not stored here)."""

    __tablename__ = "managed_camera"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    # Same shape as a config.yaml camera `source:` block.
    source: Mapped[dict[str, Any]] = mapped_column(JSON)
    segment_seconds: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DetectionProfile(Base):
    """A detection profile added in the web UI (config.yaml profiles are not stored)."""

    __tablename__ = "detection_profile"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Same fields as a config.yaml profile (see config.ProfileConfig).
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorkerToken(Base):
    """Per-worker access token created in the web UI. Only a SHA-256 hash is stored."""

    __tablename__ = "worker_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Also the worker's identity: requests with this token act as worker `name`.
    name: Mapped[str] = mapped_column(String, unique=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    detector: Mapped[str | None] = mapped_column(String)
    # Extra capability tags granted to this worker (e.g. to pin it to a profile).
    tags: Mapped[list[Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class MaskVersion(Base):
    """Immutable set of censor rectangles. Rects are normalized [x, y, w, h] in 0..1."""

    __tablename__ = "mask_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    rects: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SegmentStatus:
    NEW = "new"  # registered, waiting for preprocessing
    QUEUED = "queued"  # derivative built, job waiting/running
    DONE = "done"  # worker results stored
    PRIVATE = "private"  # camera was private, never sent to a worker
    FAILED = "failed"  # preprocessing or job permanently failed

    TERMINAL = frozenset({DONE, PRIVATE, FAILED})


event_segments = Table(
    "event_segment",
    Base.metadata,
    Column("event_id", ForeignKey("event.id", ondelete="CASCADE"), primary_key=True),
    Column("segment_id", ForeignKey("segment.id", ondelete="CASCADE"), primary_key=True),
)


class Archive(Base):
    """Consecutive segments of one camera joined into one file (compaction, stream copy).

    Segment rows stay; they point into the archive with an offset."""

    __tablename__ = "archive"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    # Path relative to recordings_dir, e.g. garden/archive/2026-10-24_14-00-00Z.mp4
    path: Mapped[str] = mapped_column(String, unique=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Recording profile it was encoded with ("original" = joined without re-encoding),
    # and the encoder used ("copy", "hevc_nvenc", ...).
    profile_id: Mapped[str | None] = mapped_column(String)
    encoder: Mapped[str | None] = mapped_column(String)
    # Object key when archived to S3 (storage.s3.recordings).
    remote_key: Mapped[str | None] = mapped_column(String)
    # File removed (all its segments expired).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Segment(Base):
    __tablename__ = "segment"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    # Path relative to recordings_dir.
    path: Mapped[str] = mapped_column(String, unique=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default=SegmentStatus.NEW, index=True)
    error: Mapped[str | None] = mapped_column(String)

    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Light tagging: IR night mode shows up as (near) zero colour saturation.
    is_night: Mapped[bool | None] = mapped_column(Boolean)
    saturation_avg: Mapped[float | None] = mapped_column(Float)
    luma_avg: Mapped[float | None] = mapped_column(Float)
    sun_elevation: Mapped[float | None] = mapped_column(Float)

    thumb_path: Mapped[str | None] = mapped_column(String)
    derived_path: Mapped[str | None] = mapped_column(String)
    # Frame rate of the derivative (the highest any of the camera's profiles needs).
    derived_fps: Mapped[float | None] = mapped_column(Float)
    mask_version_id: Mapped[int | None] = mapped_column(ForeignKey("mask_version.id"))

    has_target: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    max_confidence: Mapped[float | None] = mapped_column(Float)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Object key when archived to S3 (storage.s3.recordings).
    remote_key: Mapped[str | None] = mapped_column(String)
    # Files removed by retention; the row is kept so detections stay browsable.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Joined into an hourly file by compaction: the footage is in `archive` from
    # `archive_offset_s` on, and the file at `path` is gone.
    archive_id: Mapped[int | None] = mapped_column(ForeignKey("archive.id"), index=True)
    archive_offset_s: Mapped[float | None] = mapped_column(Float)

    archive: Mapped[Archive | None] = relationship()

    @property
    def end_at(self) -> datetime:
        from datetime import timedelta

        return self.start_at + timedelta(seconds=self.duration_s)


class JobState:
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"  # failed, will be retried after backoff
    DEAD = "dead"  # gave up after max_attempts

    ALL = (PENDING, LEASED, DONE, FAILED, DEAD)


class Job(Base):
    __tablename__ = "job"

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segment.id", ondelete="CASCADE"),
                                            index=True)
    # Detection profile this job runs (one job per segment and profile).
    profile_id: Mapped[str | None] = mapped_column(String, index=True)
    state: Mapped[str] = mapped_column(String, default=JobState.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String)
    leased_by: Mapped[str | None] = mapped_column(String)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    segment: Mapped[Segment] = relationship()


class Worker(Base):
    __tablename__ = "worker"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Capability tags from the last lease (worker --tags, detector, token).
    tags: Mapped[list[Any] | None] = mapped_column(JSON)
    # Labels its detector can report (closed-set models: COCO, a fine-tuned model,
    # SpeciesNet's taxonomy). None = not reported (open-vocabulary: anything). Suggestions
    # in the label pickers (GET /api/labels); kept until the worker reports a new list.
    vocabulary: Mapped[list[Any] | None] = mapped_column(JSON)
    vocabulary_at: Mapped[datetime | None] = mapped_column(DateTime)
    current_job_id: Mapped[int | None] = mapped_column(Integer)
    jobs_done: Mapped[int] = mapped_column(Integer, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, default=0)


class Detection(Base):
    __tablename__ = "detection"

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segment.id", ondelete="CASCADE"),
                                            index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"))
    profile_id: Mapped[str | None] = mapped_column(String, index=True)
    # Set once a target detection has been merged into an event. Not a foreign key on
    # purpose: it keeps pointing at expired (deleted) events so they are not re-created.
    event_id: Mapped[int | None] = mapped_column(Integer, index=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)  # absolute UTC time
    offset_ms: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String, index=True)
    confidence: Mapped[float] = mapped_column(Float)
    # Normalized to the frame, 0..1.
    bbox_x: Mapped[float | None] = mapped_column(Float)
    bbox_y: Mapped[float | None] = mapped_column(Float)
    bbox_w: Mapped[float | None] = mapped_column(Float)
    bbox_h: Mapped[float | None] = mapped_column(Float)
    is_target: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    model: Mapped[str | None] = mapped_column(String)
    worker_id: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Human feedback for building a training set: True = correct, False = wrong.
    feedback: Mapped[bool | None] = mapped_column(Boolean)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime)

    segment: Mapped[Segment] = relationship()

    @property
    def bbox(self) -> list[float] | None:
        if self.bbox_x is None:
            return None
        return [self.bbox_x, self.bbox_y, self.bbox_w, self.bbox_h]


class Event(Base):
    """A merged run of target detections (one camera, profile and label), plus its clip."""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    profile_id: Mapped[str | None] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(String)
    first_ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_ts: Mapped[datetime] = mapped_column(DateTime)
    max_confidence: Mapped[float] = mapped_column(Float)
    detection_count: Mapped[int] = mapped_column(Integer, default=0)
    best_detection_id: Mapped[int | None] = mapped_column(Integer)
    is_night: Mapped[bool | None] = mapped_column(Boolean)
    clip_path: Mapped[str | None] = mapped_column(String)
    clip_start: Mapped[datetime | None] = mapped_column(DateTime)
    clip_duration_s: Mapped[float | None] = mapped_column(Float)
    thumb_path: Mapped[str | None] = mapped_column(String)
    # Object keys once uploaded to S3 (reset when the clip is rebuilt).
    clip_remote_key: Mapped[str | None] = mapped_column(String)
    thumb_remote_key: Mapped[str | None] = mapped_column(String)
    clip_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    clip_error: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    segments: Mapped[list[Segment]] = relationship(secondary=event_segments)


class NotificationChannel(Base):
    """A notification channel added in the web UI (config.yaml channels are not stored)."""

    __tablename__ = "notification_channel"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Same fields as a config.yaml channel (see config.ChannelConfig).
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NotificationStatus:
    PENDING = "pending"  # waiting to be sent (or retried)
    SENT = "sent"
    FAILED = "failed"  # gave up after retries
    SKIPPED = "skipped"  # quiet hours / cooldown


class Notification(Base):
    """One notification for one channel: an outbox row, retried until sent."""

    __tablename__ = "notification"
    __table_args__ = (UniqueConstraint("channel_id", "event_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), index=True)
    camera_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # event.new | event.clip
    status: Mapped[str] = mapped_column(String, default=NotificationStatus.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    error: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)


class User(Base):
    """A person who signs in to the web UI (see accounts.py)."""

    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored lower-case; the login name.
    email: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str | None] = mapped_column(String)
    # scrypt hash (accounts._hash_password); None = OAuth sign-in only.
    password_hash: Mapped[str | None] = mapped_column(String)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class OAuthIdentity(Base):
    """An external account (Google, GitHub, an OIDC provider) linked to a user."""

    __tablename__ = "oauth_identity"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserSession(Base):
    """A signed-in browser. The cookie holds the token; only its SHA-256 is stored."""

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    user_agent: Mapped[str | None] = mapped_column(String)


class CameraRole:
    VIEWER = "viewer"  # live view, recordings, events, detections
    EDITOR = "editor"  # + censor areas, profiles, recording settings, feedback, rename
    OWNER = "owner"  # + sharing and removing the camera

    ORDER = (VIEWER, EDITOR, OWNER)

    @classmethod
    def at_least(cls, role: str | None, wanted: str) -> bool:
        return role in cls.ORDER and cls.ORDER.index(role) >= cls.ORDER.index(wanted)


class CameraAccess(Base):
    """A user's role on one camera (admins can do everything without a row)."""

    __tablename__ = "camera_access"

    camera_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"),
                                         primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String, default=CameraRole.VIEWER)
    granted_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Invite(Base):
    """A single-use link that creates an account and/or grants access to cameras."""

    __tablename__ = "invite"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    # [{"camera_id": ..., "role": ...}]
    cameras: Mapped[list[Any]] = mapped_column(JSON, default=list)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    used_by: Mapped[int | None] = mapped_column(Integer)
