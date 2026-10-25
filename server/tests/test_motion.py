"""Motion detection (pipeline/motion.py) and what profiles do with it."""

from __future__ import annotations

import random
import subprocess
from datetime import timedelta

import pytest
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


def test_overlap_is_in_space_and_time():
    boxes = [[1000, [[0.5, 0.5, 0.1, 0.1]]]]
    assert motion.overlaps(boxes, 1500, [0.55, 0.55, 0.2, 0.2], 1000)
    assert not motion.overlaps(boxes, 1500, [0.1, 0.1, 0.2, 0.2], 1000)  # elsewhere
    assert not motion.overlaps(boxes, 3000, [0.55, 0.55, 0.2, 0.2], 1000)  # later
    assert motion.overlaps(boxes, 1200, None, 1000)  # no box: any motion counts
    assert not motion.overlaps([], 1200, None, 1000)


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
def test_required_motion_decides_the_match(settings, required):
    settings.profiles.append(ProfileConfig(id="moving", targets=["fox"],
                                           require_motion=required))
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
        if required:
            assert [(d.is_target, d.moving) for d in dets] == [(True, True), (False, False)]
        else:
            assert [(d.is_target, d.moving) for d in dets] == [(True, None), (True, None)]


def test_unmeasured_recordings_count_as_moving(settings):
    settings.profiles.append(ProfileConfig(id="moving", targets=["fox"], require_motion=True))
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
