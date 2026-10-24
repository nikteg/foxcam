"""Partial resets keep everything outside their scope."""

from datetime import timedelta

from media_streamer import reset
from media_streamer.db import session_scope
from media_streamer.models import (
    CameraState,
    Detection,
    DetectionProfile,
    Event,
    Job,
    JobState,
    ManagedCamera,
    Segment,
    SegmentStatus,
    Worker,
    WorkerToken,
    utcnow,
)
from media_streamer.worker_tokens import create

from .conftest import add_detection, add_job, add_segment


def _world(settings):
    """One analysed segment with a fox event, one waiting segment, a UI camera/profile,
    a worker and a token."""
    done = add_segment(settings, status=SegmentStatus.DONE, with_file=False, has_target=True,
                       pinned=True)
    job = add_job(done, state=JobState.DONE)
    det = add_detection(done, job, 100)
    waiting = add_segment(settings, status=SegmentStatus.QUEUED, with_file=False,
                          start=utcnow().replace(microsecond=0) - timedelta(minutes=1))
    add_job(waiting)
    (settings.recordings_dir / "cam").mkdir(parents=True, exist_ok=True)
    (settings.recordings_dir / "cam" / "x.mp4").write_bytes(b"x")
    (settings.clips_dir / "event-1.mp4").write_bytes(b"x")
    now = utcnow()
    with session_scope() as s:
        ev = Event(camera_id="cam", profile_id="default", label="fox", first_ts=now,
                   last_ts=now, max_confidence=0.9, clip_path="clips/event-1.mp4")
        ev.segments.append(s.get(Segment, done))
        s.add(ev)
        s.flush()
        s.get(Detection, det).event_id = ev.id
        s.add(ManagedCamera(id="ui", name="UI", source={"type": "test"}))
        s.add(CameraState(camera_id="cam", private=True, profiles=["extra"]))
        s.add(DetectionProfile(id="extra", config={"targets": ["car"]}))
        s.add(Worker(id="gpu", info={}))
    create("gpu")
    return done, waiting


def _count(model):
    with session_scope() as s:
        return s.query(model).count()


def test_reset_recordings_keeps_setup(settings):
    _world(settings)
    reset.recordings(settings)
    assert [_count(m) for m in (Segment, Job, Detection, Event)] == [0, 0, 0, 0]
    assert not any(settings.recordings_dir.iterdir()) and not any(settings.clips_dir.iterdir())
    assert [_count(m) for m in (ManagedCamera, CameraState, DetectionProfile, WorkerToken)] == [
        1, 1, 1, 1]


def test_reset_events_rebuilds_from_detections(settings):
    from media_streamer.pipeline import events

    _world(settings)
    reset.events(settings)
    assert _count(Event) == 0 and _count(Detection) == 1
    assert events.assign_events(settings) == 1  # rebuilt


def test_reset_detections_requeues_recordings(settings):
    done, _ = _world(settings)
    reset.detections(settings)
    assert [_count(m) for m in (Detection, Event, Job)] == [0, 0, 0]
    with session_scope() as s:
        assert {g.status for g in s.query(Segment)} == {SegmentStatus.NEW}
        assert not s.get(Segment, done).pinned and not s.get(Segment, done).has_target


def test_reset_queue_drops_only_waiting_jobs(settings):
    _, waiting = _world(settings)
    reset.queue(settings)
    with session_scope() as s:
        assert [j.state for j in s.query(Job)] == [JobState.DONE]
        assert s.get(Segment, waiting).status == SegmentStatus.DONE
    assert _count(Detection) == 1


def test_reset_workers_cameras_profiles(settings):
    _world(settings)
    reset.workers(settings)
    assert _count(Worker) == 0 and _count(WorkerToken) == 0
    reset.profiles(settings)
    assert _count(DetectionProfile) == 0
    with session_scope() as s:
        assert s.get(CameraState, "cam").profiles is None and s.get(CameraState, "cam").private
    reset.cameras(settings)
    assert _count(ManagedCamera) == 0 and _count(CameraState) == 0
    assert _count(Segment) == 2  # footage untouched


def test_remove_test_cameras_and_orphans(settings):
    from media_streamer.camera_store import add_camera

    add_camera(settings, name="Loop", source={"type": "test"})
    add_camera(settings, name="Real", source={"type": "rtsp", "url": "rtsp://cam/stream1"})
    ui_test, config_test = reset.test_cameras(settings)
    assert ui_test == ["loop"] and config_test == ["cam"]  # fixture camera is a test source

    _world(settings)  # data for "cam"
    add_segment(settings, camera="gone", with_file=False)  # camera no longer defined
    assert reset.orphaned_cameras(settings) == ["gone"]

    r = reset.purge_camera(settings, "loop")
    assert r["camera_removed"] == 1
    r = reset.purge_camera(settings, "cam")
    assert r["segments"] == 2 and r["events"] == 1 and r["camera_removed"] == 0
    with session_scope() as s:
        assert s.query(Segment).filter_by(camera_id="cam").count() == 0
        assert s.query(Event).count() == 0 and s.query(Detection).count() == 0
        assert s.get(CameraState, "cam") is None
        assert sorted(c.id for c in s.query(ManagedCamera)) == ["real", "ui"]  # "ui": _world
    assert not (settings.recordings_dir / "cam").exists()
    assert reset.orphaned_cameras(settings) == ["gone"]
