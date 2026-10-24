from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from media_streamer.config import Settings
from media_streamer.db import init_db, session_scope
from media_streamer.models import Detection, Job, JobState, Segment, SegmentStatus, utcnow
from media_streamer.pipeline.scanner import NAME_FORMAT

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    samples = tmp_path / "samples"
    samples.mkdir()
    s = Settings.model_validate({
        "data_dir": str(tmp_path / "data"),
        "samples_dir": str(samples),
        "segment_seconds": 2,
        # Nothing listens on port 1: MediaMTX calls fail fast and are ignored.
        "mediamtx": {"api_url": "http://127.0.0.1:1", "rtsp_url": "rtsp://127.0.0.1:1"},
        "cameras": [{"id": "cam", "name": "Cam", "source": {"type": "test"}}],
        "queue": {"lease_seconds": 60, "max_attempts": 2, "retry_backoff_seconds": 0},
        "events": {"settle_seconds": 0, "pre_roll_seconds": 1, "post_roll_seconds": 1,
                   "merge_gap_seconds": 5},
    })
    init_db(s)
    return s


def make_video(path: Path, seconds: float = 2, gray: bool = False, size: str = "160x90") -> Path:
    """Small fragmented MP4 like MediaMTX writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    vf = ["hue=s=0"] if gray else []
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=10",
         "-t", str(seconds), *(["-vf", ",".join(vf)] if vf else []), "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "10",
         "-movflags", "+frag_keyframe+empty_moov+default_base_moof", str(path)],
        check=True,
    )
    return path


def recording_path(settings: Settings, camera: str, start: datetime) -> Path:
    return settings.recordings_dir / camera / f"{start.strftime(NAME_FORMAT)}.mp4"


def add_segment(settings: Settings, camera: str = "cam", start: datetime | None = None,
                seconds: float = 2, status: str = SegmentStatus.DONE, with_file: bool = True,
                **fields) -> int:
    """Insert a segment row (and optionally a real file) directly, bypassing the scanner."""
    start = start or utcnow().replace(microsecond=0)
    path = recording_path(settings, camera, start)
    if with_file:
        make_video(path, seconds)
    with session_scope() as s:
        seg = Segment(camera_id=camera, path=str(path.relative_to(settings.recordings_dir)),
                      start_at=start, duration_s=seconds,
                      size_bytes=path.stat().st_size if with_file else 1000,
                      status=status, **fields)
        s.add(seg)
        s.flush()
        return seg.id


def add_job(segment_id: int, state: str = JobState.PENDING, **fields) -> int:
    with session_scope() as s:
        job = Job(segment_id=segment_id, state=state, **fields)
        s.add(job)
        s.flush()
        return job.id


def add_detection(segment_id: int, job_id: int, offset_ms: int, label: str = "fox",
                  confidence: float = 0.9, target: bool = True) -> int:
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        d = Detection(segment_id=segment_id, job_id=job_id, camera_id=seg.camera_id,
                      ts=seg.start_at + timedelta(milliseconds=offset_ms), offset_ms=offset_ms,
                      label=label, confidence=confidence, bbox_x=0.4, bbox_y=0.4, bbox_w=0.2,
                      bbox_h=0.2, is_target=target)
        s.add(d)
        s.flush()
        return d.id
