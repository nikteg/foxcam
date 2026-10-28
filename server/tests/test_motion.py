"""Motion detection (pipeline/motion.py) and what profiles do with it."""

from __future__ import annotations

import random
import subprocess
from datetime import timedelta

import pytest
from foxcam import ffmpeg
from foxcam.config import ProfileConfig
from foxcam.db import session_scope
from foxcam.models import Detection, Job, JobState, Segment, SegmentStatus, utcnow
from foxcam.pipeline import motion, preprocess, queue
from foxcam.web.queries import save_motion

from .conftest import add_job, add_segment, app_client, needs_ffmpeg, recording_path

W, H = motion.WIDTH, motion.HEIGHT


def frame(level: int = 80, square: tuple[int, int, int] | None = None, noise: int = 0,
          rng: random.Random | None = None) -> bytes:
    """A flat grey frame, optionally with a bright ``(x, y, size)`` square and noise."""
    rng = rng or random.Random(1)
    px = bytearray(level for _ in range(W * H))
    if square:
        x0, y0, size = square
        for y in range(y0, y0 + size):
            for x in range(x0, x0 + size):
                px[y * W + x] = 230
    if noise:
        for i in range(W * H):
            px[i] = max(0, min(255, px[i] + rng.randint(-noise, noise)))
    return bytes(px)


def test_nothing_moves_in_a_still_picture():
    found = motion.analyse([frame()] * 10, fps=2)
    assert found.score == 0 and found.boxes == []


def test_sensor_noise_is_not_motion():
    rng = random.Random(7)
    found = motion.analyse([frame(noise=8, rng=rng) for _ in range(10)], fps=2)
    assert found.score == 0


def test_a_moving_object_is_found_where_it_is():
    frames = [frame(square=(10 + 6 * i, 20, 8)) for i in range(6)]
    found = motion.analyse(frames, fps=2)
    assert found.score > 0
    at, boxes = found.boxes[-1]
    assert at == 2500  # frame 5 at 2 fps
    # The square is at x 40..48 of 96 then (and its previous place, 34..42, fading).
    e = 0.001  # boxes are rounded
    assert any(x <= 40 / W + e and x + w >= 48 / W - e and y <= 20 / H + e
               and y + h >= 28 / H - e for x, y, w, h in boxes)


def test_a_light_change_is_not_motion():
    frames = [frame(level=60)] * 3 + [frame(level=180)] * 3  # IR switching on
    assert motion.analyse(frames, fps=2).score == 0


def test_a_flickering_lamp_is_light_not_motion():
    # A lamp with a darker surround on a textured wall, blinking.
    def lamp(on: bool) -> bytes:
        px = bytearray(((x // 3 + y // 3) % 2) * 40 + 60 for y in range(H) for x in range(W))
        for y in range(4, 10):
            for x in range(40, 56):
                px[y * W + x] = min(255, px[y * W + x] + (120 if on else 60))
        return bytes(px)

    assert motion.analyse([lamp(i % 2 == 0) for i in range(10)], fps=2).score == 0


def test_an_edge_wobbling_by_a_pixel_is_not_motion():
    # A sharp vertical edge (a door) that shifts one pixel back and forth.
    def edge(x0: int) -> bytes:
        return bytes(220 if x >= x0 else 30 for _ in range(H) for x in range(W))

    assert motion.analyse([edge(48 + i % 2) for i in range(10)], fps=2).score == 0


def test_ignored_areas_never_count():
    frames = [frame(square=(10 + 6 * i, 20, 8)) for i in range(6)]
    found = motion.analyse(frames, fps=2, ignore=[[0.0, 0.2, 1.0, 0.5]])
    assert found.score == 0


def test_sensitivity_sets_the_smallest_thing_that_counts():
    frames = [frame(square=(10 + 6 * i, 20, 2)) for i in range(6)]  # a small blob
    assert motion.analyse(frames, fps=2, sensitivity="high").score > 0
    assert motion.analyse(frames, fps=2, sensitivity="low").score == 0


def test_what_misses_the_sensitivity_is_kept_as_faint_motion():
    frames = [frame(square=(10 + 6 * i, 20, 2)) for i in range(6)]  # too small for "low"
    found = motion.analyse(frames, fps=2, sensitivity="low")
    assert found.score == 0 and found.boxes == []
    assert found.faint  # counts for a detection's box it lies in (pipeline/still.py)
    assert motion.analyse([frame()] * 10, fps=2).faint == []  # nothing moved at all


def test_a_step_then_standing_is_faint_motion():
    # Moves once and stays (a dog sniffing around): it differs from the frame before, not
    # from the one after, like the place something just left.
    frames = [frame(square=(10, 20, 6))] * 3 + [frame(square=(30, 20, 6))] * 5
    found = motion.analyse(frames, fps=2)
    assert found.score == 0
    assert [at for at, _ in found.faint] == [1500]


def _still_segment(settings) -> int:
    """A recording of a grey wall."""
    start = utcnow().replace(microsecond=0)
    path = recording_path(settings, "cam", start)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=gray:size=320x180:rate=10", "-t", "2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)
    return add_segment(settings, start=start, status=SegmentStatus.NEW, with_file=False)


@pytest.fixture
def skipping(settings):
    # The only profile (replacing the built-in default one).
    settings.profiles[:] = [ProfileConfig(id="quiet", targets=["fox"], default=True,
                                          skip_without_motion=True)]
    return settings


@needs_ffmpeg
def test_preprocess_measures_motion(settings):
    seg_id = add_segment(settings, status=SegmentStatus.NEW, size="320x180")  # testsrc2 moves
    preprocess.preprocess_segment(settings, seg_id)
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        assert seg.status == SegmentStatus.QUEUED
        assert seg.motion_score > 0 and seg.motion_boxes


@needs_ffmpeg
def test_recordings_without_motion_are_skipped(skipping):
    seg_id = _still_segment(skipping)
    preprocess.preprocess_segment(skipping, seg_id)
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        assert seg.motion_score == 0
        assert seg.status == SegmentStatus.DONE  # every job skipped
        [job] = s.query(Job).filter_by(segment_id=seg_id).all()
        assert job.state == JobState.SKIPPED and "no motion" in job.error


@needs_ffmpeg
def test_motion_settings_of_the_camera_apply(skipping):
    # The whole picture ignored: testsrc2's motion no longer counts.
    save_motion("cam", "high", [[0.0, 0.0, 1.0, 1.0]])
    seg_id = add_segment(skipping, status=SegmentStatus.NEW, size="320x180")
    preprocess.preprocess_segment(skipping, seg_id)
    with session_scope() as s:
        assert s.get(Segment, seg_id).motion_score == 0
        assert s.get(Segment, seg_id).status == SegmentStatus.DONE


@pytest.mark.parametrize("required", [True, False])
def test_must_be_moving_now_decides_the_match(settings, required):
    settings.profiles.append(ProfileConfig(id="moving", targets=["fox"],
                                           still_minutes=0 if required else None))
    seg_id = add_segment(settings, start=utcnow() - timedelta(minutes=1), with_file=False,
                         status=SegmentStatus.QUEUED, motion_score=0.02,
                         motion_boxes=[[500, [[0.1, 0.1, 0.2, 0.2]]]])
    add_job(seg_id, profile_id="moving")
    job = queue.lease(settings, "w1")
    queue.complete(settings, job.id, job.lease_token, [
        queue.DetectionIn(offset_ms=500, label="fox", confidence=0.9, bbox=[0.15, 0.15, 0.1, 0.1]),
        queue.DetectionIn(offset_ms=500, label="fox", confidence=0.9, bbox=[0.7, 0.7, 0.1, 0.1]),
    ], model="m", processing_ms=1)
    with session_scope() as s:
        dets = s.query(Detection).order_by(Detection.id).all()
        if required:  # the one beside the motion stands still: no match
            assert [(d.is_target, d.still) for d in dets] == [(True, False), (True, True)]
        else:
            assert [(d.is_target, d.still) for d in dets] == [(True, None), (True, None)]


def test_unmeasured_recordings_count_as_moving(settings):
    settings.profiles.append(ProfileConfig(id="moving", targets=["fox"], still_minutes=0))
    seg_id = add_segment(settings, with_file=False, status=SegmentStatus.QUEUED)
    add_job(seg_id, profile_id="moving")
    job = queue.lease(settings, "w1")
    queue.complete(settings, job.id, job.lease_token, [
        queue.DetectionIn(offset_ms=500, label="fox", confidence=0.9, bbox=[0.7, 0.7, 0.1, 0.1]),
    ], model="m", processing_ms=1)
    with session_scope() as s:
        assert s.query(Detection).one().is_target


def test_motion_settings_are_saved_per_camera(settings):
    with app_client(settings) as c:
        r = c.put("/api/cameras/cam/motion",
                  json={"sensitivity": "low", "rects": [[0.5, 0.5, 0.8, 0.2]]})
        assert r.status_code == 200, r.text
        assert r.json()["motion_sensitivity"] == "low"
        assert r.json()["motion_rects"] == [[0.5, 0.5, 0.5, 0.2]]  # clipped to the picture
        cam = next(x for x in c.get("/api/cameras").json() if x["id"] == "cam")
        assert cam["motion_sensitivity"] == "low"
        r = c.put("/api/cameras/cam/motion", json={"sensitivity": None, "rects": []})
        assert r.json()["motion_sensitivity"] is None and r.json()["motion_rects"] == []
        assert c.put("/api/cameras/cam/motion", json={"sensitivity": "max"}).status_code == 422


@needs_ffmpeg
def test_sampled_frames_show_their_moment(tmp_path):
    """Frame n of a sampled video is the picture at n / fps, the moment a seek there shows
    (ffmpeg's default rounding took one up to half an interval later: boxes trailed targets)."""
    src, derived = tmp_path / "src.mp4", tmp_path / "derived.mp4"
    # 10 fps, frame k a flat grey of 16 + 6k: the level tells which source frame it is.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "nullsrc=size=96x54:rate=10:duration=3,format=gray,geq=lum=16+N*6",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(src)], check=True)

    def source_frames(path, fps):
        return [round((sum(f) / len(f) - 16) / 6) for f in motion.decode(path, fps)]

    assert source_frames(src, 2) == [0, 5, 10, 15, 20, 25]
    ffmpeg.make_masked_derivative(src, derived, [], 2, 1280)
    assert source_frames(derived, 2) == [0, 5, 10, 15, 20, 25]
    assert source_frames(derived, 1) == [0, 10, 20]
    # A detection's image is the same picture: the last frame at or before the moment.
    thumb = tmp_path / "thumb.jpg"
    ffmpeg.thumbnail(src, thumb, 0.55, 96)
    assert source_frames(thumb, 1) == [5]


def test_box_label_is_safe_for_the_filter():
    """The label on a notification picture's box: nothing ffmpeg's filter syntax would
    read (':' ',' quotes), kept inside the picture, and none for an empty label."""
    tag = ffmpeg._box_tag(0.3, 0.0, "fox's: 94%,", 960)
    assert "text='foxs 94%'" in tag and "expansion=none" in tag
    assert "y=max(H*0.00000-th-8\\,4)" in tag
    assert ffmpeg._box_tag(0.3, 0.2, "::", 960) is None
