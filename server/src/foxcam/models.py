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
    Index,
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


class AppSetting(Base):
    """Server-wide settings changed in the web UI (they win over config.yaml), e.g.
    ``recording_profile_default``."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


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
    # Motion detection: low / medium / high (None = motion.sensitivity), and areas whose
    # movement is ignored (normalized [x, y, w, h], e.g. trees in the wind).
    motion_sensitivity: Mapped[str | None] = mapped_column(String)
    motion_rects: Mapped[list[Any] | None] = mapped_column(JSON)
    # Targets seen while the sun is below this many degrees are no match (e.g. a window
    # that reflects the lit room after dark, pipeline/daylight.py). None = off.
    sun_limit: Mapped[float | None] = mapped_column(Float)
    # Where the camera is, for the sun; None = location.latitude / longitude.
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)


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


class FootageSkip(Base):
    """Footage the camera never delivered: its clock jumped ahead in the stream (a cloud
    relay catching up after a stall drops what it could not send). The recording runs on
    without a pause, so this is where to mark it."""

    __tablename__ = "footage_skip"
    __table_args__ = (Index("ix_footage_skip_camera_at", "camera_id", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(String)
    # When the stream went on after the jump (UTC, the recorder's clock).
    at: Mapped[datetime] = mapped_column(DateTime)
    # How far the camera's clock jumped: about this much footage is missing there.
    missing_s: Mapped[float] = mapped_column(Float)


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
    Index("ix_event_segment_segment_id", "segment_id"),
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
    # A camera's recordings by time (lists, motion history).
    __table_args__ = (Index("ix_segment_camera_start", "camera_id", "start_at"),)

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
    # Motion in the derivative (pipeline/motion.py): the largest share of the picture that
    # moved in one frame (0 = none; None = not measured), and per frame with motion
    # [offset_ms, [[x, y, w, h], ...]] (normalized boxes around what moved), and the same
    # for faint motion (counts only for a detection's box it lies in; None = measured
    # before faint motion was kept).
    motion_score: Mapped[float | None] = mapped_column(Float)
    motion_boxes: Mapped[list[Any] | None] = mapped_column(JSON)
    motion_faint: Mapped[list[Any] | None] = mapped_column(JSON(none_as_null=True))
    # Skips in the camera's footage, found where ingest left a pause in the timestamps
    # (footage.py): [[offset_s, missing_s or None], ...]; None = prepared before that.
    skips: Mapped[list[Any] | None] = mapped_column(JSON(none_as_null=True))

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
    # Cascade (run: on_hit): the detector it follows found nothing (or gave up), so it did
    # not run. Kept as a row so the recording's run list can say why.
    SKIPPED = "skipped"

    ALL = (PENDING, LEASED, DONE, FAILED, DEAD, SKIPPED)
    FINISHED = (DONE, DEAD, SKIPPED)


class Job(Base):
    __tablename__ = "job"
    # The queue page (newest first) and its counts of jobs finished lately.
    __table_args__ = (Index("ix_job_created_at", "created_at"),
                      Index("ix_job_state_finished", "state", "finished_at"))

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segment.id", ondelete="CASCADE"),
                                            index=True)
    # Detection profile and detector (model) this job runs: one job per segment, profile
    # and detector (config.DetectorSpec).
    profile_id: Mapped[str | None] = mapped_column(String, index=True)
    detector_id: Mapped[str | None] = mapped_column(String, default="default")
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
    # The worker's result as posted (without the lease token), for debugging; at most
    # RAW_RESULT_MAX bytes, else {"truncated": true, "bytes": n, "text": "<start>…"}.
    raw_result: Mapped[Any | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    segment: Mapped[Segment] = relationship()


RAW_RESULT_MAX = 256 * 1024


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
    # A camera's detections by time (lists, the standing-still decision).
    __table_args__ = (Index("ix_detection_camera_ts", "camera_id", "ts"),
                      Index("ix_detection_target_ts", "is_target", "ts"))

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segment.id", ondelete="CASCADE"),
                                            index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[str | None] = mapped_column(String, index=True)
    # The profile's detector that reported it (its job's), next to the worker's `model`.
    detector_id: Mapped[str | None] = mapped_column(String, index=True)
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
    # A target standing still for its profile's still_minutes (pipeline/still.py): kept
    # for the record, but no match. None = not checked (off, or no motion measured).
    still: Mapped[bool | None] = mapped_column(Boolean)
    # Seen while the sun was below the camera's sun_limit (pipeline/daylight.py): kept,
    # but no match.
    below_sun_limit: Mapped[bool | None] = mapped_column(Boolean)
    model: Mapped[str | None] = mapped_column(String)
    worker_id: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Human feedback for building a training set: True = correct, False = wrong.
    feedback: Mapped[bool | None] = mapped_column(Boolean)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Starred by someone (shared by everyone). An event is a favorite when one of its
    # detections is: kept when events are rebuilt, and the event is not expired.
    favorite: Mapped[bool | None] = mapped_column(Boolean, index=True)

    segment: Mapped[Segment] = relationship()

    @property
    def bbox(self) -> list[float] | None:
        if self.bbox_x is None:
            return None
        return [self.bbox_x, self.bbox_y, self.bbox_w, self.bbox_h]


class Event(Base):
    """A merged run of target detections (one camera, profile and label), plus its clip."""

    __tablename__ = "event"
    __table_args__ = (Index("ix_event_camera_first_ts", "camera_id", "first_ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    profile_id: Mapped[str | None] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(String)
    first_ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_ts: Mapped[datetime] = mapped_column(DateTime)
    max_confidence: Mapped[float] = mapped_column(Float)
    detection_count: Mapped[int] = mapped_column(Integer, default=0)
    best_detection_id: Mapped[int | None] = mapped_column(Integer)
    # The profile's detectors (models) that saw it, and how many (pipeline/events.py).
    detectors: Mapped[list[Any] | None] = mapped_column(JSON)
    agreement: Mapped[int | None] = mapped_column(Integer, default=1)
    # False while a profile's confirming detector (cascade `confirm`) has not seen it:
    # no notifications until it has.
    confirmed: Mapped[bool | None] = mapped_column(Boolean, default=True)
    is_night: Mapped[bool | None] = mapped_column(Boolean)
    clip_path: Mapped[str | None] = mapped_column(String)
    clip_start: Mapped[datetime | None] = mapped_column(DateTime)
    clip_duration_s: Mapped[float | None] = mapped_column(Float)
    # Gaps between its recordings the clip keeps ([from_s, to_s, None] in clip time,
    # footage.py; None: built before clips kept them).
    clip_gaps: Mapped[list[Any] | None] = mapped_column(JSON)
    thumb_path: Mapped[str | None] = mapped_column(String)
    # The best detection's box, drawn over the thumbnail by the web app ([x, y, w, h],
    # 0..1). None: no box, or a thumbnail from before, with the box burned in.
    thumb_box: Mapped[list[Any] | None] = mapped_column(JSON)
    # Object keys once uploaded to S3 (reset when the clip is rebuilt).
    clip_remote_key: Mapped[str | None] = mapped_column(String)
    thumb_remote_key: Mapped[str | None] = mapped_column(String)
    clip_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    clip_error: Mapped[str | None] = mapped_column(String)
    # Rebuilt from detections that were already there (Settings → Rebuild events): no
    # notifications, they went out (or not) when it happened.
    quiet: Mapped[bool | None] = mapped_column(Boolean, default=False)
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


class PushSubscription(Base):
    """A browser or home screen app that gets a web push channel's notifications (webpush.py).
    The endpoint is the push service's URL for that one device; whoever has it (and the keys)
    can send it notifications, so it is never sent back to the UI."""

    __tablename__ = "push_subscription"
    __table_args__ = (UniqueConstraint("channel_id", "endpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String, index=True)
    endpoint: Mapped[str] = mapped_column(String)
    p256dh: Mapped[str] = mapped_column(String)  # the browser's public key (base64url)
    auth: Mapped[str] = mapped_column(String)  # its auth secret (base64url)
    name: Mapped[str | None] = mapped_column(String)  # "iPhone", "Chrome on Mac"…
    user_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime)


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
    # The address is known to be this person's: confirmed by an OAuth provider, or the
    # account was made by an admin (or setup). Self sign-up with a password is not. Only
    # verified accounts get admin_emails' rights or are matched to OAuth sign-ins.
    email_verified: Mapped[bool | None] = mapped_column(Boolean, default=False)
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


class ApiKey(Base):
    """A key for scripts and other programs (`Authorization: Bearer msk_…`), acting as its
    user. Only its SHA-256 is stored; the key itself is shown once."""

    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    # The first characters, to tell keys apart in the list.
    prefix: Mapped[str] = mapped_column(String)
    # What it may do: read, write, admin (api_keys.SCOPES).
    scopes: Mapped[list[str] | None] = mapped_column(JSON)
    # Before scopes (a NOT NULL column in databases from then); unused.
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


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
