"""Detection profiles: per-camera selection, tag-based leasing, per-profile events."""

from datetime import timedelta

import pytest
from media_streamer import db
from media_streamer.config import ProfileConfig, Settings
from media_streamer.db import session_scope
from media_streamer.models import Detection, Event, Job, JobState, Segment, SegmentStatus, utcnow
from media_streamer.pipeline import events, preprocess, queue, retention
from media_streamer.profiles import camera_profiles, set_camera_profiles
from sqlalchemy import text

from .conftest import add_job, add_segment, app_client, needs_ffmpeg

DET = queue.DetectionIn


@pytest.fixture
def two_profiles(settings):
    """`default` (fox, any worker) plus `vehicles` (cars, needs a `coco` worker, 1 fps)."""
    settings.profiles.append(ProfileConfig(
        id="vehicles", name="Vehicles", targets=["car", "person"], requires=["coco"], fps=1,
        aliases={"automobile": "car"}, min_confidence=0.4))
    return settings


def _lease(settings, worker="w", tags=None):
    job = queue.lease(settings, worker, tags=tags)
    return (job.id, job.lease_token, job.profile_id) if job else None


def test_default_profile_comes_from_detection_settings(tmp_path):
    s = Settings.model_validate({"data_dir": str(tmp_path),
                                 "detection": {"target_labels": ["Fox"], "min_confidence": 0.7}})
    [p] = s.profiles
    assert (p.id, p.default, p.requires) == ("default", True, [])
    assert p.is_target("fox", 0.7) and not p.is_target("fox", 0.69)
    vehicles = ProfileConfig(id="v", targets=["car"], aliases={"Automobile": "car"})
    assert vehicles.canonical("automobile") == "car" and vehicles.is_target("AUTOMOBILE", 0.9)


def test_camera_profile_selection(two_profiles):
    s = two_profiles
    assert [p.id for p in camera_profiles(s, "cam")] == ["default"]  # only default profiles
    s.cameras[0].profiles = ["vehicles"]
    assert [p.id for p in camera_profiles(s, "cam")] == ["vehicles"]  # config.yaml selection
    set_camera_profiles(s, "cam", ["default", "vehicles"])
    assert [p.id for p in camera_profiles(s, "cam")] == ["default", "vehicles"]  # UI wins
    set_camera_profiles(s, "cam", [])
    assert camera_profiles(s, "cam") == []  # record only
    set_camera_profiles(s, "cam", None)
    assert [p.id for p in camera_profiles(s, "cam")] == ["vehicles"]


@needs_ffmpeg
def test_preprocess_creates_one_job_per_profile(two_profiles):
    set_camera_profiles(two_profiles, "cam", ["default", "vehicles"])
    seg_id = add_segment(two_profiles, status=SegmentStatus.NEW)
    preprocess.preprocess_segment(two_profiles, seg_id)
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        assert seg.status == SegmentStatus.QUEUED
        # The shared derivative uses the highest rate any profile needs (default: 2 fps).
        assert seg.derived_fps == two_profiles.processing.sample_fps
        assert sorted(j.profile_id for j in s.query(Job).filter_by(segment_id=seg_id)) == [
            "default", "vehicles"]


@needs_ffmpeg
def test_camera_without_profiles_is_only_recorded(settings):
    set_camera_profiles(settings, "cam", [])
    seg_id = add_segment(settings, status=SegmentStatus.NEW)
    preprocess.preprocess_segment(settings, seg_id)
    with session_scope() as s:
        assert s.get(Segment, seg_id).status == SegmentStatus.DONE
        assert s.get(Segment, seg_id).derived_path is None
        assert s.query(Job).count() == 0


def test_leasing_respects_required_tags(two_profiles):
    seg = add_segment(two_profiles, status=SegmentStatus.QUEUED, with_file=False)
    add_job(seg, profile_id="vehicles")
    assert _lease(two_profiles, tags=[]) is None  # untagged worker cannot run "vehicles"
    assert _lease(two_profiles, tags=["speciesnet"]) is None
    assert _lease(two_profiles, tags=["coco", "gpu"])[2] == "vehicles"


def test_segment_finishes_when_all_profiles_are_done(two_profiles):
    seg = add_segment(two_profiles, status=SegmentStatus.QUEUED, with_file=False)
    derived = two_profiles.derived_dir / "x.mp4"
    derived.write_bytes(b"x")
    with session_scope() as s:
        s.get(Segment, seg).derived_path = "derived/x.mp4"
    add_job(seg, profile_id="default")
    add_job(seg, profile_id="vehicles")

    job_id, token, _ = _lease(two_profiles, tags=[])
    queue.complete(two_profiles, job_id, token, [DET(100, "red fox", 0.3)], "m", 10)
    with session_scope() as s:
        assert s.get(Segment, seg).status == SegmentStatus.QUEUED  # vehicles still waiting
    assert derived.exists()

    job_id, token, _ = _lease(two_profiles, tags=["coco"])
    queue.complete(two_profiles, job_id, token,
                   [DET(200, "automobile", 0.8), DET(300, "dog", 0.9)], "yolo", 10)
    with session_scope() as s:
        g = s.get(Segment, seg)
        assert g.status == SegmentStatus.DONE and g.has_target and g.max_confidence == 0.8
        dets = {(d.profile_id, d.label): d for d in s.query(Detection)}
        car = dets[("vehicles", "car")]
        assert car.is_target and car.extra == {"raw_label": "automobile"}  # alias applied
        assert not dets[("vehicles", "dog")].is_target
        fox = dets[("default", "fox")]  # default profile aliases "red fox" to "fox"
        assert not fox.is_target and fox.extra == {"raw_label": "red fox"}  # below threshold
    assert not derived.exists()  # removed once every profile is done


def test_dead_job_of_one_profile_does_not_fail_the_segment(two_profiles):
    seg = add_segment(two_profiles, status=SegmentStatus.QUEUED, with_file=False)
    add_job(seg, profile_id="default", state=JobState.DONE)
    add_job(seg, profile_id="vehicles", attempts=two_profiles.queue.max_attempts)
    job_id, token, _ = _lease(two_profiles, tags=["coco"])
    queue.fail(two_profiles, job_id, token, "boom")
    with session_scope() as s:
        assert s.get(Segment, seg).status == SegmentStatus.DONE


def test_events_are_per_profile_and_label(two_profiles):
    two_profiles.events.merge_gap_seconds = 5
    seg = add_segment(two_profiles, status=SegmentStatus.QUEUED, with_file=False)
    add_job(seg, profile_id="default")
    add_job(seg, profile_id="vehicles")
    job_id, token, _ = _lease(two_profiles)
    queue.complete(two_profiles, job_id, token, [DET(100, "fox", 0.9)], "m", 1)
    job_id, token, _ = _lease(two_profiles, tags=["coco"])
    queue.complete(two_profiles, job_id, token,
                   [DET(100, "car", 0.9), DET(500, "person", 0.8), DET(900, "car", 0.7)], "m", 1)
    assert events.assign_events(two_profiles) == 3
    assert events.assign_events(two_profiles) == 0  # each detection is assigned once
    with session_scope() as s:
        evs = {(e.profile_id, e.label): e for e in s.query(Event)}
        assert set(evs) == {("default", "fox"), ("vehicles", "car"), ("vehicles", "person")}
        assert evs[("vehicles", "car")].detection_count == 2


def test_events_days_per_profile(two_profiles):
    two_profiles.retention.events_days = 0  # keep forever by default...
    two_profiles.profiles[1].events_days = 7  # ...but vehicles only a week
    old = utcnow() - timedelta(days=10)
    with session_scope() as s:
        for pid in ("default", "vehicles"):
            s.add(Event(camera_id="cam", profile_id=pid, label="x", first_ts=old, last_ts=old,
                        max_confidence=0.9, clip_dirty=False))
    assert retention.run_retention(two_profiles)["events"] == 1
    with session_scope() as s:
        assert [e.profile_id for e in s.query(Event)] == ["default"]


def test_profiles_api(settings):
    with app_client(settings) as c:
        body = {"id": "wildlife", "name": "Wildlife", "targets": ["fox", "badger"],
                "requires": ["SpeciesNet", "bad tag!"], "aliases": {"red fox": "fox"}}
        assert c.post("/api/profiles", json=body).status_code == 201
        assert c.post("/api/profiles", json=body).status_code == 400  # duplicate id
        # config.yaml profiles are read-only
        assert c.put("/api/profiles/default", json={**body, "id": "default"}).status_code == 400

        r = c.put("/api/cameras/cam/profiles", json={"profiles": ["default", "wildlife"]})
        assert r.json()["profiles"] == ["default", "wildlife"]
        assert c.put("/api/cameras/cam/profiles", json={"profiles": ["nope"]}).status_code == 400
        [cam] = c.get("/api/cameras").json()
        assert cam["profiles"] == ["default", "wildlife"] and cam["profiles_source"] == "ui"

        # A worker token with the tag makes the profile runnable.
        tok = c.post("/api/workers/tokens", json={"name": "gpu", "tags": ["speciesnet"]}).json()
        assert tok["tags"] == ["speciesnet"]
        auth = {"Authorization": f"Bearer {tok['token']}"}
        assert c.post("/api/worker/lease", json={"worker_id": "x"}, headers=auth).status_code == 204
        wildlife = next(p for p in c.get("/api/profiles").json() if p["id"] == "wildlife")
        assert wildlife["requires"] == ["speciesnet"]  # normalized, invalid tag dropped
        assert wildlife["cameras"] == ["cam"] and wildlife["workers_online"] == ["gpu"]
        assert "speciesnet" in c.get("/api/worker-tags").json()

        seg = add_segment(settings, status=SegmentStatus.QUEUED, with_file=False)
        add_job(seg, profile_id="wildlife")
        r = c.delete("/api/profiles/wildlife")
        assert r.json()["cancelled_jobs"] == 1
        with session_scope() as s:
            assert s.query(Job).one().state == JobState.DEAD
            assert s.get(Segment, seg).status == SegmentStatus.FAILED
        assert c.delete("/api/profiles/default").status_code == 400


def test_lease_returns_profile_targets_and_frame_rate(two_profiles):
    seg = add_segment(two_profiles, status=SegmentStatus.QUEUED, with_file=False)
    add_job(seg, profile_id="vehicles")
    with app_client(two_profiles) as c:
        job = c.post("/api/worker/lease", json={"worker_id": "w", "tags": ["coco"]}).json()
    assert job["profile"]["id"] == "vehicles" and job["sample_fps"] == 1
    assert job["hints"]["target_labels"] == ["car", "person"]
    assert job["hints"]["min_confidence"] == 0.4


def test_legacy_rows_join_the_default_profile(settings):
    seg = add_segment(settings, status=SegmentStatus.DONE, with_file=False)
    job = add_job(seg, state=JobState.DONE, profile_id=None)
    now = utcnow()
    with session_scope() as s:
        ev = Event(camera_id="cam", label="fox", first_ts=now, last_ts=now, max_confidence=0.9)
        ev.segments.append(s.get(Segment, seg))
        s.add(ev)
        s.flush()
        s.add(Detection(segment_id=seg, job_id=job, camera_id="cam", ts=now, offset_ms=0,
                        label="fox", confidence=0.9, is_target=True))
    engine = db._engine
    with engine.begin() as conn:
        conn.execute(text("UPDATE event SET profile_id = NULL"))
    db._backfill(engine, {"detection.event_id"})
    with session_scope() as s:
        assert s.get(Job, job).profile_id == "default"
        d = s.query(Detection).one()
        assert d.profile_id == "default" and d.event_id == s.query(Event).one().id
        assert s.query(Event).one().profile_id == "default"
    assert events.assign_events(settings) == 0  # already part of the old event


def test_fixed_class_workers_only_get_matching_profiles(two_profiles):
    from media_streamer.profiles import eligible_profiles

    two_profiles.profiles[1].requires = []  # vehicles: any worker
    both = [("default", "default"), ("vehicles", "default")]
    assert eligible_profiles(two_profiles, [], None) == both
    assert eligible_profiles(two_profiles, [], ["fox"]) == [("default", "default")]
    assert eligible_profiles(two_profiles, [], ["automobile"]) == [("vehicles", "default")]
    assert eligible_profiles(two_profiles, [], ["cat"]) == []
