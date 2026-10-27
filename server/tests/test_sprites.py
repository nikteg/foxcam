"""Timeline preview sprites (WebVTT + JPEG grid) for recordings, archives and clips."""

from __future__ import annotations

import re
import subprocess
from datetime import timedelta

from foxcam import sprites
from foxcam.db import session_scope
from foxcam.models import Archive, Segment, utcnow
from foxcam.pipeline import compaction, retention

from .conftest import add_segment, app_client, needs_ffmpeg


def test_vtt_maps_time_ranges_to_tiles():
    s = sprites.Sprite(interval=2, duration=5, tiles=3, columns=2, tile_w=160, tile_h=90)
    text = sprites.vtt(s, "/img.jpg")
    assert text.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:02.000\n/img.jpg#xywh=0,0,160,90" in text
    assert "00:00:02.000 --> 00:00:04.000\n/img.jpg#xywh=160,0,160,90" in text
    # The last tile ends with the recording and starts a new row.
    assert "00:00:04.000 --> 00:00:05.000\n/img.jpg#xywh=0,90,160,90" in text


def _size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                         check=True).stdout.strip()
    return tuple(int(v) for v in out.split(",")[:2])


@needs_ffmpeg
def test_segment_thumbnails_are_made_on_first_request(settings):
    seg = add_segment(settings, seconds=6, size="320x180")
    with app_client(settings) as c:
        r = c.get(f"/api/segments/{seg}/thumbnails.vtt")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/vtt")
        assert r.text.count("#xywh=") == 3  # one tile per 2 s
        assert re.search(rf"/api/segments/{seg}/sprite.jpg\?v=\d+#xywh=160,0,160,90", r.text)
        img = c.get(f"/api/segments/{seg}/sprite.jpg")
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert _size(sprites.files(settings, "segment", seg)[0]) == (480, 90)


@needs_ffmpeg
def test_archives_get_a_sprite_when_built_and_lose_it_with_the_file(settings):
    start = (utcnow() - timedelta(hours=3)).replace(minute=10, second=0, microsecond=0)
    ids = [add_segment(settings, start=start + timedelta(seconds=2 * i)) for i in range(3)]
    assert compaction.compact_once(settings) == 1
    with session_scope() as s:
        arc_id = s.get(Segment, ids[0]).archive_id
    image, meta = sprites.files(settings, "archive", arc_id)
    assert image.exists() and sprites.load(meta).tiles == 1  # 6 s, a tile per 10 s

    with app_client(settings) as c:
        assert "#xywh=0,0," in c.get(f"/api/archives/{arc_id}/thumbnails.vtt").text
        # A segment inside the archive: cut from it by its offset.
        r = c.get(f"/api/segments/{ids[1]}/thumbnails.vtt")
        assert r.status_code == 200 and "#xywh=" in r.text

    # Retention removing the archive file takes its sprite along.
    with session_scope() as s:
        for g in s.query(Segment).filter(Segment.id.in_(ids)):
            g.deleted_at = utcnow()
    assert retention._remove_empty_archives(settings) == 1
    assert not image.exists() and not meta.exists()


@needs_ffmpeg
def test_older_archives_are_filled_in_one_at_a_time(settings):
    start = (utcnow() - timedelta(hours=3)).replace(minute=10, second=0, microsecond=0)
    for i in range(3):
        add_segment(settings, start=start + timedelta(seconds=2 * i))
    compaction.compact_once(settings)
    with session_scope() as s:
        arc_id = s.query(Archive).one().id
    for path in sprites.files(settings, "archive", arc_id):
        path.unlink()
    assert sprites.backfill_archives(settings) == 1
    assert sprites.files(settings, "archive", arc_id)[0].exists()
    assert sprites.backfill_archives(settings) == 0


@needs_ffmpeg
def test_preprocessing_makes_the_segments_sprite(settings):
    from foxcam.models import SegmentStatus
    from foxcam.pipeline import preprocess

    seg = add_segment(settings, seconds=4, status=SegmentStatus.NEW)
    preprocess.preprocess_segment(settings, seg)
    image, meta = sprites.files(settings, "segment", seg)
    assert image.exists() and sprites.load(meta).tiles == 2


@needs_ffmpeg
def test_a_recordings_sprite_goes_with_its_footage(settings):
    seg = add_segment(settings, seconds=4, size="320x180")
    assert sprites.get(settings, "segment", seg) is not None
    image, meta = sprites.files(settings, "segment", seg)
    assert image.exists() and image.parent == settings.sprites_dir
    with session_scope() as s:
        retention._remove_files(settings, s.get(Segment, seg))
    assert not image.exists() and not meta.exists()
