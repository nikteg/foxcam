"""The motion report (shadow mode) and the backfill for older recordings."""

from __future__ import annotations

import time
from datetime import timedelta

from foxcam.config import ProfileConfig
from foxcam.db import session_scope
from foxcam.models import Detection, Segment, SegmentStatus, utcnow
from foxcam.pipeline import motion_report

from .conftest import add_job, add_segment, app_client, needs_ffmpeg

MOVED = [[500, [[0.1, 0.1, 0.2, 0.2]]]]


def _target(seg_id: int, job_id: int, x: float, feedback: bool | None = None,
            profile: str = "default") -> None:
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        s.add(Detection(segment_id=seg_id, job_id=job_id, profile_id=profile, camera_id="cam",
                        ts=seg.start_at, offset_ms=500, label="fox", confidence=0.9,
                        bbox_x=x, bbox_y=0.15, bbox_w=0.1, bbox_h=0.1, is_target=True,
                        feedback=feedback))


def _segment(settings, minutes_ago: int, **fields) -> int:
    return add_segment(settings, start=utcnow() - timedelta(minutes=minutes_ago),
                       with_file=False, **fields)


def _recording(settings, minutes_ago: int, **fields) -> tuple[int, int]:
    seg = _segment(settings, minutes_ago, **fields)
    return seg, add_job(seg, state="done")


def test_report_counts_what_the_options_would_do(settings):
    moving, j1 = _recording(settings, 10, motion_score=0.05, motion_boxes=MOVED)
    _target(moving, j1, 0.15, feedback=True)    # on the motion: kept
    _target(moving, j1, 0.7, feedback=False)    # elsewhere: would be dropped (a win)
    still, j2 = _recording(settings, 20, motion_score=0.0, motion_boxes=[])
    _target(still, j2, 0.15, feedback=True)     # in a still recording: skipping misses it
    unmeasured, _ = _recording(settings, 30)
    [row] = motion_report.report(settings, utcnow() - timedelta(days=1))
    assert (row["recordings"], row["measured"], row["still"]) == (3, 2, 1)
    assert row["targets"] == {"correct": 2, "wrong": 1, "unrated": 0}
    assert row["in_still"] == {"correct": 1, "wrong": 0, "unrated": 0}
    assert row["no_motion"] == {"correct": 1, "wrong": 1, "unrated": 0}
    assert motion_report.pending(utcnow() - timedelta(days=1)) == [unmeasured]


def test_report_has_a_row_per_profile(settings):
    settings.profiles.append(ProfileConfig(id="cars", targets=["car"], require_motion=True))
    seg = _segment(settings, 5, motion_score=0.1, motion_boxes=MOVED)
    add_job(seg, state="done")
    add_job(seg, state="done", profile_id="cars")
    rows = motion_report.report(settings, utcnow() - timedelta(days=1))
    assert [(r["profile_id"], r["require_motion"]) for r in rows] == [
        ("cars", True), ("default", False)]


@needs_ffmpeg
def test_backfill_measures_older_recordings(settings):
    seg = add_segment(settings, size="320x180")  # done, with testsrc2 (moving) footage
    add_job(seg, state="done")
    assert motion_report.pending(utcnow() - timedelta(days=1)) == [seg]
    with app_client(settings) as c:
        r = c.post("/api/motion/backfill", json={"days": 1})
        assert r.status_code == 200 and r.json()["started"]
        for _ in range(100):
            if not c.get("/api/motion/report").json()["backfill"]["running"]:
                break
            time.sleep(0.1)
        body = c.get("/api/motion/report?days=1").json()
        assert body["unmeasured"] == 0 and body["backfill"]["done"] == 1
        assert body["rows"][0]["measured"] == 1
        detail = c.get(f"/api/segments/{seg}").json()
        assert detail["motion_score"] > 0 and detail["motion_boxes"]


def test_profiles_that_no_longer_exist_are_left_out(settings):
    seg = _segment(settings, 5, motion_score=0.1, motion_boxes=MOVED)
    add_job(seg, state="done")
    gone = add_job(seg, state="done", profile_id="removed")
    _target(seg, gone, 0.15, profile="removed")
    rows = motion_report.report(settings, utcnow() - timedelta(days=1))
    assert [r["profile_id"] for r in rows] == ["default"]


def test_the_report_is_for_admins(settings):
    with app_client(settings, email="viewer@example.org", admin=False) as c:
        assert c.get("/api/motion/report").status_code == 403
        assert c.post("/api/motion/backfill", json={}).status_code == 403


def test_recordings_still_being_analysed_are_left_out(settings):
    seg = _segment(settings, 5, status=SegmentStatus.QUEUED, motion_score=0.1)
    add_job(seg)
    assert motion_report.report(settings, utcnow() - timedelta(days=1)) == []
