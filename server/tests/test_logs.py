import logging

from foxcam import logs

from .conftest import app_client


def test_redact_hides_logins_and_secret_values():
    assert logs.redact("open rtsp://admin:hun@ter2@192.0.2.5:554/stream1 failed") == \
        "open rtsp://***@192.0.2.5:554/stream1 failed"
    assert logs.redact("GET /api/x?token=abc123&day=1") == "GET /api/x?token=***&day=1"
    assert logs.redact('{"password": "s3cret", "user": "a"}') == \
        '{"password": "***", "user": "a"}'
    assert logs.redact("Authorization: Bearer xyz.abc") == "Authorization: ***"
    # Ordinary words stay.
    assert logs.redact("relay session ended for grusgang") == \
        "relay session ended for grusgang"
    assert logs.redact("see https://example.org/docs") == "see https://example.org/docs"


def _write(settings, process="server"):
    log = logging.getLogger(f"test.{process}")
    log.propagate = False
    log.setLevel(logging.DEBUG)
    handler = logs.file_handler(logs.log_path(settings.data_dir, process))
    log.handlers = [handler]
    log.info("camera grusgang online at rtsp://u:p@192.0.2.1/s")
    log.warning("drift on mot-wikings")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("ingest crashed")
    log.debug("detail")
    handler.close()


def test_read_newest_first_with_tracebacks_filters(settings):
    _write(settings)
    entries = logs.read(settings.data_dir, "server")
    assert [e["level"] for e in entries] == ["DEBUG", "ERROR", "WARNING", "INFO"]
    assert entries[1]["message"].startswith("ingest crashed\nTraceback")
    assert "ValueError: boom" in entries[1]["message"]
    assert entries[3]["message"] == "camera grusgang online at rtsp://***@192.0.2.1/s"
    assert entries[0]["ts"].endswith("Z")
    assert [e["level"] for e in logs.read(settings.data_dir, "server", level="WARNING")] == \
        ["ERROR", "WARNING"]
    assert [e["message"] for e in logs.read(settings.data_dir, "server", text="WIKINGS")] == \
        ["drift on mot-wikings"]
    assert len(logs.read(settings.data_dir, "server", limit=2)) == 2


def test_reads_rotated_files_in_order(settings):
    path = logs.log_path(settings.data_dir, "ingest")
    path.parent.mkdir(parents=True)
    path.with_name("ingest.log.1").write_text("2026-11-01T10:00:00.000Z INFO    a: older\n")
    path.write_text("2026-11-01T11:00:00.000Z INFO    a: newer\n")
    assert [e["message"] for e in logs.read(settings.data_dir, "ingest")] == ["newer", "older"]
    assert b"".join(logs.chunks(settings.data_dir, "ingest")).decode().splitlines() == [
        "2026-11-01T10:00:00.000Z INFO    a: older", "2026-11-01T11:00:00.000Z INFO    a: newer"]


def test_mediamtx_lines_get_levels(settings):
    log = logs.mediamtx_logger(settings.data_dir)
    logs.mediamtx_line(log, "2026/11/01 10:00:00 WAR [path grusgang] detected drift, resetting")
    logs.mediamtx_line(log, "2026/11/01 10:00:01 INF [RTSP] listener opened on :8554")
    for h in log.handlers:
        h.close()
    entries = logs.read(settings.data_dir, "mediamtx")
    assert [(e["level"], e["message"]) for e in entries] == [
        ("INFO", "[RTSP] listener opened on :8554"),
        ("WARNING", "[path grusgang] detected drift, resetting")]


def test_api_is_for_admins(settings):
    _write(settings)
    with app_client(settings) as c:
        assert [p["process"] for p in c.get("/api/logs").json()] == ["server"]
        r = c.get("/api/logs/server", params={"level": "ERROR"})
        assert [e["message"].split("\n")[0] for e in r.json()] == ["ingest crashed"]
        assert c.get("/api/logs/nope").status_code == 404
        d = c.get("/api/logs/server/download")
        assert d.status_code == 200 and "drift on mot-wikings" in d.text
        assert c.get("/api/logs/compact/download").status_code == 404
    with app_client(settings, email="viewer@example.org", admin=False) as c:
        assert c.get("/api/logs").status_code == 403
        assert c.get("/api/logs/server").status_code == 403


def test_worker_sends_its_log_to_the_server(settings):
    from foxcam_worker.logship import LogShipper

    settings.worker_token = "secret"
    settings.open_worker_access = False
    with app_client(settings, email=None) as c:
        c.headers["Authorization"] = "Bearer secret"
        shipper = LogShipper(c, "gpu box/1")
        log = logging.getLogger("foxcam_worker.test")
        log.addHandler(shipper)
        log.warning("job 7: model failed for rtsp://u:p@192.0.2.9/x")
        log.debug("not sent (below INFO)")
        assert shipper.flush_to_server() and not shipper.buffer
        log.removeHandler(shipper)
        # Unauthenticated workers are turned away.
        del c.headers["Authorization"]
        assert c.post("/api/worker/logs", json={"worker_id": "x", "entries": []}).status_code \
            == 401
    entries = logs.read(settings.data_dir, "worker-gpu_box_1")
    assert [(e["level"], e["logger"], e["message"]) for e in entries] == [
        ("WARNING", "foxcam_worker.test", "job 7: model failed for rtsp://***@192.0.2.9/x")]
    with app_client(settings) as c:
        assert "worker-gpu_box_1" in [p["process"] for p in c.get("/api/logs").json()]
        assert len(c.get("/api/logs/worker-gpu_box_1").json()) == 1
        assert c.get("/api/logs/..%2Fsecret").status_code == 404


def test_worker_logs_are_capped(settings, monkeypatch):
    monkeypatch.setattr(logs, "MAX_WORKER_LOGS", 1)
    entry = [{"ts": "2026-11-01T10:00:00Z", "level": "INFO", "logger": "w", "message": "hi"}]
    assert logs.write_worker_entries(settings.data_dir, "a", entry)
    assert logs.write_worker_entries(settings.data_dir, "a", entry)
    assert not logs.write_worker_entries(settings.data_dir, "b", entry)
