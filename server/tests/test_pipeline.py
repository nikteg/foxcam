"""Scanner + preprocess + events, with real (tiny) video files."""

import os
import subprocess
import time
from datetime import UTC, datetime, timedelta, timezone

from foxcam import ffmpeg
from foxcam.db import session_scope
from foxcam.models import Event, Job, Segment, SegmentStatus, utcnow
from foxcam.pipeline import events, preprocess, scanner
from foxcam.web.queries import save_camera_state

from .conftest import (
    add_detection,
    add_job,
    add_segment,
    app_client,
    make_video,
    needs_ffmpeg,
    recording_path,
)


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


@needs_ffmpeg
def test_scanner_registers_only_finished_files(settings):
    start = utcnow().replace(microsecond=0) - timedelta(seconds=10)
    first = make_video(recording_path(settings, "cam", start))
    current = make_video(recording_path(settings, "cam", start + timedelta(seconds=5)))
    assert scanner.scan_once(settings) == 1  # newest file may still be recording
    _age(current, 60)  # untouched for longer than segment_seconds + grace
    assert scanner.scan_once(settings) == 1
    assert scanner.scan_once(settings) == 0  # idempotent
    with session_scope() as s:
        segs = s.query(Segment).order_by(Segment.start_at).all()
        assert [g.start_at for g in segs] == [start, start + timedelta(seconds=5)]
        assert all(g.status == SegmentStatus.NEW and g.duration_s > 1.5 for g in segs)
    assert first.exists()


def test_recording_names_are_converted_to_utc(tmp_path):
    utc = datetime(2026, 10, 24, 12, 0, 35, 560256)
    assert scanner.parse_start(tmp_path / "2026-10-24_14-00-35-560256+0200.mp4") == utc
    assert scanner.parse_start(tmp_path / "2026-10-24_12-00-35-560256+0000.mp4") == utc
    assert scanner.parse_start(tmp_path / "2026-10-24_12-00-35-560256.mp4") == utc  # legacy
    assert scanner.parse_start(tmp_path / "notes.mp4") is None


def test_finished_files_sort_by_time_across_dst(settings):
    cam_dir = settings.recordings_dir / "cam"
    cam_dir.mkdir(parents=True)
    # Clocks go back at 03:00 CEST: 02:50+0200 (00:50Z) comes before 02:10+0100 (01:10Z).
    names = ["2026-10-25_02-50-00-000000+0200.mp4", "2026-10-25_02-10-00-000000+0100.mp4"]
    for name in names:
        (cam_dir / name).touch()
        _age(cam_dir / name, 600)
    assert [p.name for p in scanner.finished_files(settings, "cam")] == names


@needs_ffmpeg
def test_preprocess_censors_tags_night_and_queues(settings):
    save_camera_state("cam", [[0.0, 0.0, 0.5, 1.0]], private=False)  # black out left half
    start = utcnow().replace(microsecond=0)
    path = make_video(recording_path(settings, "cam", start), gray=True, size="320x180")
    with session_scope() as s:
        seg = Segment(camera_id="cam", path=str(path.relative_to(settings.recordings_dir)),
                      start_at=start, duration_s=2, status=SegmentStatus.NEW)
        s.add(seg)
        s.flush()
        seg_id = seg.id
    preprocess.preprocess_segment(settings, seg_id)
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        assert seg.status == SegmentStatus.QUEUED
        assert seg.is_night is True  # grayscale = IR night look
        assert seg.mask_version_id is not None and seg.thumb_path
        assert s.query(Job).filter_by(segment_id=seg_id).count() == 1
        derived = settings.data_dir / seg.derived_path
    info = ffmpeg.probe(derived)
    assert info.width == 320
    # Left half must be pure black in the worker input; the right half is not.
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(derived), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    w = 320
    left = [raw[y * w + x] for y in range(0, 180, 10) for x in range(0, 150, 10)]
    right = [raw[y * w + x] for y in range(0, 180, 10) for x in range(170, 320, 10)]
    assert max(left) <= 20 and max(right) > 60


@needs_ffmpeg
def test_private_camera_never_gets_a_job(settings):
    save_camera_state("cam", [], private=True)
    seg_id = add_segment(settings, status=SegmentStatus.NEW)
    preprocess.preprocess_segment(settings, seg_id)
    with session_scope() as s:
        assert s.get(Segment, seg_id).status == SegmentStatus.PRIVATE
        assert s.query(Job).count() == 0


@needs_ffmpeg
def test_fox_segments_merge_into_one_event_with_clip(settings):
    start = utcnow().replace(microsecond=0) - timedelta(minutes=5)
    segs = [add_segment(settings, start=start + timedelta(seconds=2 * i)) for i in range(4)]
    for seg_id in segs:
        add_job(seg_id, state="done")
    # Fox in segments 1 and 2 (adjacent), nothing in 0 and 3.
    add_detection(segs[1], segs[1], 1500, confidence=0.7)
    add_detection(segs[2], segs[2], 500, confidence=0.95)
    with session_scope() as s:
        for seg_id in segs[1:3]:
            s.get(Segment, seg_id).has_target = True

    assert events.assign_events(settings) == 2
    assert events.build_clips(settings) == 1
    with session_scope() as s:
        ev = s.query(Event).one()
        assert ev.max_confidence == 0.95 and ev.detection_count == 2
        assert ev.clip_path and not ev.clip_dirty
        # The thumbnail is the best detection's frame; its box is drawn by the web app.
        assert ev.thumb_path and ev.thumb_box == [0.4, 0.4, 0.2, 0.2]
        clip = settings.data_dir / ev.clip_path
        pinned = [s.get(Segment, i).pinned for i in segs]
    # Fox from 3.5 s to 4.5 s, ±1 s roll -> clip 2.5..5.5 s, inside segments 1 and 2.
    assert clip.exists() and 2.0 < ffmpeg.probe(clip).duration_s < 4.5
    assert pinned == [False, True, True, False]

    # An event's own detections (Events -> Detections, "Show its detections").
    from foxcam.web import queries
    add_detection(segs[3], segs[3], 100, label="cat")
    with session_scope() as s:
        ev_id = s.query(Event).one().id
    assert len(queries.detections(settings, event_id=ev_id)) == 2
    assert len(queries.detections(settings)) == 3


@needs_ffmpeg
def test_a_clip_keeps_the_gap_between_its_recordings(settings):
    """The recorder stopped for 5 s between two recordings: the clip holds the picture
    there instead of joining them back to back, so its time stays the clock's (the
    event's moments, motion) and the gap is marked."""
    settings.events.pre_roll_seconds = 1
    settings.events.post_roll_seconds = 1
    start = utcnow().replace(microsecond=0) - timedelta(minutes=5)
    first = add_segment(settings, start=start, seconds=2)
    second = add_segment(settings, start=start + timedelta(seconds=7), seconds=2)
    for seg_id, at in ((first, 1500), (second, 500)):
        add_detection(seg_id, add_job(seg_id, state="done"), at)
        with session_scope() as s:
            s.get(Segment, seg_id).has_target = True
    events.assign_events(settings)
    with session_scope() as s:  # one event across the gap
        s.query(Event).update({Event.first_ts: start + timedelta(seconds=1.5),
                               Event.last_ts: start + timedelta(seconds=7.5)})
        s.query(Event).filter(Event.id != s.query(Event.id).limit(1).scalar_subquery()) \
            .delete(synchronize_session=False)
    assert events.build_clips(settings) == 1
    with session_scope() as s:
        ev = s.query(Event).one()
        clip, gaps = settings.data_dir / ev.clip_path, ev.clip_gaps
    # 0.5 s .. 8.5 s after the first recording's start, the gap from 2 s to 7 s in it.
    assert gaps == [[1.5, 6.5, None]]
    assert 7.0 < ffmpeg.probe(clip).duration_s < 8.5


@needs_ffmpeg
def test_pre_roll_reaches_into_previous_segment(settings):
    settings.events.pre_roll_seconds = 3
    start = utcnow().replace(microsecond=0) - timedelta(minutes=5)
    segs = [add_segment(settings, start=start + timedelta(seconds=2 * i)) for i in range(3)]
    add_detection(segs[1], add_job(segs[1], state="done"), 200)
    with session_scope() as s:
        s.get(Segment, segs[1]).has_target = True
    events.assign_events(settings)
    events.build_clips(settings)
    with session_scope() as s:
        assert [s.get(Segment, i).pinned for i in segs] == [True, True, False]


@needs_ffmpeg
def test_events_marked_wrong_can_be_left_out(settings):
    from foxcam.web import queries

    start = utcnow().replace(microsecond=0) - timedelta(minutes=30)
    segs = [add_segment(settings, start=start + timedelta(minutes=10 * i)) for i in range(3)]
    dets = []
    for seg_id in segs:  # far apart: three events
        add_job(seg_id, state="done")
        dets.append(add_detection(seg_id, seg_id, 500))
        with session_scope() as s:
            s.get(Segment, seg_id).has_target = True
    events.assign_events(settings)
    queries.set_feedback(settings, dets[0], False)  # a wrong one
    queries.set_feedback(settings, dets[1], True)  # a right one
    by_id = {e["best_detection_id"]: e for e in queries.events()}
    assert [by_id[d]["feedback"] for d in dets] == ["wrong", "correct", None]
    shown = {e["best_detection_id"] for e in queries.events(hide_wrong=True)}
    assert shown == {dets[1], dets[2]}
    # The recording with only a wrong match has no match any more (archives count these),
    # and clearing the feedback brings it back.
    with session_scope() as s:
        assert [s.get(Segment, i).has_target for i in segs] == [False, True, True]
    assert [j["targets"] for j in queries.segment_jobs(settings, segs[0])] == [0]
    queries.set_feedback(settings, dets[0], None)
    with session_scope() as s:
        assert s.get(Segment, segs[0]).has_target is True


def test_an_event_marked_wrong_is_no_match_in_any_of_its_recordings(settings):
    from foxcam.web import queries

    start = utcnow().replace(microsecond=0) - timedelta(minutes=5)
    segs = [add_segment(settings, start=start + timedelta(seconds=2 * i), with_file=False)
            for i in range(2)]
    dets = []
    for seg_id in segs:  # close together: one event over both recordings
        add_job(seg_id, state="done")
        dets.append(add_detection(seg_id, seg_id, 500))
        with session_scope() as s:
            s.get(Segment, seg_id).has_target = True
    events.assign_events(settings)

    def matches() -> list[bool]:
        with session_scope() as s:
            return [s.get(Segment, i).has_target for i in segs]

    # Marking the event wrong (its drawer marks one detection) clears both recordings.
    queries.set_feedback(settings, dets[0], False)
    assert matches() == [False, False]
    # Another detection marked correct: the event is a real visit after all (only the
    # detection marked wrong itself stays no match).
    queries.set_feedback(settings, dets[1], True)
    assert matches() == [False, True]


def test_detections_standing_still_can_be_left_out(settings):
    from foxcam.models import Detection
    from foxcam.web import queries

    seg = add_segment(settings)
    add_job(seg, state="done")
    moving, still = add_detection(seg, seg, 0), add_detection(seg, seg, 1000)
    with session_scope() as s:
        s.get(Detection, still).still = True
    assert {d["id"]: d["still"] for d in queries.detections(settings)} == {moving: False, still: True}
    assert [d["id"] for d in queries.detections(settings, hide_still=True)] == [moving]


def test_events_and_detections_filter_by_time_light_and_feedback(settings):
    from foxcam.web import queries

    start = utcnow().replace(microsecond=0, second=0) - timedelta(hours=3)
    segs = [add_segment(settings, start=start + timedelta(hours=i), with_file=False,
                        is_night=i == 2) for i in range(3)]
    dets = []
    for seg_id in segs:  # an hour apart: three events
        add_job(seg_id, state="done")
        dets.append(add_detection(seg_id, seg_id, 500))
        with session_scope() as s:
            s.get(Segment, seg_id).has_target = True
    events.assign_events(settings)
    with session_scope() as s:  # what building the clip sets (no footage here)
        for ev in s.query(Event):
            ev.is_night = ev.best_detection_id == dets[2]
    queries.set_feedback(settings, dets[0], False)
    queries.set_feedback(settings, dets[1], True)
    with app_client(settings) as client:
        def ids(path: str) -> list[int]:
            rows = client.get(path).json()
            return sorted(r.get("best_detection_id", r["id"]) for r in rows)

        # A time with a zone is taken as that moment; one without is UTC.
        since = (start + timedelta(minutes=30)).isoformat() + "Z"
        until = (start + timedelta(hours=1, minutes=30)).isoformat()
        for kind in ("events", "detections"):
            assert ids(f"/api/{kind}?since={since}&until={until}") == [dets[1]]
            assert ids(f"/api/{kind}?since={since}") == dets[1:]
            assert ids(f"/api/{kind}?light=night") == [dets[2]]
            assert ids(f"/api/{kind}?light=day") == dets[:2]
            assert ids(f"/api/{kind}?feedback=wrong") == [dets[0]]
            assert ids(f"/api/{kind}?feedback=correct") == [dets[1]]
            assert ids(f"/api/{kind}?feedback=none") == [dets[2]]
        zoned = (start + timedelta(minutes=30)).replace(tzinfo=UTC).astimezone(
            timezone(timedelta(hours=2))).isoformat()
        assert ids("/api/events?since=" + zoned.replace("+", "%2B")) == dets[1:]


def test_reanalyse_a_time_range(settings):
    from .conftest import app_client

    start = utcnow().replace(microsecond=0) - timedelta(hours=2)
    segs = [add_segment(settings, start=start + timedelta(minutes=30 * i), status="done",
                        with_file=False) for i in range(4)]
    for seg in segs:
        add_detection(seg, add_job(seg, state="done"), 500)
    body = {"start": (start + timedelta(minutes=29)).isoformat() + "Z",
            "end": (start + timedelta(minutes=89)).isoformat() + "Z"}
    with app_client(settings) as c:
        r = c.post("/api/segments/reanalyse", json=body)
        assert r.status_code == 200, r.text
        assert r.json() == {"queued": 2, "skipped": 0, "detections_removed": 2}
        assert c.post("/api/segments/reanalyse", json={**body, "end": body["start"]}) \
            .status_code == 422
    with session_scope() as s:
        assert [s.get(Segment, i).status for i in segs] == ["done", "new", "new", "done"]


def test_weak_non_matches_are_not_kept(settings):
    from foxcam.models import Detection
    from foxcam.pipeline import retention

    seg = add_segment(settings)
    job = add_job(seg, state="done")
    weak = add_detection(seg, job, 0, label="cat", confidence=0.18, target=False)
    rated = add_detection(seg, job, 100, label="cat", confidence=0.18, target=False)
    kept = add_detection(seg, job, 200, label="cat", confidence=0.4, target=False)
    match = add_detection(seg, job, 300, confidence=0.2)  # a match stays, however weak
    with session_scope() as s:
        s.get(Detection, rated).feedback = False
    settings.processing.keep_non_targets_from = 0.25
    assert retention.run_retention(settings)["weak_detections"] == 1
    with session_scope() as s:
        assert sorted(d.id for d in s.query(Detection)) == sorted([rated, kept, match])
    assert weak not in (rated, kept, match)


@needs_ffmpeg
def test_scanner_drops_the_fragment_before_a_recorder_reset(settings):
    settings.segment_seconds = 1800
    start = utcnow().replace(microsecond=0) - timedelta(minutes=10)
    fragment = make_video(recording_path(settings, "cam", start), seconds=2)
    after = make_video(recording_path(settings, "cam", start + timedelta(seconds=4)), seconds=3)
    # A short file after an outage (long gap) is a real recording.
    lone = make_video(recording_path(settings, "cam", start + timedelta(minutes=2)), seconds=2)
    make_video(recording_path(settings, "cam", start + timedelta(minutes=5)))  # recording
    assert scanner.scan_once(settings) == 2
    assert not fragment.exists() and after.exists() and lone.exists()
    with session_scope() as s:
        assert sorted(g.start_at for g in s.query(Segment)) == [
            start + timedelta(seconds=4), start + timedelta(minutes=2)]


@needs_ffmpeg
def test_preprocessing_status_shows_the_waiting_and_the_pace(settings, monkeypatch):
    monkeypatch.setattr(preprocess, "activity", preprocess.Activity())  # not other tests' runs
    start = utcnow().replace(microsecond=0)
    older = add_segment(settings, start=start - timedelta(minutes=5), status=SegmentStatus.NEW)
    newer = add_segment(settings, start=start, status=SegmentStatus.NEW)
    with app_client(settings) as client:
        p = client.get("/api/preparation").json()
    assert p["waiting"] == 2 and p["oldest_waiting_age_s"] >= 300
    # In the order they are prepared: the newest first.
    assert [g["id"] for g in p["segments"]] == [newer, older]
    assert p["segments"][0]["started_at"] is None

    preprocess.preprocess_segment(settings, newer)
    with app_client(settings) as client:
        p = client.get("/api/preparation").json()
    assert p["waiting"] == 1 and [g["id"] for g in p["segments"]] == [older]
    assert p["done_last_hour"] == 1 and p["avg_seconds_last_hour"] is not None


def test_targets_below_the_sun_limit_are_no_match(settings):
    from foxcam.models import Detection
    from foxcam.pipeline import daylight
    from foxcam.web import queries

    day, night = datetime(2026, 6, 21, 11), datetime(2026, 6, 21, 23)  # UTC, at 58° N
    segs = [add_segment(settings, start=t, with_file=False) for t in (day, night)]
    dets = []
    for seg_id in segs:
        add_job(seg_id, state="done")
        dets.append(add_detection(seg_id, seg_id, 500))
        with session_scope() as s:
            s.get(Segment, seg_id).has_target = True
    events.assign_events(settings)

    def state() -> list[tuple[bool, bool]]:
        with session_scope() as s:
            return [(s.get(Detection, d).event_id is not None, s.get(Segment, g).has_target)
                    for d, g in zip(dets, segs)]

    # A limit without a location ignores nothing.
    queries.save_sun("cam", -3.0, None, None)
    assert daylight.apply(settings, "cam") == 0
    assert state() == [(True, True), (True, True)]
    # The night one leaves its event (which goes) and its recording has no match.
    queries.save_sun("cam", -3.0, 58.0, 12.0)
    assert daylight.apply(settings, "cam") == 1
    assert state() == [(True, True), (False, False)]
    with session_scope() as s:
        assert s.query(Event).count() == 1
    chart = daylight.chart(settings, "cam", 90)
    assert chart["located"] and chart["limit"] == -3.0 and chart["sun"]
    # Off again: it forms an event again.
    queries.save_sun("cam", None, 58.0, 12.0)
    assert daylight.apply(settings, "cam") == 1
    assert state() == [(True, True), (True, True)]


def test_favorites_and_bulk_changes_through_the_api(settings):
    start = utcnow().replace(microsecond=0) - timedelta(minutes=30)
    segs = [add_segment(settings, start=start + timedelta(minutes=10 * i), with_file=False)
            for i in range(3)]
    dets = []
    for seg_id in segs:  # far apart: three events
        add_job(seg_id, state="done")
        dets.append(add_detection(seg_id, seg_id, 500))
        with session_scope() as s:
            s.get(Segment, seg_id).has_target = True
    events.assign_events(settings)
    with app_client(settings) as client:
        ev_ids = {e["best_detection_id"]: e["id"] for e in client.get("/api/events").json()}
        evs = [ev_ids[d] for d in dets]

        # A star on a detection makes its event a favorite; both can be filtered on.
        assert client.post(f"/api/detections/{dets[0]}/favorite",
                           json={"favorite": True}).status_code == 200
        assert [d["id"] for d in client.get("/api/detections?favorite=true").json()] == [dets[0]]
        assert [e["id"] for e in client.get("/api/events?favorite=true").json()] == [evs[0]]
        # Starring an event stars its best detection; taking it off clears them all.
        client.post(f"/api/events/{evs[1]}/favorite", json={"favorite": True})
        favs = client.get("/api/detections?favorite=true").json()
        assert {d["id"] for d in favs} == {dets[0], dets[1]}
        client.post(f"/api/events/{evs[0]}/favorite", json={"favorite": False})
        assert [e["id"] for e in client.get("/api/events?favorite=true").json()] == [evs[1]]

        # Bulk: events marked wrong and starred at once.
        r = client.post("/api/events/bulk", json={"ids": evs[:2], "feedback": False,
                                                  "favorite": True})
        assert r.json() == {"changed": 2}
        by_id = {e["id"]: e for e in client.get("/api/events").json()}
        assert [(by_id[i]["feedback"], by_id[i]["favorite"]) for i in evs] == [
            ("wrong", True), ("wrong", True), (None, False)]
        with session_scope() as s:
            assert [s.get(Segment, i).has_target for i in segs] == [False, False, True]
        # Detections: feedback cleared (null), the star left alone.
        r = client.post("/api/detections/bulk", json={"ids": dets, "feedback": None})
        assert r.json() == {"changed": 3}
        by_id = {d["id"]: d for d in client.get("/api/detections").json()}
        assert [(by_id[i]["feedback"], by_id[i]["favorite"]) for i in dets] == [
            (None, True), (None, True), (None, False)]
        # Nothing to change, or an unknown id: refused.
        assert client.post("/api/detections/bulk", json={"ids": dets}).status_code == 422
        assert client.post("/api/detections/bulk",
                           json={"ids": [*dets, 999999], "favorite": True}).status_code == 404


def test_bulk_run_detection_again(settings):
    start = utcnow().replace(microsecond=0) - timedelta(minutes=30)
    segs = [add_segment(settings, start=start + timedelta(minutes=10 * i), with_file=False)
            for i in range(3)]
    dets = []
    for seg_id in segs:  # far apart: three events
        add_job(seg_id, state="done")
        dets.append(add_detection(seg_id, seg_id, 500))
    events.assign_events(settings)
    with app_client(settings) as client:
        # Detections: their recordings go back for detection, the detections are replaced.
        r = client.post("/api/detections/reanalyse", json={"ids": [dets[0]]})
        assert r.json() == {"queued": 1, "skipped": 0, "detections_removed": 1}
        with session_scope() as s:
            assert s.get(Segment, segs[0]).status == SegmentStatus.NEW
        # Events: every recording of each (the first event went with its only detection).
        ev_ids = [e["id"] for e in client.get("/api/events").json()]
        r = client.post("/api/events/reanalyse", json={"ids": ev_ids})
        assert r.json() == {"queued": 2, "skipped": 0, "detections_removed": 2}
        assert client.get("/api/detections").json() == []
        assert client.post("/api/events/reanalyse", json={"ids": [999999]}).status_code == 404
