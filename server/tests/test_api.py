"""HTTP API: worker protocol, masks, camera management."""

import pytest
from foxcam.db import session_scope
from foxcam.models import JobState, Segment, SegmentStatus

from .conftest import add_detection, add_job, add_segment, app_client, make_video, needs_ffmpeg


@pytest.fixture
def client(settings):
    with app_client(settings) as c:
        yield c


@needs_ffmpeg
def test_worker_protocol_end_to_end(settings, client):
    seg_id = add_segment(settings, status=SegmentStatus.QUEUED)
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        derived = settings.derived_dir / "x.mp4"
        make_video(derived)
        seg.derived_path = str(derived.relative_to(settings.data_dir))
    add_job(seg_id)

    job = client.post("/api/worker/lease", json={"worker_id": "gpu", "info": {"m": 1}}).json()
    assert job["segment"]["camera_id"] == "cam" and job["input_fps"] > 0
    assert client.post("/api/worker/lease", json={"worker_id": "gpu"}).status_code == 204

    assert client.get(job["input_url"]).status_code == 409  # token required
    video = client.get(job["input_url"], headers={"X-Lease-Token": job["lease_token"]})
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"

    hb = client.post(f"/api/worker/jobs/{job['job_id']}/heartbeat",
                     json={"lease_token": job["lease_token"]})
    assert hb.status_code == 200
    r = client.post(f"/api/worker/jobs/{job['job_id']}/result", json={
        "lease_token": job["lease_token"], "model": "test", "processing_ms": 5,
        "detections": [{"offset_ms": 100, "label": "fox", "confidence": 0.8,
                        "bbox": [0.1, 0.2, 0.3, 0.4]}]})
    assert r.status_code == 200
    dets = client.get("/api/detections", params={"segment": seg_id}).json()
    assert [(d["label"], d["is_target"], d["bbox"]) for d in dets] == [
        ("fox", True, [0.1, 0.2, 0.3, 0.4])]
    assert client.get("/api/queue").json()["workers"][0]["id"] == "gpu"


def test_invalid_detection_payload_rejected(settings, client):
    r = client.post("/api/worker/jobs/1/result", json={
        "lease_token": "x", "detections": [{"offset_ms": 1, "label": "fox", "confidence": 2}]})
    assert r.status_code == 422


def test_worker_token_required_when_configured(settings, client):
    settings.worker_token = "secret"
    settings.open_worker_access = False
    assert client.post("/api/worker/lease", json={"worker_id": "w"}).status_code == 401
    ok = client.post("/api/worker/lease", json={"worker_id": "w"},
                     headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 204
    # Public API is not affected by the worker token.
    assert client.get("/api/settings").json()["worker_token"] == "***"


def test_mask_rectangles_are_clamped_and_versioned(client):
    r = client.put("/api/cameras/cam/mask", json={
        "rects": [[-0.1, 0.5, 0.5, 0.9], [0.2, 0.2, 0.0001, 0.3]], "private": False})
    body = r.json()
    assert body["rects"] == [[0.0, 0.5, 0.5, 0.5]]  # clamped; tiny rect dropped
    first_version = body["mask_version_id"]
    again = client.put("/api/cameras/cam/mask", json={"rects": body["rects"], "private": True})
    assert again.json()["mask_version_id"] == first_version  # unchanged rects, same version
    assert again.json()["private"] is True
    assert client.put("/api/cameras/nope/mask", json={"rects": []}).status_code == 404


@needs_ffmpeg
def test_camera_management(settings, client):
    make_video(settings.samples_dir / "fox.mp4")
    assert client.get("/api/cameras-sample-files").json() == ["fox.mp4"]

    r = client.post("/api/cameras", json={"name": "Trädgården", "source": {
        "type": "file", "path": "fox.mp4"}, "segment_seconds": 30})
    assert r.status_code == 201 and r.json()["id"] == "tradgarden"
    rtsp = client.post("/api/cameras", json={"name": "Porch", "source": {
        "type": "rtsp", "url": "rtsp://admin:hunter2@192.168.1.5/stream1"}})
    assert rtsp.status_code == 201

    cams = {c["id"]: c for c in client.get("/api/cameras").json()}
    assert cams["cam"]["managed"] == "config"
    assert cams["tradgarden"]["segment_seconds"] == 30
    assert cams["porch"]["source_config"]["url"] == "rtsp://***@192.168.1.5/stream1"

    assert client.patch("/api/cameras/porch", json={"enabled": False}).status_code == 200
    assert {c["id"]: c["enabled"] for c in client.get("/api/cameras").json()}["porch"] is False
    assert client.patch("/api/cameras/cam", json={"enabled": False}).status_code == 400
    assert client.delete("/api/cameras/porch").status_code == 200
    assert "porch" not in {c["id"] for c in client.get("/api/cameras").json()}


def test_camera_validation(client):
    def add(name, source, **kw):
        return client.post("/api/cameras", json={"name": name, "source": source, **kw})

    assert add("x", {"type": "file", "path": "../../etc/hosts"}).status_code == 400
    assert add("x", {"type": "rtsp", "url": "http://not-rtsp"}).status_code == 400
    assert add("x", {"type": "test"}, id="cam").status_code == 400  # id taken by config
    assert add("", {"type": "test"}).status_code == 422


def test_probe_never_echoes_rtsp_password(client):
    r = client.post("/api/cameras/probe", json={"source": {
        "type": "rtsp", "url": "rtsp://admin:hunter2@127.0.0.1:1/stream1"}}).json()
    assert r["ok"] is False and "hunter2" not in r["detail"]


def test_tapo_status_without_session(client):
    status = client.get("/api/integrations/tapo").json()
    assert status["connected"] is False
    assert client.get("/api/integrations/tapo/devices").status_code in (401, 501)


def test_worker_api_is_locked_by_default(tmp_path):
    from foxcam.config import Settings

    s = Settings.model_validate({"data_dir": str(tmp_path),
                                 "mediamtx": {"api_url": "http://127.0.0.1:1"}})
    assert s.open_worker_access is False
    with app_client(s) as c:
        assert c.get("/api/workers/auth").json()["mode"] == "locked"
        r = c.post("/api/worker/lease", json={"worker_id": "stranger"})
        assert r.status_code == 401 and "token" in r.json()["detail"]
        assert c.get("/api/queue").json()["workers"] == []  # never registered


def test_worker_token_cli_reuses_saved_token(settings, tmp_path):
    from foxcam import worker_tokens

    path = tmp_path / "dev.token"
    first = worker_tokens.ensure("dev", path)
    assert worker_tokens.ensure("dev", path) == first  # reused while valid
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    worker_tokens.revoke(worker_tokens.list_tokens()[0]["id"])
    second = worker_tokens.ensure("dev", path)  # e.g. after reset:workers
    assert second != first and worker_tokens.verify(second).name == "dev"


def test_worker_tokens_lock_the_api_and_fix_identity(settings, client):
    settings.open_worker_access = False
    lease = {"worker_id": "spoofed"}
    assert client.get("/api/workers/auth").json()["mode"] == "locked"
    settings.open_worker_access = True
    assert client.post("/api/worker/lease", json=lease).status_code == 204  # opened explicitly
    settings.open_worker_access = False

    created = client.post("/api/workers/tokens", json={"name": "gpu-box", "detector": "speciesnet"})
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("msw_")
    assert "token" not in client.get("/api/workers/tokens").json()[0]  # shown once only
    assert client.get("/api/workers/auth").json() == {"mode": "locked", "shared_token": False,
                                                      "tokens": 1}
    assert client.post("/api/workers/tokens", json={"name": "gpu-box"}).status_code == 400

    assert client.post("/api/worker/lease", json=lease).status_code == 401
    bad = {"Authorization": "Bearer msw_wrong"}
    assert client.post("/api/worker/lease", json=lease, headers=bad).status_code == 401
    ok = client.post("/api/worker/lease", json=lease, headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 204
    workers = [w["id"] for w in client.get("/api/queue").json()["workers"]]
    assert "gpu-box" in workers and workers.count("spoofed") == 1  # only from open access

    token_id = created.json()["id"]
    assert client.delete(f"/api/workers/tokens/{token_id}").status_code == 200
    revoked = client.post("/api/worker/lease", json=lease,
                          headers={"Authorization": f"Bearer {token}"})
    assert revoked.status_code == 401
    assert client.get("/api/workers/auth").json()["mode"] == "locked"  # stays locked


def test_offline_workers_can_be_removed(client):
    from datetime import timedelta

    from foxcam.models import Worker, utcnow

    assert client.post("/api/worker/lease", json={"worker_id": "dummy"}).status_code == 204
    assert client.delete("/api/workers/dummy").status_code == 409  # still online
    with session_scope() as s:
        s.get(Worker, "dummy").last_seen = utcnow() - timedelta(hours=1)
    assert client.delete("/api/workers/dummy").status_code == 200
    assert client.get("/api/queue").json()["workers"] == []
    assert client.delete("/api/workers/dummy").status_code == 404


def test_whoami_checks_credentials_without_side_effects(settings, client):
    assert client.get("/api/worker/whoami").json()["token_name"] is None  # open access
    settings.open_worker_access = False
    token = client.post("/api/workers/tokens", json={"name": "wsl-pc"}).json()["token"]
    assert client.get("/api/worker/whoami").status_code == 401
    assert client.get("/api/worker/whoami",
                      headers={"Authorization": "Bearer msw_wrong"}).status_code == 401
    ok = client.get("/api/worker/whoami", headers={"Authorization": f"Bearer {token}"})
    assert ok.json()["token_name"] == "wsl-pc"
    assert client.get("/api/queue").json()["stats"]["counts"].get("leased", 0) == 0


def test_segment_jobs_tell_empty_runs_from_missing_ones(settings, client):
    seg_id = add_segment(settings, with_file=False)
    found = add_job(seg_id, state=JobState.DONE, model="speciesnet:4.0.1", processing_ms=14_000,
                    leased_by="gpu")
    add_detection(seg_id, found, 1000, "fox", 0.9, target=True)
    add_detection(seg_id, found, 2000, "dog", 0.3, target=False)
    empty = add_job(seg_id, state=JobState.DONE, profile_id="people", model="yolo",
                    processing_ms=900)
    add_job(seg_id, state=JobState.DEAD, profile_id="cars", error="CUDA out of memory")

    jobs = client.get(f"/api/segments/{seg_id}/jobs").json()

    assert [j["id"] for j in jobs][:2] == [found, empty]
    assert jobs[0].items() >= {"model": "speciesnet:4.0.1", "state": "done", "detections": 2,
                               "targets": 1, "processing_ms": 14_000,
                               "worker_id": "gpu"}.items()
    assert (jobs[1]["detections"], jobs[1]["targets"]) == (0, 0)  # ran, found nothing
    assert jobs[2]["state"] == "dead" and jobs[2]["error"] == "CUDA out of memory"
    assert client.get("/api/segments/999999/jobs").status_code == 404


@needs_ffmpeg
def test_segment_download_name_has_segment_id(settings, client):
    seg_id = add_segment(settings)
    r = client.get(f"/api/segments/{seg_id}/download")
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert f'_seg{seg_id}.mp4"' in disposition and disposition.startswith("attachment")
    assert "filename=\"cam_" in disposition


def test_recording_profiles_with_several_cameras(settings, client):
    from foxcam.config import CameraConfig

    settings.cameras.append(CameraConfig.model_validate(
        {"id": "door", "recording_profile": "720p", "source": {"type": "test"}}))
    r = client.get("/api/recording-profiles")
    assert r.status_code == 200, r.text
    by_id = {p["id"]: p for p in r.json()}
    assert by_id["original"]["cameras"] == ["cam"] and by_id["original"]["default"]
    assert by_id["720p"]["cameras"] == ["door"]


def test_default_recording_profile_from_the_ui(settings, client):
    from foxcam import recording
    from foxcam.config import CameraConfig

    settings.cameras.append(CameraConfig.model_validate(
        {"id": "door", "recording_profile": "720p", "source": {"type": "test"}}))
    r = client.put("/api/recording-profiles/default", json={"profile": "h265"})
    assert r.json() == {"default": "h265", "default_source": "ui"}
    profiles = {p["id"]: p for p in client.get("/api/recording-profiles").json()}
    assert profiles["h265"]["default"] and profiles["h265"]["default_source"] == "ui"
    assert profiles["h265"]["cameras"] == ["cam"]  # no own choice: follows the default
    assert profiles["720p"]["cameras"] == ["door"]  # its own (config) choice stays
    assert not profiles["original"]["default"]
    cam = settings.camera("cam")
    assert recording.camera_recording_profile(settings, cam)[0].id == "h265"

    assert client.put("/api/recording-profiles/default",
                      json={"profile": "nope"}).status_code == 400
    r = client.put("/api/recording-profiles/default", json={"profile": None})
    assert r.json() == {"default": "original", "default_source": "config"}


def test_default_recording_profile_is_for_admins(settings):
    from .conftest import app_client

    with app_client(settings, email="viewer@example.org", admin=False) as c:
        assert c.put("/api/recording-profiles/default",
                     json={"profile": "h265"}).status_code == 403


@needs_ffmpeg
def test_detection_frame_has_a_small_preview(settings, client):
    import subprocess

    seg = add_segment(settings, size="640x360")
    det = add_detection(seg, add_job(seg), 500)
    for size, width in (("", 960), ("?size=small", 240)):
        r = client.get(f"/api/detections/{det}/frame.jpg{size}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert "max-age" in r.headers["cache-control"]
        name = f"{det}-{seg}-500-small.jpg" if size else f"{det}-{seg}-500.jpg"
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width",
                              "-of", "csv=p=0", str(settings.thumbs_dir / "detections" / name)],
                             capture_output=True, text=True, check=True).stdout
        assert int(out.strip()) == width


def test_status_has_workers_and_preparation_for_admins(settings, client):
    add_segment(settings, status=SegmentStatus.NEW)
    s = client.get("/api/status").json()
    assert s["workers"] == {"total": 0, "online": 0, "busy": 0}
    assert s["preprocessing"]["waiting"] == 1 and s["preprocessing"]["segments"] == []
    with app_client(settings, email="viewer@example.org", admin=False) as c:
        s = c.get("/api/status").json()
    assert s["workers"] is None and s["preprocessing"] is None


def test_danger_zone_reset_deletes_one_kind_of_data(settings, client):
    seg = add_segment(settings)
    add_detection(seg, add_job(seg), 500)
    r = client.post("/api/admin/reset/recordings")
    assert r.status_code == 200 and "removed" in r.json()["message"]
    with session_scope() as s:
        assert s.query(Segment).count() == 0
    assert client.post("/api/admin/reset/users").status_code == 422  # not from the web
    with app_client(settings, email="viewer@example.org", admin=False) as c:
        assert c.post("/api/admin/reset/queue").status_code == 403


def test_danger_zone_keeps_recordings_while_archiving_runs(settings, client, monkeypatch):
    from foxcam.pipeline import compaction

    monkeypatch.setattr(compaction, "status", lambda _settings: {"running": True})
    add_segment(settings)
    assert client.post("/api/admin/reset/recordings").status_code == 409
    with session_scope() as s:
        assert s.query(Segment).count() == 1


def test_gate_holds_background_work_during_a_reset():
    import threading
    import time

    from foxcam.pipeline.runner import Gate

    gate, order = Gate(), []
    with gate.exclusive():
        t = threading.Thread(target=lambda: gate.work().__enter__() or order.append("work"))
        t.start()
        time.sleep(0.1)
        order.append("reset done")
    t.join(2)
    assert order == ["reset done", "work"]
