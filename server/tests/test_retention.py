from datetime import timedelta

from foxcam.db import session_scope
from foxcam.models import Detection, Event, JobState, Segment, SegmentStatus, utcnow
from foxcam.pipeline import retention

from .conftest import add_detection, add_job, add_segment, needs_ffmpeg

DAY = timedelta(days=1)


def _deleted(seg_id):
    with session_scope() as s:
        return s.get(Segment, seg_id).deleted_at is not None


def _file(settings, seg_id):
    with session_scope() as s:
        return settings.recordings_dir / s.get(Segment, seg_id).path


@needs_ffmpeg
def test_age_based_retention(settings):
    settings.retention.segments_days = 7
    settings.retention.unprocessed_max_days = 14
    now = utcnow().replace(microsecond=0)
    fresh = add_segment(settings, start=now - 1 * DAY)
    old = add_segment(settings, start=now - 8 * DAY)
    old_pinned = add_segment(settings, start=now - 9 * DAY, pinned=True)
    old_waiting = add_segment(settings, start=now - 10 * DAY, status=SegmentStatus.QUEUED)
    add_job(old_waiting, state=JobState.PENDING)
    very_old_waiting = add_segment(settings, start=now - 20 * DAY, status=SegmentStatus.QUEUED)
    add_job(very_old_waiting, state=JobState.PENDING)

    result = retention.run_retention(settings)

    assert result["segments_age"] == 2
    assert not _deleted(fresh)
    assert _deleted(old) and not _file(settings, old).exists()
    assert not _deleted(old_pinned)  # part of a fox event
    assert not _deleted(old_waiting)  # GPU box offline: keep until unprocessed_max_days
    assert _deleted(very_old_waiting)


@needs_ffmpeg
def test_disk_cap_deletes_oldest_unpinned_first(settings):
    settings.retention.segments_days = 0  # only the cap applies
    now = utcnow().replace(microsecond=0)
    ids = [add_segment(settings, start=now - timedelta(minutes=10 - i)) for i in range(4)]
    with session_scope() as s:
        s.get(Segment, ids[0]).pinned = True
        sizes = [s.get(Segment, i).size_bytes for i in ids]
    # Room for roughly two files.
    settings.retention.max_disk_gb = (sum(sizes) - sizes[1] - sizes[2] + 1) / 1024**3
    retention.run_retention(settings)
    assert [_deleted(i) for i in ids] == [False, True, True, False]


@needs_ffmpeg
def test_expired_events_unpin_their_segments(settings):
    settings.retention.events_days = 30
    settings.retention.segments_days = 7
    now = utcnow().replace(microsecond=0)
    seg_id = add_segment(settings, start=now - 40 * DAY, pinned=True)
    with session_scope() as s:
        ev = Event(camera_id="cam", label="fox", first_ts=now - 40 * DAY,
                   last_ts=now - 40 * DAY, max_confidence=0.9, clip_dirty=False)
        ev.segments.append(s.get(Segment, seg_id))
        s.add(ev)
    result = retention.run_retention(settings)
    assert result["events"] == 1
    assert _deleted(seg_id)  # unpinned, then removed by age in the same run
    with session_scope() as s:
        assert s.query(Event).count() == 0


def test_events_kept_forever_by_default(settings):
    assert settings.retention.events_days == 0
    assert retention.run_retention(settings)["events"] == 0


@needs_ffmpeg
def test_recording_profile_keep_days(settings):
    from foxcam.config import CameraConfig, RecordingProfileConfig

    settings.retention.segments_days = 7
    settings.recording_profiles = [RecordingProfileConfig(id="long", keep_days=30),
                                   RecordingProfileConfig(id="short", keep_days=1)]
    settings.cameras += [
        CameraConfig.model_validate({"id": c, "recording_profile": p,
                                     "source": {"type": "test"}})
        for c, p in (("long-cam", "long"), ("short-cam", "short"))]
    now = utcnow().replace(microsecond=0)
    default_old = add_segment(settings, start=now - 8 * DAY)
    long_kept = add_segment(settings, camera="long-cam", start=now - 8 * DAY)
    long_old = add_segment(settings, camera="long-cam", start=now - 31 * DAY)
    short_old = add_segment(settings, camera="short-cam", start=now - 2 * DAY)

    retention.run_retention(settings)

    assert _deleted(default_old) and _deleted(long_old) and _deleted(short_old)
    assert not _deleted(long_kept)



@needs_ffmpeg
def test_favorite_events_are_not_expired(settings):
    settings.retention.events_days = 30
    now = utcnow().replace(microsecond=0)
    seg_id = add_segment(settings, start=now - 40 * DAY, pinned=True)
    job_id = add_job(seg_id, state=JobState.DONE)
    det_id = add_detection(seg_id, job_id, 500)
    with session_scope() as s:
        ev = Event(camera_id="cam", label="fox", first_ts=now - 40 * DAY,
                   last_ts=now - 40 * DAY, max_confidence=0.9, clip_dirty=False)
        ev.segments.append(s.get(Segment, seg_id))
        s.add(ev)
        s.flush()
        det = s.get(Detection, det_id)
        det.event_id, det.favorite = ev.id, True
    assert retention.run_retention(settings)["events"] == 0
    assert not _deleted(seg_id)  # still pinned by the event


@needs_ffmpeg
def test_starred_detections_keep_their_segment(settings):
    settings.retention.segments_days = 7
    now = utcnow().replace(microsecond=0)
    starred = add_segment(settings, start=now - 10 * DAY)
    det_id = add_detection(starred, add_job(starred, state=JobState.DONE), 500,
                           label="person", confidence=0.5, target=False)
    plain = add_segment(settings, start=now - 11 * DAY)
    add_detection(plain, add_job(plain, state=JobState.DONE), 500, label="person",
                  confidence=0.5, target=False)
    with session_scope() as s:
        s.get(Detection, det_id).favorite = True

    assert retention.run_retention(settings)["segments_age"] == 1
    assert not _deleted(starred) and _file(settings, starred).exists()
    assert _deleted(plain)

    settings.retention.segments_days = 0
    settings.retention.max_disk_gb = 1e-9  # over the cap: still kept
    retention.run_retention(settings)
    assert not _deleted(starred)
