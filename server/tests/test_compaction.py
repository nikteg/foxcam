"""Compaction: segments joined into hourly archives, and everything still finds them."""

from __future__ import annotations

from datetime import timedelta

from media_streamer import ffmpeg
from media_streamer.config import RecordingProfileConfig
from media_streamer.db import session_scope
from media_streamer.models import Archive, Segment, SegmentStatus, utcnow
from media_streamer.pipeline import compaction, retention, uploader

from .conftest import add_segment, needs_ffmpeg

HOUR = timedelta(hours=1)


def _hour_ago(settings, n=3, **kw) -> list[int]:
    """n consecutive 2 s segments in a finished hour."""
    start = (utcnow() - 3 * HOUR).replace(minute=10, second=0, microsecond=0)
    return [add_segment(settings, start=start + timedelta(seconds=2 * i), **kw)
            for i in range(n)]


def _seg(seg_id: int) -> Segment:
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        s.expunge(seg)
        return seg


@needs_ffmpeg
def test_finished_hour_is_joined_without_reencoding(settings):
    ids = _hour_ago(settings)
    current = add_segment(settings, start=utcnow().replace(microsecond=0))  # hour not over

    assert compaction.compact_once(settings) == 1

    segs = [_seg(i) for i in ids]
    assert len({g.archive_id for g in segs}) == 1 and segs[0].archive_id is not None
    assert [g.archive_offset_s for g in segs] == [0.0, 2.0, 4.0]
    assert _seg(current).archive_id is None
    with session_scope() as s:
        arc = s.get(Archive, segs[0].archive_id)
        assert arc.encoder == "copy" and abs(arc.duration_s - 6) < 0.5
        assert arc.path.startswith("cam/archive/")
    # The originals stay a little while (clips being cut), then go.
    originals = [settings.recordings_dir / g.path for g in segs]
    assert all(p.exists() for p in originals)
    assert compaction.delete_replaced(settings, now=utcnow() + timedelta(minutes=5)) == 3
    assert not any(p.exists() for p in originals)

    # A segment can be cut back out, exactly.
    played = compaction.playable_file(settings, ids[1])
    assert played is not None and abs(ffmpeg.probe(played).duration_s - 2) < 0.3
    src = compaction.source_for(settings, ids[2])
    assert src.archived and src.offset_s == 4.0


@needs_ffmpeg
def test_waits_for_preprocessing(settings):
    ids = _hour_ago(settings, n=3)
    with session_scope() as s:
        s.get(Segment, ids[2]).status = SegmentStatus.NEW
    assert compaction.compact_once(settings) == 0


@needs_ffmpeg
def test_incompatible_segments_are_not_mixed(settings):
    start = (utcnow() - 3 * HOUR).replace(minute=0, second=0, microsecond=0)
    a = add_segment(settings, start=start)
    b = add_segment(settings, start=start + timedelta(seconds=2))
    c = add_segment(settings, start=start + timedelta(seconds=4), size="320x180")
    assert compaction.compact_once(settings) == 1
    assert _seg(a).archive_id == _seg(b).archive_id is not None
    assert _seg(c).archive_id is None  # alone in its run: left as it is


@needs_ffmpeg
def test_recording_profile_reencodes_with_keyframes_at_segment_starts(settings):
    settings.recording_profiles = [RecordingProfileConfig(
        id="small", transcode=True, max_height=144, bitrate_kbit=150)]
    settings.cameras[0].recording_profile = "small"
    settings.mediamtx.hwaccel = "none"  # libx264, everywhere
    ids = _hour_ago(settings, n=3, size="320x180")

    assert compaction.compact_once(settings) == 1
    with session_scope() as s:
        arc = s.get(Archive, s.get(Segment, ids[0]).archive_id)
        path, encoder, profile = settings.recordings_dir / arc.path, arc.encoder, arc.profile_id
    assert (encoder, profile) == ("libx264", "small")
    assert ffmpeg.probe(path).height == 144
    assert compaction.missing_keyframes(path, [0, 2, 4]) == []
    played = compaction.playable_file(settings, ids[1])
    assert abs(ffmpeg.probe(played).duration_s - 2) < 0.3


@needs_ffmpeg
def test_failed_reencode_falls_back_to_joining(settings, monkeypatch):
    settings.cameras[0].recording_profile = "720p"

    def broken(*_args, **_kw):
        raise ffmpeg.FfmpegError("no encoder")

    monkeypatch.setattr(compaction, "_reencode", broken)
    ids = _hour_ago(settings, n=2)
    assert compaction.compact_once(settings) == 1
    with session_scope() as s:
        assert s.get(Archive, s.get(Segment, ids[0]).archive_id).encoder == "copy"


@needs_ffmpeg
def test_clip_parts_from_an_archive(settings, tmp_path):
    ids = _hour_ago(settings)
    compaction.compact_once(settings)
    src = [compaction.source_for(settings, i) for i in ids[1:]]
    clip = tmp_path / "clip.mp4"
    ffmpeg.concat_clip([(x.path, x.offset_s, x.offset_s + x.duration_s) for x in src],
                       clip, 1.0, 2.5)
    assert abs(ffmpeg.probe(clip).duration_s - 2.5) < 0.4


@needs_ffmpeg
def test_retention_removes_archive_with_its_last_segment(settings):
    settings.retention.segments_days = 1
    start = (utcnow() - timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
    ids = [add_segment(settings, start=start + timedelta(seconds=2 * i)) for i in range(2)]
    compaction.compact_once(settings)
    with session_scope() as s:
        arc_path = settings.recordings_dir / s.get(Archive, s.get(Segment, ids[0]).archive_id).path
    assert arc_path.exists()
    result = retention.run_retention(settings)
    assert result["archives"] == 1 and not arc_path.exists()
    assert compaction.source_for(settings, ids[0]) is None


def test_uploads_archives_not_segments(settings, monkeypatch):
    uploaded = []

    class Store:
        def key(self, *parts):
            return "/".join(parts)

        def upload(self, path, key):
            uploaded.append(key)

    monkeypatch.setattr(uploader, "get_storage", lambda _s: Store())
    settings.storage.s3.recordings, settings.storage.s3.clips = True, False
    add_segment(settings, with_file=False)  # a plain segment: not uploaded on its own
    arc_file = settings.recordings_dir / "cam" / "archive" / "x.mp4"
    arc_file.parent.mkdir(parents=True)
    arc_file.write_bytes(b"x")
    with session_scope() as s:
        s.add(Archive(camera_id="cam", path="cam/archive/x.mp4", start_at=utcnow()))
    assert uploader.upload_pending(settings) == 1
    assert uploaded == ["recordings/cam/archive/x.mp4"]


@needs_ffmpeg
def test_status_for_the_status_page(settings):
    ids = _hour_ago(settings, n=3)
    st = compaction.status(settings)
    assert (st["running"], st["waiting_periods"], st["waiting_segments"]) == (False, 1, 3)

    compaction.compact_once(settings)
    compaction._write_state(settings, heartbeat_at=compaction._now_iso())
    st = compaction.status(settings)
    assert st["running"] and st["current"] is None
    assert (st["archives"], st["joined_segments"], st["waiting_periods"]) == (1, 3, 0)
    last = st["last_built"]
    assert last["camera_id"] == "cam" and last["segments"] == len(ids)
    assert last["encoder"] == "copy" and last["bytes_after"] > 0
