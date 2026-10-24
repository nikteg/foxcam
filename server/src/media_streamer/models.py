"""Database schema (SQLite via SQLAlchemy). All timestamps are naive UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Table
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


class WorkerToken(Base):
    """Per-worker access token created in the web UI. Only a SHA-256 hash is stored."""

    __tablename__ = "worker_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Also the worker's identity: requests with this token act as worker `name`.
    name: Mapped[str] = mapped_column(String, unique=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    detector: Mapped[str | None] = mapped_column(String)
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
    mask_version_id: Mapped[int | None] = mapped_column(ForeignKey("mask_version.id"))

    has_target: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    max_confidence: Mapped[float | None] = mapped_column(Float)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Object key when archived to S3 (storage.s3.recordings).
    remote_key: Mapped[str | None] = mapped_column(String)
    # Files removed by retention; the row is kept so detections stay browsable.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

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
    current_job_id: Mapped[int | None] = mapped_column(Integer)
    jobs_done: Mapped[int] = mapped_column(Integer, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, default=0)


class Detection(Base):
    __tablename__ = "detection"

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segment.id", ondelete="CASCADE"),
                                            index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"))
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
    """A merged run of segments where a target (fox) was detected, plus its clip."""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
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
