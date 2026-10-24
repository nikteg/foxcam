"""Several detectors (models) per detection profile: config, jobs, leasing, results."""

from __future__ import annotations

from datetime import timedelta

import pytest
from media_streamer import profiles
from media_streamer.config import ProfileConfig
from media_streamer.db import session_scope
from media_streamer.models import Detection, Job, JobState, Segment, SegmentStatus
from media_streamer.pipeline import preprocess, queue
from pydantic import ValidationError

from .conftest import add_segment, app_client, make_video, needs_ffmpeg

DET = queue.DetectionIn


def two_models(**over) -> ProfileConfig:
    """Foxes, looked for by YOLO (lower threshold, 1 fps) and SpeciesNet."""
    return ProfileConfig.model_validate({
        "id": "wildlife", "targets": ["fox"], "aliases": {"red fox": "fox"},
        "min_confidence": 0.5, "requires": ["gpu"],
        "detectors": [
            {"id": "yolo", "name": "YOLO-World", "requires": ["open-vocabulary"],
             "min_confidence": 0.3, "fps": 1},
            {"id": "speciesnet", "requires": ["speciesnet"]},
        ], **over})


@pytest.fixture
def wildlife(settings):
    settings.profiles = [two_models(default=True)]
    return settings


def _queued_segment(settings, start=None) -> int:
    seg_id = add_segment(settings, status=SegmentStatus.QUEUED, start=start)
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        derived = settings.derived_dir / f"{seg_id}.mp4"
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_bytes(b"x")
        seg.derived_path = str(derived.relative_to(settings.data_dir))
        for d in ("yolo", "speciesnet"):
            s.add(Job(segment_id=seg_id, profile_id="wildlife", detector_id=d))
    return seg_id


def test_a_profile_without_detectors_has_one():
    p = ProfileConfig(id="fox", targets=["fox"], requires=["coco"])
    [d] = p.detectors
    assert d.id == "default" and p.detector_requires(d) == {"coco"}
    assert p.detector(None) is d and p.min_confidence_for("default") == 0.5


def test_detector_settings_and_validation():
    p = two_models()
    yolo = p.detectors[0]
    assert p.detector_requires(yolo) == {"gpu", "open-vocabulary"}
    assert (p.min_confidence_for("yolo"), p.min_confidence_for("speciesnet")) == (0.3, 0.5)
    assert (p.fps_for("yolo"), p.fps_for("speciesnet")) == (1, None)
    assert p.is_target("red fox", 0.35, "yolo") and not p.is_target("red fox", 0.35, "speciesnet")
    with pytest.raises(ValidationError):
        two_models(detectors=[{"id": "a"}, {"id": "a"}])
    with pytest.raises(ValidationError):
        two_models(detectors=[{"id": "Not A Slug"}])


def test_ui_profiles_with_detectors_round_trip(settings):
    profiles.save_profile(settings, {
        "id": "pets", "targets": ["cat"],
        "detectors": [{"id": "coco", "requires": ["COCO", "bad tag!"]}, {"id": "sn"}]},
        create=True)
    p = settings.profile("pets")
    assert [d.id for d in p.detectors] == ["coco", "sn"]
    assert p.detectors[0].requires == ["coco"]  # normalised, invalid dropped
    # Stored without detectors (an older UI profile): one `default` detector on load.
    profiles.save_profile(settings, {"id": "old", "targets": ["cat"], "requires": ["x"]},
                          create=True)
    assert [d.id for d in settings.profile("old").detectors] == ["default"]


@needs_ffmpeg
def test_preprocess_creates_one_job_per_profile_and_detector(wildlife):
    seg_id = add_segment(wildlife, status=SegmentStatus.NEW)
    preprocess.preprocess_segment(wildlife, seg_id)
    with session_scope() as s:
        pairs = sorted((j.profile_id, j.detector_id) for j in
                       s.query(Job).filter(Job.segment_id == seg_id))
    assert pairs == [("wildlife", "speciesnet"), ("wildlife", "yolo")]


def test_workers_lease_only_their_detectors_jobs(wildlife):
    _queued_segment(wildlife)
    assert queue.lease(wildlife, "cpu", tags=["speciesnet"]) is None  # lacks `gpu`
    sn = queue.lease(wildlife, "sn", tags=["gpu", "speciesnet"])
    assert sn.detector_id == "speciesnet"
    assert queue.lease(wildlife, "sn2", tags=["gpu", "speciesnet"]) is None
    yolo = queue.lease(wildlife, "yolo", tags=["gpu", "open-vocabulary"])
    assert yolo.detector_id == "yolo"


def test_results_use_the_detectors_threshold(wildlife):
    seg_id = _queued_segment(wildlife)
    for worker, tags in (("y", ["gpu", "open-vocabulary"]), ("s", ["gpu", "speciesnet"])):
        job = queue.lease(wildlife, worker, tags=tags)
        queue.complete(wildlife, job.id, job.lease_token,
                       [DET(100, "red fox", 0.35)], f"model-{worker}", 10)
    with session_scope() as s:
        dets = {d.detector_id: d for d in s.query(Detection)}
        seg = s.get(Segment, seg_id)
        assert dets["yolo"].is_target and not dets["speciesnet"].is_target
        assert dets["yolo"].model == "model-y" and dets["yolo"].label == "fox"
        # Done once both detectors' jobs are.
        assert seg.status == SegmentStatus.DONE and seg.has_target


def test_lease_response_names_the_detector(wildlife):
    seg_id = _queued_segment(wildlife)
    with session_scope() as s:
        make_video(wildlife.data_dir / s.get(Segment, seg_id).derived_path)
    with app_client(wildlife) as c:
        r = c.post("/api/worker/lease", json={"worker_id": "y",
                                              "tags": ["gpu", "open-vocabulary"]})
        job = r.json()
    assert job["detector"] == {"id": "yolo", "name": "YOLO-World"}
    assert job["profile"]["min_confidence"] == 0.3 and job["hints"]["detector"] == "yolo"
    assert job["sample_fps"] == 1


def test_removing_a_detector_gives_up_its_waiting_jobs(settings):
    profiles.save_profile(settings, two_models().model_dump(exclude={"managed"}), create=True)
    seg_id = _queued_segment(settings)
    data = two_models().model_dump(exclude={"managed"})
    data["detectors"] = data["detectors"][:1]  # drop speciesnet
    profiles.save_profile(settings, data, create=False)
    with session_scope() as s:
        states = {j.detector_id: (j.state, j.error) for j in
                  s.query(Job).filter(Job.segment_id == seg_id)}
    assert states["yolo"][0] == JobState.PENDING
    assert states["speciesnet"][0] == JobState.DEAD
    assert "speciesnet" in states["speciesnet"][1]


def test_profiles_api_lists_detectors_with_their_workers(wildlife):
    queue.touch_worker("y", tags=["gpu", "open-vocabulary"])
    with app_client(wildlife) as c:
        [p] = c.get("/api/profiles").json()
    assert [(d["id"], d["workers_online"]) for d in p["detectors"]] == \
        [("yolo", ["y"]), ("speciesnet", [])]
    assert p["workers_online"] == ["y"]


# --- part 2: one event across models ----------------------------------------------------

def _two_model_results(settings, yolo=(("red fox", 0.6),), sn=(("fox", 0.7),)) -> int:
    """Both detectors report on one segment; returns the segment id."""
    seg_id = _queued_segment(settings)
    for worker, tags, dets in (("y", ["gpu", "open-vocabulary"], yolo),
                               ("s", ["gpu", "speciesnet"], sn)):
        job = queue.lease(settings, worker, tags=tags)
        queue.complete(settings, job.id, job.lease_token,
                       [DET(100 + i * 500, label, conf) for i, (label, conf) in enumerate(dets)],
                       worker, 10)
    return seg_id


def test_detections_of_both_models_make_one_event(wildlife):
    from media_streamer.models import Event
    from media_streamer.pipeline import events

    _two_model_results(wildlife)
    events.assign_events(wildlife)
    with session_scope() as s:
        [ev] = s.query(Event).all()
        assert (ev.detectors, ev.agreement, ev.detection_count) == \
            (["speciesnet", "yolo"], 2, 2)
    with app_client(wildlife) as c:
        assert [e["agreement"] for e in c.get("/api/events").json()] == [2]
        assert c.get("/api/events?min_agreement=3").json() == []
        dets = c.get("/api/detections?detector=yolo").json()
        assert [d["detector_id"] for d in dets] == ["yolo"]


def test_channels_can_wait_for_models_to_agree(wildlife):
    from media_streamer.config import ChannelConfig
    from media_streamer.models import Notification
    from media_streamer.pipeline import events

    wildlife.notifications.channels = [
        ChannelConfig(id="any", type="webhook", url="http://x"),
        ChannelConfig(id="both", type="webhook", url="http://x", min_agreement=2)]
    seg_id = _queued_segment(wildlife)

    def result(worker, tags, offset):
        job = queue.lease(wildlife, worker, tags=tags)
        queue.complete(wildlife, job.id, job.lease_token, [DET(offset, "fox", 0.8)], worker, 1)
        events.assign_events(wildlife)
        with session_scope() as s:
            return sorted(n.channel_id for n in s.query(Notification))

    assert result("y", ["gpu", "open-vocabulary"], 100) == ["any"]
    # The second model agrees: `both` now, `any` not again.
    assert result("s", ["gpu", "speciesnet"], 200) == ["any", "both"]
    assert seg_id


def test_reanalysis_recounts_the_models(wildlife):
    from media_streamer.models import Event
    from media_streamer.pipeline import events
    from media_streamer.pipeline.reanalyse import reanalyse_segment

    first = _two_model_results(wildlife, sn=())  # only YOLO on the first segment
    with session_scope() as s:
        start = s.get(Segment, first).start_at
    second = _queued_segment(wildlife, start + timedelta(seconds=3))
    job = queue.lease(wildlife, "s", tags=["gpu", "speciesnet"])
    assert job.segment_id == second
    queue.complete(wildlife, job.id, job.lease_token, [DET(300, "fox", 0.9)], "s", 1)
    job = queue.lease(wildlife, "y", tags=["gpu", "open-vocabulary"])
    queue.complete(wildlife, job.id, job.lease_token, [], "y", 1)
    events.assign_events(wildlife)
    with session_scope() as s:
        [ev] = s.query(Event).all()
        assert ev.agreement == 2
    reanalyse_segment(wildlife, second)  # SpeciesNet's detection goes
    with session_scope() as s:
        [ev] = s.query(Event).all()
        assert (ev.detectors, ev.agreement) == (["yolo"], 1)
    assert first
