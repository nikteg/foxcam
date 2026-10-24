from datetime import timedelta

import pytest
from media_streamer.db import session_scope
from media_streamer.models import Event, Segment, SegmentStatus, utcnow
from media_streamer.pipeline import retention, uploader
from media_streamer.storage import get_storage

from .conftest import add_segment, app_client, needs_ffmpeg

moto = pytest.importorskip("moto")
BUCKET = "fox-clips"


@pytest.fixture
def s3(settings, monkeypatch):
    """Settings with S3 enabled against an in-memory moto bucket."""
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(var, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-north-1")
    with moto.mock_aws():
        cfg = settings.storage.s3
        cfg.enabled, cfg.bucket, cfg.region = True, BUCKET, "eu-north-1"
        store = get_storage(settings)
        store.client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"})
        yield store


def _keys(store):
    return sorted(o["Key"] for o in store.client.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def _event_with_clip(settings, **fields) -> int:
    clip = settings.data_dir / "clips" / "cam" / "ev.mp4"
    thumb = clip.with_suffix(".jpg")
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"clip")
    thumb.write_bytes(b"thumb")
    ts = fields.pop("ts", utcnow().replace(microsecond=0))
    with session_scope() as s:
        ev = Event(camera_id="cam", label="fox", first_ts=ts, last_ts=ts, max_confidence=0.9, clip_dirty=False,
                   clip_path="clips/cam/ev.mp4", thumb_path="clips/cam/ev.jpg", **fields)
        s.add(ev)
        s.flush()
        return ev.id


def test_check_round_trip(s3):
    assert s3.check()["ok"]
    assert _keys(s3) == []


def test_clips_upload_and_serve_from_bucket(settings, s3):
    settings.storage.s3.delete_local_clips = True
    ev_id = _event_with_clip(settings)

    assert uploader.upload_pending(settings) == 1
    assert _keys(s3) == ["media-streamer/clips/cam/ev.jpg", "media-streamer/clips/cam/ev.mp4"]
    assert not (settings.data_dir / "clips/cam/ev.mp4").exists()
    assert uploader.upload_pending(settings) == 0  # nothing left
    assert uploader.stats(settings)["clips_uploaded"] == 1

    with app_client(settings) as c:
        listed = c.get("/api/events").json()[0]
        assert listed["clip_url"] and listed["remote"]
        r = c.get(f"/api/events/{ev_id}/clip?download=true", follow_redirects=False)
        assert r.status_code == 307
        assert BUCKET in r.headers["location"] and "X-Amz-Signature" in r.headers["location"]
        assert "attachment" in r.headers["location"].replace("%3B", ";")
        # Secrets never reach the browser.
        cfg = c.get("/api/settings").json()["storage"]["s3"]
        assert cfg["secret_access_key"] is None and cfg["bucket"] == BUCKET
        status = c.get("/api/storage").json()
        assert status["enabled"] and status["clips_uploaded"] == 1
        assert c.post("/api/storage/test").json()["ok"]


def test_local_file_preferred_while_present(settings, s3):
    ev_id = _event_with_clip(settings)
    uploader.upload_pending(settings)
    with app_client(settings) as c:
        r = c.get(f"/api/events/{ev_id}/clip", follow_redirects=False)
        assert r.status_code == 200 and r.content == b"clip"


def test_rebuilt_clip_is_uploaded_again(settings, s3):
    ev_id = _event_with_clip(settings)
    uploader.upload_pending(settings)
    with session_scope() as s:  # what events.py does when an event grows
        ev = s.get(Event, ev_id)
        ev.clip_remote_key = ev.thumb_remote_key = None
    assert uploader.upload_pending(settings) == 1


def test_expired_event_removes_remote_clip(settings, s3):
    settings.retention.events_days = 30
    _event_with_clip(settings, ts=utcnow() - timedelta(days=40))
    uploader.upload_pending(settings)
    assert len(_keys(s3)) == 2
    assert retention.run_retention(settings)["events"] == 1
    assert _keys(s3) == []


@needs_ffmpeg
def test_recordings_archive_and_remote_expiry(settings, s3):
    settings.compaction.enabled = False  # per-segment uploads (see test_compaction)
    cfg = settings.storage.s3
    cfg.clips, cfg.recordings, cfg.recordings_days = False, True, 3
    now = utcnow().replace(microsecond=0)
    old = add_segment(settings, start=now - timedelta(days=5))
    new = add_segment(settings, start=now - timedelta(hours=1))
    add_segment(settings, start=now, status=SegmentStatus.QUEUED)  # not finished: skipped

    assert uploader.upload_pending(settings) == 2
    assert len(_keys(s3)) == 2

    result = retention.run_retention(settings)
    assert result["remote_recordings"] == 1
    with session_scope() as s:
        assert s.get(Segment, old).remote_key is None
        assert s.get(Segment, new).remote_key is not None
    assert len(_keys(s3)) == 1


@needs_ffmpeg
def test_deleted_recording_served_from_archive(settings, s3):
    settings.compaction.enabled = False
    settings.storage.s3.recordings = True
    seg_id = add_segment(settings)
    uploader.upload_pending(settings)
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        (settings.recordings_dir / seg.path).unlink()
        seg.deleted_at = utcnow()
    with app_client(settings) as c:
        [item] = c.get("/api/segments?include_deleted=true").json()
        assert item["deleted"] and item["remote"] and item["download_url"]
        r = c.get(f"/api/segments/{seg_id}/download", follow_redirects=False)
        assert r.status_code == 307


def test_upload_error_is_reported(settings, s3):
    settings.storage.s3.bucket = "missing-bucket"
    _event_with_clip(settings)
    assert uploader.upload_pending(settings) == 0
    assert "missing-bucket" in uploader.stats(settings)["last_error"]
