"""HTTP API: worker protocol, masks, camera management."""

from datetime import timedelta

import pytest
from foxcam.db import session_scope
from foxcam.models import (
    Detection,
    Event,
    FootageSkip,
    JobState,
    Segment,
    SegmentStatus,
    utcnow,
)

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
        seg.width, seg.height = 1280, 720
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
    assert (dets[0]["width"], dets[0]["height"]) == (1280, 720)  # the recording's size
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


def test_archive_of_recordings_deleted_meanwhile_is_dropped(settings, client, monkeypatch):
    """Deleting recordings while the archiving process builds an archive of them: the
    archive is thrown away instead of kept without recordings."""
    from foxcam import recording
    from foxcam.models import Archive
    from foxcam.pipeline import compaction

    seg = add_segment(settings)
    plan = compaction.Plan(camera_id="cam", segment_ids=[seg], files=[], durations=[2.0],
                           profile=recording.default_recording_profile(settings)[0])

    def encode_then_delete(_settings, _plan, part):
        part.write_bytes(b"x")
        assert client.post("/api/admin/reset/recordings").status_code == 200
        return "copy"

    monkeypatch.setattr(compaction.ffmpeg, "probe",
                        lambda _p: type("Info", (), {"duration_s": 2.0})())
    assert compaction.build(settings, plan, encode_then_delete) is None
    with session_scope() as s:
        assert s.query(Archive).count() == 0
    assert not list(settings.recordings_dir.rglob("*.mp4"))


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


def test_danger_zone_footage_keeps_what_was_found(settings, client):
    seg = add_segment(settings)
    det = add_detection(seg, add_job(seg, JobState.DONE), 500)
    with session_scope() as s:
        path = settings.recordings_dir / s.get(Segment, seg).path
    assert path.exists()
    assert client.post("/api/admin/reset/footage").status_code == 200
    assert not path.exists()
    with session_scope() as s:
        assert s.get(Segment, seg).deleted_at is not None
    with session_scope() as s:
        assert s.get(Detection, det) is not None


def test_event_motion_is_in_clip_time(settings):
    """Motion of the event's recordings, shifted to the clip's clock (it starts with the
    pre-roll, in the middle of the first recording)."""
    start = utcnow().replace(microsecond=0)
    first = add_segment(settings, start=start, seconds=10, with_file=False,
                        motion_boxes=[[1000, [[0.1, 0.1, 0.2, 0.2]]],
                                      [8000, [[0.5, 0.5, 0.1, 0.1]]]])
    second = add_segment(settings, start=start + timedelta(seconds=10), seconds=10,
                         with_file=False, motion_boxes=[[500, [[0.2, 0.2, 0.1, 0.1]]]])
    with session_scope() as s:
        ev = Event(camera_id="cam", label="fox", first_ts=start, last_ts=start,
                   max_confidence=0.9, clip_dirty=False, clip_path="clips/cam/ev.mp4",
                   clip_start=start + timedelta(seconds=5), clip_duration_s=10)
        ev.segments = [s.get(Segment, first), s.get(Segment, second)]
        s.add(ev)
        s.flush()
        ev_id = ev.id
    with app_client(settings) as c:
        r = c.get(f"/api/events/{ev_id}/timeline")
    assert r.status_code == 200
    # 1000 ms is before the clip; 8000 -> 3000; the second recording's 500 -> 5500.
    assert r.json()["motion"] == [[3000, [[0.5, 0.5, 0.1, 0.1]]],
                                  [5500, [[0.2, 0.2, 0.1, 0.1]]]]
    assert r.json()["gaps"] == []


def test_missing_footage_in_an_event_clip(settings):
    """The gaps between its recordings the clip kept (how much they cost is unknown), and
    the skips inside them, in clip time."""
    start = utcnow().replace(microsecond=0)
    first = add_segment(settings, start=start, seconds=8, with_file=False,
                        skips=[[3.5, 11.2]])
    # Prepared before skips were found in the file: where ingest noted them, by the clock.
    second = add_segment(settings, start=start + timedelta(seconds=15), seconds=15,
                         with_file=False)
    with session_scope() as s:
        ev = Event(camera_id="cam", label="fox", first_ts=start, last_ts=start,
                   max_confidence=0.9, clip_dirty=False, clip_path="clips/cam/ev.mp4",
                   clip_start=start + timedelta(seconds=2), clip_duration_s=26,
                   clip_gaps=[[6.0, 13.0, 7.0]])
        ev.segments = [s.get(Segment, first), s.get(Segment, second)]
        s.add(ev)
        s.add(FootageSkip(camera_id="cam", at=start + timedelta(seconds=20), missing_s=6))
        s.add(FootageSkip(camera_id="other", at=start + timedelta(seconds=21), missing_s=9))
        s.flush()
        ev_id = ev.id
    with app_client(settings) as c:
        r = c.get(f"/api/events/{ev_id}/timeline")
    assert r.json()["gaps"] == [[1.5, 1.5, 11.2], [6.0, 13.0, None], [18.0, 18.0, 6.0]]


def test_missing_footage_in_a_recording(settings):
    start = utcnow().replace(microsecond=0)
    seg = add_segment(settings, start=start, seconds=60, with_file=False)
    with session_scope() as s:
        s.add(FootageSkip(camera_id="cam", at=start + timedelta(seconds=14), missing_s=11))
        s.add(FootageSkip(camera_id="cam", at=start + timedelta(seconds=90), missing_s=5))
    with app_client(settings) as c:
        r = c.get(f"/api/segments/{seg}")
    assert r.json()["gaps"] == [[14.0, 14.0, 11.0]]
    # Found in the file when it was prepared: those win.
    with session_scope() as s:
        s.get(Segment, seg).skips = [[13.62, 11.0], [40.1, None]]
    with app_client(settings) as c:
        r = c.get(f"/api/segments/{seg}")
    assert r.json()["gaps"] == [[13.62, 13.62, 11.0], [40.1, 40.1, None]]



@needs_ffmpeg
def test_event_thumbnails_from_the_clip_itself(settings):
    """An event clip's sprite: a tile each half second, from every frame; kept when the
    clip itself leaves the disk (moved to S3 storage), gone with the event."""
    start = utcnow().replace(microsecond=0)
    make_video(settings.data_dir / "clips" / "cam" / "ev.mp4", seconds=3)
    with session_scope() as s:
        ev = Event(camera_id="cam", label="fox", first_ts=start, last_ts=start,
                   max_confidence=0.9, clip_dirty=False, clip_path="clips/cam/ev.mp4",
                   clip_start=start, clip_duration_s=3)
        s.add(ev)
        s.flush()
        ev_id = ev.id
    with app_client(settings) as c:
        r = c.get(f"/api/events/{ev_id}/thumbnails.vtt")
        assert r.status_code == 200
        cues = [line for line in r.text.splitlines() if "-->" in line]
        assert cues[:2] == ["00:00:00.000 --> 00:00:00.500", "00:00:00.500 --> 00:00:01.000"]
        assert len(cues) == 6
        image = next(line for line in r.text.splitlines() if "#xywh" in line).split("#")[0]
        assert image.startswith(f"/api/events/{ev_id}/sprite.jpg?v=")
        assert c.get(image).status_code == 200
        # The clip moved to S3 storage (local copy deleted): its previews stay.
        (settings.data_dir / "clips" / "cam" / "ev.mp4").unlink()
        assert c.get(f"/api/events/{ev_id}/thumbnails.vtt").text == r.text
        assert c.get(image).status_code == 200
    from foxcam import sprites

    sprites.remove(settings, "clip", ev_id)
    assert not sprites.files(settings, "clip", ev_id)[0].exists()


def test_archive_motion_is_in_archive_time(settings):
    """An archive's recordings' motion, each moved by where it sits in the file."""
    from foxcam.models import Archive

    start = utcnow().replace(microsecond=0)
    with session_scope() as s:
        arc = Archive(camera_id="cam", start_at=start, path="archive/cam/a.mp4",
                      duration_s=20, size_bytes=1)
        s.add(arc)
        s.flush()
        arc_id = arc.id
    for i, boxes in enumerate(([[1000, [[0.1, 0.1, 0.2, 0.2]]]],
                               [[500, [[0.2, 0.2, 0.1, 0.1]]]])):
        seg = add_segment(settings, start=start + timedelta(seconds=10 * i), seconds=10,
                          with_file=False, motion_boxes=boxes)
        with session_scope() as s:
            g = s.get(Segment, seg)
            g.archive_id, g.archive_offset_s = arc_id, 10.0 * i
    with app_client(settings) as c:
        r = c.get(f"/api/archives/{arc_id}/timeline")
    assert r.status_code == 200
    assert r.json()["motion"] == [[1000, [[0.1, 0.1, 0.2, 0.2]]],
                                  [10500, [[0.2, 0.2, 0.1, 0.1]]]]
    assert r.json()["gaps"] == []


def test_missing_footage_in_an_archive(settings):
    """Recordings with a gap between them are joined back to back: the gap is a moment
    where they meet. Skips inside a recording sit where it does in the file."""
    from foxcam.models import Archive

    start = utcnow().replace(microsecond=0)
    with session_scope() as s:
        arc = Archive(camera_id="cam", start_at=start, path="archive/cam/a.mp4",
                      duration_s=20, size_bytes=1)
        s.add(arc)
        s.flush()
        arc_id = arc.id
        s.add(FootageSkip(camera_id="cam", at=start + timedelta(seconds=22), missing_s=4))
    # 0-10 s, then (8 s gap) 18-28 s: the second sits at 10 s in the file.
    for i, at in enumerate((0, 18)):
        seg = add_segment(settings, start=start + timedelta(seconds=at), seconds=10,
                          with_file=False)
        with session_scope() as s:
            g = s.get(Segment, seg)
            g.archive_id, g.archive_offset_s = arc_id, 10.0 * i
    with app_client(settings) as c:
        r = c.get(f"/api/archives/{arc_id}/timeline")
    assert r.json()["gaps"] == [[10.0, 10.0, None], [14.0, 14.0, 4.0]]


def test_api_responses_say_which_web_build_is_served(settings, tmp_path, monkeypatch):
    """A page left open across a deploy compares this with its own build (newVersion.ts)."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<html><head><meta name="foxcam-build" content="abc123"></head></html>')
    monkeypatch.setenv("FOXCAM_WEB_DIST", str(dist))
    with app_client(settings) as c:
        assert c.get("/api/events").headers["x-foxcam-build"] == "abc123"
        assert c.get("/api/nothing-here").headers["x-foxcam-build"] == "abc123"
        assert "x-foxcam-build" not in c.get("/").headers  # the page itself carries it


def test_no_build_header_without_a_build(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("FOXCAM_WEB_DIST", str(tmp_path / "missing"))
    with app_client(settings) as c:
        assert "x-foxcam-build" not in c.get("/api/events").headers


@needs_ffmpeg
def test_skips_are_found_where_ingest_left_a_pause(settings, tmp_path):
    """Ingest leaves half a second in the timestamps where the camera skipped ahead and
    notes how far; preparing the recording finds the place, with the amount."""
    from foxcam import footage

    parts = [tmp_path / f"{i}.mp4" for i in range(3)]
    for part in parts:
        make_video(part, 2)
    joined = tmp_path / "joined.mp4"
    lst = tmp_path / "list.txt"
    # 2 s, a 0.5 s pause, 2 s, a 0.5 s pause, 2 s.
    lst.write_text("".join(f"file '{p}'\nduration 2.5\n" for p in parts[:2])
                   + f"file '{parts[2]}'\n")
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(joined)], check=True)
    start = utcnow().replace(microsecond=0)
    with session_scope() as s:
        s.add(FootageSkip(camera_id="cam", at=start + timedelta(seconds=3), missing_s=11.2))
    skips = footage.find_skips(joined, "cam", start, 7.0)
    assert [round(at, 1) for at, _ in skips] == [2.5, 5.0]
    assert [missing for _, missing in skips] == [11.2, None]
