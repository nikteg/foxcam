"""Fixes from the security review: live-view proxy paths, the MediaMTX login, camera
sources users may add, sign-up and e-mail verification, reused camera ids, proxies."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from media_streamer import accounts, camera_store, mediamtx_auth
from media_streamer.config import CameraConfig, RtspSource
from media_streamer.config import TestSource as PatternSource
from media_streamer.db import session_scope
from media_streamer.ingest.supervisor import ffmpeg_publisher_args, publish_url
from media_streamer.models import Invite, User
from media_streamer.web import api_auth, live
from media_streamer.web.app import create_app
from media_streamer.web.queries import _redact_url, public_settings
from pydantic import ValidationError

from .conftest import PASSWORD, add_segment, app_client, sign_in

LAN_CAMERA = "rtsp://user:pw@192.168.1.50:554/stream1"


@pytest.fixture(autouse=True)
def _fresh_limiter():
    api_auth.limiter.failures.clear()
    api_auth.email_limiter.failures.clear()


# --- 1. live-view proxy ---------------------------------------------------------------

def _mock_mediamtx(c: TestClient) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    c.app.state.live_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen


def test_live_proxy_reaches_only_the_cameras_own_path(settings):
    settings.cameras.append(CameraConfig(id="victim", source=PatternSource(type="test")))
    with TestClient(create_app(settings, background=False)) as c:
        accounts.grant("cam", sign_in(c, settings, "bob@example.org"), "viewer")
        seen = _mock_mediamtx(c)
        assert c.get("/api/live/cam/main/hls/index.m3u8").status_code == 200
        assert c.get("/api/live/cam/main/hls/hls.min.js").status_code == 200
        assert c.post("/api/live/cam/main/webrtc/whep", content=b"v=0").status_code == 200
        assert c.patch("/api/live/cam/main/webrtc/whep/a1-b2", content=b"x").status_code == 200
        assert c.delete("/api/live/cam/main/webrtc/whep/a1-b2").status_code == 200
        assert [r.url.path for r in seen] == ["/cam/index.m3u8", "/cam/hls.min.js",
                                              "/cam/whep", "/cam/whep/a1-b2", "/cam/whep/a1-b2"]
        # Signed in to MediaMTX as the internal user; the browser's session stays here.
        assert seen[0].headers["authorization"] == mediamtx_auth.basic_auth(settings)
        assert "cookie" not in seen[0].headers

        for bad in ("%2e%2e/%2e%2e/victim/raw/index.m3u8",  # another camera, unmasked
                    "%2e%2e/victim/index.m3u8",
                    "..%2Fvictim%2Findex.m3u8",
                    "raw/index.m3u8",                       # cam/raw: before the masks
                    "raw/", "low/index.m3u8", "a/b.m3u8", "whep/x/y", "..", ".m3u8"):
            assert c.get(f"/api/live/cam/main/hls/{bad}").status_code == 404, bad
        # Publishing (WHIP) through the proxy's login, or any other POST.
        assert c.post("/api/live/cam/main/webrtc/whip", content=b"v=0").status_code == 404
        assert c.post("/api/live/cam/main/webrtc/index.html").status_code == 404
        assert c.patch("/api/live/cam/main/webrtc/whep").status_code == 404
        assert len(seen) == 5
        # A body far beyond any WHEP offer.
        big = b"x" * (live.MAX_BODY + 1)
        assert c.post("/api/live/cam/main/webrtc/whep", content=big).status_code == 413


def test_live_rest_patterns():
    get = live.REST["GET"]
    assert get.fullmatch("") and get.fullmatch("index.m3u8") and get.fullmatch("a_b-1.mp4")
    for bad in ("..", "../x.m3u8", "x/../y.m3u8", "raw/index.m3u8", "x..m3u8", "index"):
        assert not get.fullmatch(bad), bad


# --- 2. MediaMTX login ----------------------------------------------------------------

def test_mediamtx_password_is_random_stable_and_private(settings):
    pw = mediamtx_auth.password(settings)
    assert len(pw) >= 24 and mediamtx_auth.password(settings) == pw
    path = settings.data_dir / mediamtx_auth.FILE
    assert path.stat().st_mode & 0o077 == 0
    assert mediamtx_auth.process_env(settings) == {"MTX_AUTHINTERNALUSERS_0_PASS": pw,
                                                    "MEDIA_MTX_PASSWORD": pw}
    settings.mediamtx.password = "from config"
    assert mediamtx_auth.password(settings) == "from config"
    # Never shown in the UI's configuration view.
    assert public_settings(settings)["mediamtx"]["password"] == "***"


def test_everything_that_talks_to_mediamtx_signs_in(settings):
    pw = mediamtx_auth.password(settings)
    cam = settings.camera("cam")
    assert publish_url(settings, cam) == f"rtsp://media-streamer:{pw}@127.0.0.1:1/cam"
    assert publish_url(settings, cam) in ffmpeg_publisher_args(settings, cam)
    assert mediamtx_auth.url(settings, "cam/raw").startswith("rtsp://media-streamer:")
    # The transcoders MediaMTX runs get it from the environment (cli.py).
    hook = "rtsp://localhost:$RTSP_PORT/cam/raw"
    assert mediamtx_auth.is_local(hook)
    assert mediamtx_auth.with_login(hook, "p@ss") == \
        "rtsp://media-streamer:p%40ss@localhost:$RTSP_PORT/cam/raw"
    assert mediamtx_auth.with_login("rtsp://a:b@host/x", "pw") == "rtsp://a:b@host/x"
    assert not mediamtx_auth.is_local("rtsp://192.168.1.50/x")


def test_mediamtx_yml_trusts_no_address_with_video():
    conf = yaml.safe_load((Path(__file__).parents[2] / "mediamtx" / "mediamtx.yml").read_text())
    users = conf["authInternalUsers"]
    # media-streamer first (MTX_AUTHINTERNALUSERS_0_PASS), with a password hash.
    assert users[0]["user"] == mediamtx_auth.USER and users[0]["pass"].startswith("sha256:")
    for u in users:
        actions = {p["action"] for p in u["permissions"]}
        if u["user"] == "any":
            assert actions <= {"api", "metrics"}, u


@pytest.mark.parametrize("url", [
    "rtsp://127.0.0.1:8554/cam/raw", "rtsp://localhost:8554/cam", "rtsp://[::1]:8554/cam",
    "rtsp://127.1/cam", "rtsp://169.254.169.254/x", "rtsp://0.0.0.0:8554/cam",
    "rtsp://[::ffff:127.0.0.1]:8554/cam",
])
def test_users_cannot_add_cameras_on_the_server_itself(settings, url):
    with app_client(settings, email="bob@example.org", admin=False) as c:
        r = c.post("/api/cameras", json={"name": "x", "source": {"type": "rtsp", "url": url}})
        assert r.status_code == 403, r.text
        r = c.post("/api/cameras/probe", json={"source": {"type": "rtsp", "url": url}})
        assert r.status_code == 403, r.text
    with app_client(settings) as admin:  # admins may (e.g. a stream from another tool)
        r = admin.post("/api/cameras", json={"name": "x", "source": {"type": "rtsp",
                                                                     "url": url}})
        assert r.status_code == 201, r.text


def test_the_mediamtx_host_counts_as_the_server(settings, monkeypatch):
    real = camera_store._addresses
    monkeypatch.setattr(camera_store, "_addresses", lambda h: (
        real("10.0.0.7") if h in ("mediamtx", "cams.lan") else real(h)))
    settings.mediamtx.rtsp_url = "rtsp://mediamtx:8554"
    with pytest.raises(camera_store.CameraError) as e:
        camera_store.check_source(settings, PatternSource(type="test"), admin=False)
    assert e.value.status == 403
    with pytest.raises(camera_store.CameraError):
        camera_store.check_source(settings, RtspSource(type="rtsp", url="rtsp://cams.lan/x"),
                                  admin=False)
    camera_store.check_source(settings, RtspSource(type="rtsp", url=LAN_CAMERA), admin=False)


# --- 3. test sources ------------------------------------------------------------------

def test_test_source_size_and_fps_are_validated():
    payload = "64x64[a];[a]nullsink;movie=/data/x.mp4,scale=64:48"
    with pytest.raises(ValidationError):
        PatternSource(type="test", size=payload)
    for bad in ({"size": "64x64:rate=1"}, {"size": "1280x720\n"}, {"fps": 0}, {"fps": 61}):
        with pytest.raises(ValidationError):
            PatternSource.model_validate({"type": "test", **bad})
    assert PatternSource(type="test", size="640x360", fps=30).size == "640x360"


def test_only_admins_add_test_cameras_and_sample_files(settings):
    (settings.samples_dir / "fox.mp4").write_bytes(b"x")
    with app_client(settings, email="bob@example.org", admin=False) as c:
        for source in ({"type": "test"}, {"type": "file", "path": "fox.mp4"}):
            r = c.post("/api/cameras", json={"name": "x", "source": source})
            assert r.status_code == 403, source
            assert c.post("/api/cameras/probe", json={"source": source}).status_code == 403
        assert c.get("/api/cameras-sample-files").status_code == 403
        r = c.post("/api/cameras", json={"name": "Garden", "source": {"type": "rtsp",
                                                                      "url": LAN_CAMERA}})
        assert r.status_code == 201


# --- 4. probe -------------------------------------------------------------------------

def test_probe_checks_the_source_and_allows_only_rtsp(settings, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **_kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "Connection refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with app_client(settings, email="bob@example.org", admin=False) as c:
        for url in ("http://192.168.1.1/admin", "file:///etc/passwd", "concat:/a|/b"):
            r = c.post("/api/cameras/probe", json={"source": {"type": "rtsp", "url": url}})
            assert r.status_code == 400, url
        assert calls == []
        r = c.post("/api/cameras/probe", json={"source": {"type": "rtsp", "url": LAN_CAMERA}})
        assert r.status_code == 200 and r.json()["ok"] is False
    args = calls[0]
    assert args[args.index("-protocol_whitelist") + 1] == "rtsp,rtsps,tcp,tls,udp,rtp"
    assert args[-1] == LAN_CAMERA


# --- 5. sign-up and e-mail verification -----------------------------------------------

def test_admin_emails_need_a_verified_address(settings):
    settings.auth.admin_emails = ["boss@example.org"]
    with app_client(settings, email=None) as c:
        # registration: invite - the admin address no longer skips it.
        r = c.post("/api/auth/register", json={"email": "boss@example.org",
                                               "password": PASSWORD})
        assert r.status_code == 403
        # registration: open - an account, but no admin rights from the address.
        settings.auth.registration = "open"
        r = c.post("/api/auth/register", json={"email": "boss@example.org",
                                               "password": PASSWORD})
        assert r.status_code == 200 and r.json()["is_admin"] is False
        assert r.json()["user"]["email_verified"] is False
        r = c.post("/api/auth/login", json={"email": "boss@example.org", "password": PASSWORD})
        assert r.json()["is_admin"] is False


def test_oauth_never_takes_over_an_unverified_account(settings):
    settings.auth.registration = "open"
    squatter = accounts.register(settings, "victim@example.org", PASSWORD, None, None)
    with pytest.raises(accounts.AccountError) as e:
        accounts.oauth_user(settings, "google", "sub-1", "victim@example.org", True, None)
    assert e.value.status == 409
    # The owner signing in with the password and connecting Google verifies it.
    accounts.link_identity(squatter, "google", "sub-1", "victim@example.org", True)
    assert accounts.oauth_user(settings, "google", "sub-1", "victim@example.org", True,
                               None) == squatter
    with session_scope() as s:
        assert s.get(User, squatter).email_verified is True
    # Accounts an admin made are verified: matched by e-mail as before.
    uid = accounts.create_user(settings, "kim@example.org", PASSWORD)["id"]
    assert accounts.oauth_user(settings, "github", "g-1", "kim@example.org", True, None) == uid


def test_oauth_admin_email_still_signs_up(settings):
    settings.auth.admin_emails = ["boss@example.org"]
    uid = accounts.oauth_user(settings, "google", "s", "boss@example.org", True, "Boss")
    assert accounts.get_user(uid)["is_admin"] and accounts.get_user(uid)["email_verified"]


def test_an_invite_can_only_be_used_once(settings):
    admin = accounts.create_user(settings, "admin@example.org", PASSWORD, is_admin=True)["id"]
    kim = accounts.create_user(settings, "kim@example.org", PASSWORD)["id"]
    _, token = accounts.create_invite(accounts.principal_for(admin),
                                      [{"camera_id": "cam", "role": "viewer"}])
    assert accounts.accept_invite(token, kim) == ["cam"]
    with pytest.raises(accounts.AccountError) as e:
        accounts.accept_invite(token, admin)
    assert e.value.status == 410


# --- 6. reused camera ids -------------------------------------------------------------

def test_a_removed_cameras_id_is_not_handed_to_someone_else(settings):
    with app_client(settings) as admin:
        r = admin.post("/api/cameras", json={"name": "Garden", "id": "garden", "source": {
            "type": "rtsp", "url": LAN_CAMERA}})
        assert r.status_code == 201
        add_segment(settings, camera="garden", with_file=False)
        r = admin.post("/api/invites", json={"cameras": [
            {"camera_id": "garden", "role": "viewer"}, {"camera_id": "cam", "role": "viewer"}]})
        assert r.status_code in (200, 201)
        r = admin.post("/api/invites", json={"cameras": [
            {"camera_id": "garden", "role": "viewer"}]})
        assert admin.delete("/api/cameras/garden").status_code == 200
        # Its open invites no longer give it (an invite for nothing else is gone).
        with session_scope() as s:
            invites = [inv.cameras for inv in s.query(Invite).all()]
        assert invites == [[{"camera_id": "cam", "role": "viewer"}]]

    with app_client(settings, email="eve@example.org", admin=False) as eve:
        body = {"name": "Garden", "source": {"type": "rtsp", "url": LAN_CAMERA}}
        r = eve.post("/api/cameras", json={**body, "id": "garden"})
        assert r.status_code == 409, r.text
        # Without an id she gets a fresh one, not the removed camera's.
        r = eve.post("/api/cameras", json=body)
        assert r.status_code == 201 and r.json()["id"] == "garden-2"
        assert eve.get("/api/segments?camera=garden").json() == []

    with app_client(settings) as admin:  # admins may, to continue its history
        r = admin.post("/api/cameras", json={"name": "Garden", "id": "garden", "source": {
            "type": "rtsp", "url": LAN_CAMERA}})
        assert r.status_code == 201


# --- 7. reverse proxies ---------------------------------------------------------------

def test_session_cookie_is_secure_when_public_url_is_https(settings):
    settings.public_url = "https://cams.example.org"
    with TestClient(create_app(settings, background=False)) as c:
        accounts.create_user(settings, "kim@example.org", PASSWORD)
        r = c.post("/api/auth/login", json={"email": "kim@example.org", "password": PASSWORD})
        assert "secure" in r.headers["set-cookie"].lower()


def test_failed_logins_from_one_address_dont_lock_out_others(settings):
    accounts.create_user(settings, "admin@example.org", PASSWORD, is_admin=True)
    app = create_app(settings, background=False)
    bad = {"email": "admin@example.org", "password": "wrong"}
    with TestClient(app, client=("203.0.113.9", 1)) as attacker, \
            TestClient(app, client=("198.51.100.2", 1)) as owner:
        for _ in range(10):
            assert attacker.post("/api/auth/login", json=bad).status_code == 401
        assert attacker.post("/api/auth/login", json=bad).status_code == 429
        r = owner.post("/api/auth/login", json={**bad, "password": PASSWORD})
        assert r.status_code == 200


# --- low ------------------------------------------------------------------------------

def test_security_headers_and_no_public_api_docs(settings):
    with TestClient(create_app(settings, background=False)) as c:
        r = c.get("/api/auth/state")
        assert r.headers["content-security-policy"] == "frame-ancestors 'self'"
        assert r.headers["x-frame-options"] == "SAMEORIGIN"
        for path in ("/docs", "/redoc", "/openapi.json"):  # 404, or the web UI's page
            text = c.get(path).text
            assert "swagger" not in text.lower() and "redoc" not in text.lower() \
                and '"openapi"' not in text, path


def test_redact_url_hides_passwords_with_at_signs():
    assert _redact_url("rtsp://admin:p@ss@192.168.1.5/s@1") == "rtsp://***@192.168.1.5/s@1"
    assert _redact_url("rtsp://192.168.1.5/stream") == "rtsp://192.168.1.5/stream"
