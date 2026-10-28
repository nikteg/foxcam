"""Targets standing still (the profile's still_minutes, pipeline/still.py) stop counting."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from foxcam.config import ProfileConfig
from foxcam.db import session_scope
from foxcam.models import Detection, Event, SegmentStatus
from foxcam.pipeline import events, queue
from sqlalchemy import select

from .conftest import add_job, add_segment

T0 = datetime(2026, 10, 25, 12, 0, 0)
PARKED = [0.40, 0.50, 0.20, 0.15]


@pytest.fixture(autouse=True)
def merge_gap(settings):
    settings.events.merge_gap_seconds = 60  # sightings 30 s apart are one event


@pytest.fixture
def cars(settings):
    settings.profiles.append(ProfileConfig(id="cars", targets=["car"]))  # 10 min by default
    return settings


def _seen(settings, minutes: range, moving: set[int] = frozenset(), box=PARKED,
          profile: str = "cars", measured: bool = True) -> None:
    """One 1-minute recording per minute with the car seen every 30 s; motion under its
    box only in the ``moving`` minutes."""
    for m in minutes:
        start = T0 + timedelta(minutes=m)
        motion = ({"motion_score": 0.05 if m in moving else 0.0,
                   "motion_boxes": [[0, [box]], [30_000, [box]]] if m in moving else []}
                  if measured else {})
        seg = add_segment(settings, start=start, seconds=60, with_file=False,
                          status=SegmentStatus.QUEUED, **motion)
        add_job(seg, profile_id=profile)
        job = queue.lease(settings, "w1")
        queue.complete(settings, job.id, job.lease_token, [
            queue.DetectionIn(offset_ms=off, label="car", confidence=0.8, bbox=box)
            for off in (0, 30_000)], model="m", processing_ms=1)
        events.assign_events(settings)


def _events():
    with session_scope() as s:
        return [(e.first_ts, e.last_ts) for e in s.scalars(select(Event).order_by(Event.id))]


def _still() -> int:
    with session_scope() as s:
        return len(s.scalars(select(Detection).where(Detection.still.is_(True))).all())


def test_a_parked_car_stops_counting_after_it_stood_still(cars):
    _seen(cars, range(30), moving={0})  # arrives, then stands there
    [(first, last)] = _events()
    assert first == T0
    # It counts for 10 minutes after it last moved, then no longer.
    assert T0 + timedelta(minutes=9) < last < T0 + timedelta(minutes=11)
    assert _still() > 30


def test_moving_again_counts_again(cars):
    _seen(cars, range(15), moving={0})
    _seen(cars, range(15, 17), moving={15}, box=[0.05, 0.50, 0.20, 0.15])  # drives off
    assert len(_events()) == 2


def test_motion_elsewhere_in_the_picture_does_not_count(cars):
    _seen(cars, range(15), moving={0})
    with session_scope() as s:  # something moving in another corner
        for d in s.scalars(select(Detection)):
            d.segment.motion_boxes = d.segment.motion_boxes or [[0, [[0.0, 0.0, 0.1, 0.1]]]]
    _seen(cars, range(15, 20))
    [(_, last)] = _events()
    assert last < T0 + timedelta(minutes=11)


def test_zero_means_it_must_be_moving_now(settings):
    settings.profiles.append(ProfileConfig(id="now", targets=["car"], still_minutes=0))
    _seen(settings, range(3), moving={1}, profile="now")
    with session_scope() as s:
        flags = [d.still for d in s.scalars(select(Detection).order_by(Detection.ts))]
    assert flags == [True, True, False, False, True, True]


def test_off_never_stops_counting(settings):
    settings.profiles.append(ProfileConfig(id="plain", targets=["car"], still_minutes=None))
    _seen(settings, range(20), moving={0}, profile="plain")
    [(_, last)] = _events()
    assert last >= T0 + timedelta(minutes=19)
    assert _still() == 0


def test_recordings_without_motion_measured_count(cars):
    _seen(cars, range(20), measured=False)
    [(_, last)] = _events()
    assert last >= T0 + timedelta(minutes=19)


def test_rebuilding_events_decides_again_and_notifies_nobody(cars):
    from foxcam.config import ChannelConfig
    from foxcam.models import Notification

    cars.profile("cars").still_minutes = None  # off while recording
    _seen(cars, range(30), moving={0})
    [(_, last)] = _events()
    assert last >= T0 + timedelta(minutes=29) and _still() == 0

    cars.profile("cars").still_minutes = 10
    cars.notifications.channels.append(
        ChannelConfig.model_validate({"id": "hook", "type": "webhook", "url": "http://hook"}))
    with session_scope() as s:
        s.query(Notification).delete()
    result = events.rebuild_events(cars)
    [(first, last)] = _events()
    assert first == T0 and last < T0 + timedelta(minutes=11)
    assert result == {"removed": 1, "created": 1, "still": _still()}
    assert _still() > 30
    with session_scope() as s:
        assert s.query(Notification).count() == 0
        assert all(e.quiet for e in s.scalars(select(Event)))


def _now(settings, boxes: list[list[float]], faint=None, motion_boxes=None) -> list[bool]:
    """A recording where a car is seen once a second at ``boxes`` with the profile wanting
    it to be moving right now; what it decided for each look (True: standing still)."""
    if settings.profile("now") is None:
        settings.profiles.append(ProfileConfig(id="now", targets=["car"], still_minutes=0))
    seg = add_segment(settings, start=T0, seconds=60, with_file=False,
                      status=SegmentStatus.QUEUED, motion_score=0.01,
                      motion_boxes=motion_boxes or [], motion_faint=faint or [])
    add_job(seg, profile_id="now")
    job = queue.lease(settings, "w1")
    queue.complete(settings, job.id, job.lease_token, [
        queue.DetectionIn(offset_ms=i * 1000, label="car", confidence=0.8, bbox=box)
        for i, box in enumerate(boxes)], model="m", processing_ms=1)
    with session_scope() as s:
        return [d.still for d in s.scalars(select(Detection).order_by(Detection.ts))]


def test_a_box_that_moves_is_moving_without_pixel_motion(settings):
    # Walking away: the box shrinks and drifts, the picture barely changes.
    boxes = [[0.80, 0.50, 0.08, 0.30], [0.80, 0.50, 0.07, 0.24], [0.80, 0.51, 0.06, 0.19]]
    assert _now(settings, boxes) == [False, False, False]


def test_a_box_that_jitters_is_still(settings):
    boxes = [PARKED, [0.401, 0.501, 0.198, 0.152], [0.399, 0.499, 0.201, 0.149]]
    assert _now(settings, boxes) == [True, True, True]


def test_faint_motion_counts_only_inside_the_box(settings):
    inside = [[1000, [[0.45, 0.55, 0.04, 0.04]]]]  # a small dog's nose
    assert _now(settings, [PARKED] * 3, faint=inside) == [False, False, False]


def test_faint_motion_beside_the_box_does_not_count(settings):
    edge = [[1000, [[0.58, 0.55, 0.1, 0.04]]]]  # overlaps the box, centre outside it
    assert _now(settings, [PARKED] * 3, faint=edge) == [True, True, True]


def test_the_detections_moving_flag_uses_the_window(settings):
    from foxcam.web import queries

    seen = [[3000, [PARKED]]]
    assert _now(settings, [PARKED], motion_boxes=seen) == [True]
    assert [d["moving"] for d in queries.detections(settings)] == [False]
    settings.motion.window_seconds = 3
    assert [d["moving"] for d in queries.detections(settings)] == [True]
