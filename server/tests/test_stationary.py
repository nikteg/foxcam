"""Targets that stay in one place (a parked car) stop making and extending events."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from foxcam.config import ProfileConfig
from foxcam.db import session_scope
from foxcam.models import Detection, Event
from foxcam.pipeline import events
from sqlalchemy import select

from .conftest import add_job, add_segment

T0 = datetime(2026, 10, 25, 12, 0, 0)
PARKED = (0.40, 0.50, 0.20, 0.15)


@pytest.fixture(autouse=True)
def merge_gap(settings):
    settings.events.merge_gap_seconds = 60  # sightings 30 s apart are one event


@pytest.fixture
def cars(settings):
    settings.profiles.append(ProfileConfig(id="cars", targets=["car"]))  # 10 min by default
    return settings


def _seen(settings, minutes: range, box=PARKED, step_s: int = 30, profile="cars") -> None:
    """One 1-minute segment per minute, with the car seen every ``step_s`` seconds."""
    for m in minutes:
        start = T0 + timedelta(minutes=m)
        seg = add_segment(settings, start=start, seconds=60, with_file=False)
        job = add_job(seg, profile_id=profile)
        with session_scope() as s:
            for off in range(0, 60, step_s):
                x, y, w, h = box
                s.add(Detection(segment_id=seg, job_id=job, profile_id=profile,
                                camera_id="cam", ts=start + timedelta(seconds=off),
                                offset_ms=off * 1000, label="car", confidence=0.8,
                                bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h, is_target=True))
        events.assign_events(settings)


def _events():
    with session_scope() as s:
        return [(e.first_ts, e.last_ts) for e in s.scalars(select(Event).order_by(Event.id))]


def _stationary() -> int:
    with session_scope() as s:
        return len(s.scalars(select(Detection).where(Detection.stationary.is_(True))).all())


def test_a_parked_car_stops_extending_its_event(cars):
    _seen(cars, range(30))
    [(first, last)] = _events()
    assert first == T0
    # Arriving counts; after 10 minutes in the same place it no longer does.
    assert last < T0 + timedelta(minutes=11)
    assert _stationary() > 30


def test_moving_on_counts_again(cars):
    _seen(cars, range(15))
    _seen(cars, range(15, 17), box=(0.05, 0.50, 0.20, 0.15))  # drives off to the left
    assert len(_events()) == 2


def test_a_long_absence_breaks_the_chain(cars):
    _seen(cars, range(8))
    _seen(cars, range(20, 28))  # gone for 12 minutes, then back in the same spot
    assert len(_events()) == 2
    assert _stationary() == 0


def test_zero_turns_it_off(settings):
    settings.profiles.append(ProfileConfig(id="plain", targets=["car"],
                                           ignore_stationary_minutes=0))
    _seen(settings, range(20), profile="plain")
    [(_, last)] = _events()
    assert last >= T0 + timedelta(minutes=19)
    assert _stationary() == 0


def test_rebuilding_events_checks_older_detections_and_notifies_nobody(cars):
    from foxcam.config import ChannelConfig
    from foxcam.models import Notification

    cars.profile("cars").ignore_stationary_minutes = 0  # as before the check existed
    _seen(cars, range(30))
    [(_, last)] = _events()
    assert last >= T0 + timedelta(minutes=29) and _stationary() == 0

    cars.profile("cars").ignore_stationary_minutes = 10
    cars.notifications.channels.append(
        ChannelConfig.model_validate({"id": "hook", "type": "webhook", "url": "http://hook"}))
    with session_scope() as s:
        s.query(Notification).delete()
    result = events.rebuild_events(cars)
    [(first, last)] = _events()
    assert first == T0 and last < T0 + timedelta(minutes=11)
    assert result == {"removed": 1, "created": 1, "stationary": _stationary()}
    assert _stationary() > 30
    with session_scope() as s:
        assert s.query(Notification).count() == 0
        assert all(e.quiet for e in s.scalars(select(Event)))
