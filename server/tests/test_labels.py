"""Label suggestions: worker vocabularies stored on lease, and GET /api/labels."""

from __future__ import annotations

from media_streamer.db import session_scope
from media_streamer.models import JobState, Worker
from media_streamer.pipeline import queue

from .conftest import add_detection, add_job, add_segment, app_client


def _vocabulary(worker_id: str):
    with session_scope() as s:
        w = s.get(Worker, worker_id)
        return w.vocabulary, w.vocabulary_at


def test_lease_stores_vocabulary(settings):
    queue.lease(settings, "species", {"detector": "speciesnet:4.0.3a"}, ["speciesnet"],
                vocabulary=["red fox", "animal", " canine family ", "red fox", ""])
    vocab, at = _vocabulary("species")
    assert vocab == ["animal", "canine family", "red fox"] and at is not None

    # Leases without a vocabulary (most of them) keep the stored one.
    queue.lease(settings, "species", {}, ["speciesnet"])
    assert _vocabulary("species")[0] == ["animal", "canine family", "red fox"]


def test_older_workers_labels_count_as_vocabulary(settings):
    queue.lease(settings, "coco", {}, ["ultralytics"], labels=["person", "car", "dog"])
    assert _vocabulary("coco")[0] == ["car", "dog", "person"]


def test_open_vocabulary_worker_reports_none(settings):
    queue.lease(settings, "world", {}, ["ultralytics", "open-vocabulary"])
    assert _vocabulary("world")[0] is None


def test_lease_endpoint_accepts_a_large_vocabulary(settings):
    big = [f"species {i}" for i in range(3000)]
    with app_client(settings) as c:
        r = c.post("/api/worker/lease", json={"worker_id": "sn", "tags": ["speciesnet"],
                                              "vocabulary": big})
        assert r.status_code == 204
    assert len(_vocabulary("sn")[0]) == 3000


def test_labels_endpoint(settings):
    queue.lease(settings, "coco", {"detector": "ultralytics:yolov8n.pt"}, ["ultralytics", "gpu"],
                labels=["person", "car"])
    queue.lease(settings, "world", {"detector": "ultralytics:yolov8s-worldv2.pt"},
                ["ultralytics", "open-vocabulary"])
    seg = add_segment(settings, with_file=False)
    job = add_job(seg, state=JobState.DONE)
    add_detection(seg, job, 100, "fox", 0.91)
    add_detection(seg, job, 200, "fox", 0.62)
    add_detection(seg, job, 300, "cat", 0.3, target=False)

    with app_client(settings) as c:
        data = c.get("/api/labels").json()

    by_id = {w["id"]: w for w in data["workers"]}
    assert by_id["coco"]["vocabulary"] == ["car", "person"] and by_id["coco"]["online"]
    assert not by_id["coco"]["open_vocabulary"] and "gpu" in by_id["coco"]["tags"]
    assert by_id["world"]["open_vocabulary"] and by_id["world"]["vocabulary"] is None
    assert by_id["world"]["detector"] == "ultralytics:yolov8s-worldv2.pt"
    ov = data["open_vocabulary"]
    assert len(ov["lvis"]) > 1000 and len(ov["objects365"]) > 300
    assert "fox" in ov["wildlife"] and "person" in ov["objects365"] and "dog" in ov["lvis"]
    assert all("/" not in x for x in ov["lvis"])  # first name of each class only
    seen = {r["label"]: r for r in data["seen"]}
    assert seen["fox"]["count"] == 2 and seen["fox"]["max_confidence"] == 0.91
    assert seen["fox"]["targets"] == 2 and seen["cat"]["targets"] == 0
    assert ["person", "people", "human"] == data["synonym_groups"][0][:3]


def test_labels_endpoint_is_for_admins(settings):
    with app_client(settings, email="viewer@example.org", admin=False) as c:
        assert c.get("/api/labels").status_code == 403
    with app_client(settings, email=None) as c:
        assert c.get("/api/labels").status_code == 401
