"""Accounts, sessions, camera sharing, invites, OAuth and the live-view proxy."""

from __future__ import annotations

import base64
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from foxcam import accounts, oauth
from foxcam.config import OAuthProviderConfig
from foxcam.models import SegmentStatus
from foxcam.web import api_auth
from foxcam.web.app import create_app

from .conftest import PASSWORD, add_detection, add_job, add_segment, app_client, sign_in


@pytest.fixture(autouse=True)
def _fresh_limiter():
    api_auth.limiter.failures.clear()
    api_auth.email_limiter.failures.clear()


def _route_url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path).replace(":path", "")


def test_every_api_route_needs_a_login(settings):
    """Default deny: a route added without thinking about auth is still private."""
    app = create_app(settings, background=False)
    public = ("/api/auth/", "/api/worker/")
    with TestClient(app) as c:
        checked = 0
        for path, methods in app.openapi()["paths"].items():
            if not path.startswith("/api/") or path.startswith(public):
                continue
            for method in methods:
                r = c.request(method.upper(), _route_url(path), json={})
                assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
                checked += 1
        assert checked > 50
        # The live-view proxy too (it is not in the OpenAPI schema).
        assert c.get("/api/live/cam/main/webrtc/").status_code == 401
        # Login state is public; protected /api/auth routes check by themselves.
        assert c.get("/api/auth/state").json()["signed_in"] is False
        assert c.patch("/api/auth/me", json={"name": "x"}).status_code == 401
        assert c.post("/api/auth/password", json={"new": "x" * 10}).status_code == 401


def test_setup_creates_the_first_admin_with_the_code(settings):
    with app_client(settings, email=None) as c:
        state = c.get("/api/auth/state").json()
        assert state["setup_required"] and not state["signed_in"]
        body = {"email": "Me@Example.org", "password": PASSWORD, "code": "wrong"}
        assert c.post("/api/auth/setup", json=body).status_code == 403
        code = (settings.data_dir / "setup-code").read_text().strip()
        r = c.post("/api/auth/setup", json={**body, "code": code})
        assert r.status_code == 200
        me = r.json()["user"]
        assert me["email"] == "me@example.org" and me["is_admin"]
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert c.get("/api/cameras").status_code == 200
        assert not (settings.data_dir / "setup-code").exists()
        assert c.post("/api/auth/setup", json={**body, "code": code}).status_code == 409


def test_login_logout_and_brute_force_limit(settings):
    accounts.create_user(settings, "ann@example.org", PASSWORD)
    with app_client(settings, email=None) as c:
        bad = {"email": "ann@example.org", "password": "nope-nope"}
        assert c.post("/api/auth/login", json=bad).status_code == 401
        assert c.post("/api/auth/login", json={**bad, "email": "nobody@x.org"}).status_code \
            == 401
        r = c.post("/api/auth/login", json={**bad, "password": PASSWORD})
        assert r.status_code == 200 and r.json()["user"]["email"] == "ann@example.org"
        assert c.get("/api/status").status_code == 200
        c.post("/api/auth/logout")
        assert c.get("/api/status").status_code == 401
        for _ in range(10):
            c.post("/api/auth/login", json=bad)
        assert c.post("/api/auth/login", json={**bad, "password": PASSWORD}).status_code == 429


def test_cross_site_writes_are_refused(settings):
    with app_client(settings) as c:
        evil = {"Origin": "https://evil.example"}
        r = c.put("/api/cameras/cam/mask", json={"rects": []}, headers=evil)
        assert r.status_code == 403
        ok = c.put("/api/cameras/cam/mask", json={"rects": []},
                   headers={"Origin": "http://testserver"})
        assert ok.status_code == 200
        # Login CSRF as well.
        assert c.post("/api/auth/login", json={"email": "a@b", "password": "x"},
                      headers=evil).status_code == 403


def _two_users(settings):
    """An admin-owned config camera `cam`, plus Bob who adds his own camera."""
    owner = TestClient(create_app(settings, background=False))
    owner.__enter__()
    sign_in(owner, settings, "bob@example.org")
    r = owner.post("/api/cameras", json={"name": "Bob's garden", "source": {
        "type": "rtsp", "url": "rtsp://user:pw@192.168.1.50:554/stream1"}})
    assert r.status_code == 201, r.text
    other = TestClient(create_app(settings, background=False))
    other.__enter__()
    sign_in(other, settings, "eve@example.org")
    return owner, other, r.json()["id"]


def test_cameras_are_private_until_shared(settings):
    owner, other, cam_id = _two_users(settings)
    try:
        # Bob owns what he added and sees only that (not the config camera).
        assert [(c["id"], c["role"]) for c in owner.get("/api/cameras").json()] == \
            [(cam_id, "owner")]
        assert other.get("/api/cameras").json() == []
        seg = add_segment(settings, camera=cam_id, status=SegmentStatus.DONE, with_file=False)
        det = add_detection(seg, add_job(seg), 100)
        assert len(owner.get("/api/segments").json()) == 1
        assert other.get("/api/segments").json() == []
        assert other.get(f"/api/segments?camera={cam_id}").json() == []
        assert other.get(f"/api/segments/{seg}").status_code == 404
        assert other.get("/api/detections").json() == []
        assert other.get(f"/api/detections/{det}/frame.jpg").status_code == 404
        assert other.get(f"/api/cameras/{cam_id}/snapshot.jpg").status_code == 404
        assert other.get(f"/api/live/{cam_id}/main/webrtc/").status_code == 404
        assert other.get("/api/status").json()["cameras"] == []

        # Share as viewer: Eve sees it, but cannot change anything.
        r = owner.post(f"/api/cameras/{cam_id}/access",
                       json={"email": "EVE@example.org", "role": "viewer"})
        assert r.status_code == 201
        eve_id = r.json()["user_id"]
        assert [c["role"] for c in other.get("/api/cameras").json()] == ["viewer"]
        assert len(other.get("/api/detections").json()) == 1
        assert other.put(f"/api/cameras/{cam_id}/mask", json={"rects": []}).status_code == 403
        assert other.post(f"/api/detections/{det}/feedback",
                          json={"correct": True}).status_code == 403
        assert other.delete(f"/api/cameras/{cam_id}").status_code == 403
        assert other.get(f"/api/cameras/{cam_id}/access").status_code == 403

        # Editor: settings yes, sharing and removing no.
        assert owner.put(f"/api/cameras/{cam_id}/access/{eve_id}",
                         json={"role": "editor"}).status_code == 200
        assert other.put(f"/api/cameras/{cam_id}/mask", json={"rects": []}).status_code == 200
        assert other.post(f"/api/detections/{det}/feedback",
                          json={"correct": True}).status_code == 200
        assert other.post(f"/api/cameras/{cam_id}/access",
                          json={"email": "x@y.z"}).status_code == 403
        members = owner.get(f"/api/cameras/{cam_id}/access").json()["members"]
        assert {(m["email"], m["role"]) for m in members} == {
            ("bob@example.org", "owner"), ("eve@example.org", "editor")}

        # Unknown e-mail: use an invite link instead.
        assert owner.post(f"/api/cameras/{cam_id}/access",
                          json={"email": "new@example.org"}).status_code == 404

        # Eve can leave; then it is gone for her.
        assert other.delete(f"/api/cameras/{cam_id}/access/{eve_id}").status_code == 200
        assert other.get("/api/cameras").json() == []

        # Removing the camera drops its shares (a new camera could reuse the id).
        assert owner.delete(f"/api/cameras/{cam_id}").status_code == 200
        assert accounts.camera_access(cam_id) == []
    finally:
        owner.__exit__(None, None, None)
        other.__exit__(None, None, None)


def test_admin_only_areas(settings):
    with app_client(settings, email="user@example.org", admin=False) as c:
        for method, url in [("GET", "/api/queue"), ("GET", "/api/workers/tokens"),
                            ("POST", "/api/workers/tokens"), ("GET", "/api/notifications"),
                            ("GET", "/api/storage"), ("GET", "/api/integrations/tapo"),
                            ("POST", "/api/profiles"), ("GET", "/api/users"),
                            ("GET", "/api/detections/feedback-export"),
                            ("GET", "/api/worker-tags")]:
            assert c.request(method, url, json={}).status_code == 403, url
        # Configuration: only what the UI needs, no camera sources or secrets.
        cfg = c.get("/api/settings").json()
        assert set(cfg) == {"segment_seconds", "events", "cameras"} and cfg["cameras"] == []
        # Tapo cameras use the server's account.
        r = c.post("/api/cameras", json={"name": "t", "source": {"type": "tapo_cloud",
                                                                  "device_name": "x"}})
        assert r.status_code == 403
        assert c.put("/api/cameras/cam/mask", json={"rects": []}).status_code == 404


def test_users_cannot_add_cameras_when_turned_off(settings):
    settings.auth.users_can_add_cameras = False
    with app_client(settings, email="user@example.org", admin=False) as c:
        assert c.get("/api/auth/state").json()["can_add_cameras"] is False
        r = c.post("/api/cameras", json={"name": "x", "source": {"type": "test"}})
        assert r.status_code == 403


def test_invite_link_creates_an_account_with_cameras(settings):
    with app_client(settings) as admin, app_client(settings, email=None) as guest:
        assert guest.post("/api/auth/register", json={
            "email": "kim@example.org", "password": PASSWORD}).status_code == 403
        r = admin.post("/api/invites", json={"cameras": [{"camera_id": "cam",
                                                          "role": "viewer"}],
                                             "note": "for Kim"})
        assert r.status_code == 201
        token = r.json()["token"]
        assert r.json()["path"] == f"/invite/{token}"
        preview = guest.get(f"/api/auth/invite/{token}").json()
        assert preview["cameras"] == [{"camera_id": "cam", "role": "viewer", "name": "Cam"}]
        assert preview["from"] == "admin@example.org"
        r = guest.post("/api/auth/register", json={"email": "kim@example.org",
                                                   "password": PASSWORD, "invite": token})
        assert r.status_code == 200
        assert [c["id"] for c in guest.get("/api/cameras").json()] == ["cam"]
        # Single use.
        assert guest.get(f"/api/auth/invite/{token}").status_code == 410
        assert [i["state"] for i in admin.get("/api/invites").json()] == ["used"]


def test_invites_only_for_owned_cameras(settings):
    owner, other, cam_id = _two_users(settings)
    try:
        assert other.post("/api/invites", json={"cameras": [
            {"camera_id": cam_id, "role": "viewer"}]}).status_code == 403
        assert owner.post("/api/invites", json={"cameras": [], "is_admin": True}) \
            .status_code == 403
        r = owner.post("/api/invites", json={"cameras": [{"camera_id": cam_id,
                                                          "role": "editor"}]})
        token = r.json()["token"]
        # An existing account accepts it while signed in.
        assert other.post(f"/api/auth/invite/{token}/accept").status_code == 200
        assert [c["role"] for c in other.get("/api/cameras").json()] == ["editor"]
    finally:
        owner.__exit__(None, None, None)
        other.__exit__(None, None, None)


def test_open_registration(settings):
    settings.auth.registration = "open"
    with app_client(settings, email=None) as c:
        r = c.post("/api/auth/register", json={"email": "a@example.org", "password": PASSWORD})
        assert r.status_code == 200 and r.json()["user"]["is_admin"] is False
        assert c.get("/api/cameras").json() == []
        short = {"email": "b@example.org", "password": "short"}
        assert c.post("/api/auth/register", json=short).status_code == 400


def test_user_admin_and_last_admin(settings):
    with app_client(settings) as c:
        r = c.post("/api/users", json={"email": "sam@example.org", "password": PASSWORD})
        sam = r.json()["id"]
        me = c.get("/api/auth/state").json()["user"]["id"]
        assert c.patch(f"/api/users/{me}", json={"is_admin": False}).status_code == 409
        assert c.delete(f"/api/users/{me}").status_code == 409
        with app_client(settings, email=None) as s:
            sign_in(s, settings, "sam@example.org")
            assert s.get("/api/cameras").status_code == 200
            # Disabling signs Sam out at once and blocks new logins.
            assert c.patch(f"/api/users/{sam}", json={"disabled": True}).status_code == 200
            assert s.get("/api/cameras").status_code == 401
            r = s.post("/api/auth/login", json={"email": "sam@example.org",
                                                "password": PASSWORD})
            assert r.status_code == 403
        assert c.delete(f"/api/users/{sam}").status_code == 200


def test_change_password_signs_out_other_browsers(settings):
    with app_client(settings, email="ann@example.org", admin=False) as a, \
            app_client(settings, email=None) as b:
        sign_in(b, settings, "ann@example.org")
        r = a.post("/api/auth/password", json={"current": "wrong", "new": "x" * 12})
        assert r.status_code == 403
        r = a.post("/api/auth/password", json={"current": PASSWORD, "new": "new password!"})
        assert r.status_code == 200
        assert a.get("/api/cameras").status_code == 200
        assert b.get("/api/cameras").status_code == 401


def test_auth_disabled_opens_everything(settings):
    settings.auth.enabled = False
    with app_client(settings, email=None) as c:
        state = c.get("/api/auth/state").json()
        assert state["enabled"] is False and state["is_admin"] and not state["setup_required"]
        assert c.get("/api/queue").status_code == 200
        assert [cam["role"] for cam in c.get("/api/cameras").json()] == ["owner"]


# --- live view proxy ------------------------------------------------------------------

def test_live_proxy_forwards_to_mediamtx(settings):
    seen: list[httpx.Request] = []

    def mediamtx(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(201, stream=httpx.ByteStream(b"v=0 answer"),
                                  headers={"content-type": "application/sdp",
                                           "location": "/cam/whep/abc-123",
                                           "set-cookie": "x=1"})
        return httpx.Response(200, stream=httpx.ByteStream(b"<html>player</html>"),
                              headers={"content-type": "text/html"})

    with app_client(settings) as c:
        c.app.state.live_client = httpx.AsyncClient(transport=httpx.MockTransport(mediamtx))
        page = c.get("/api/live/cam/main/webrtc/?controls=true")
        assert page.status_code == 200 and "player" in page.text
        assert str(seen[0].url) == "http://localhost:8889/cam/?controls=true"
        assert "cookie" not in seen[0].headers  # the session never reaches MediaMTX
        r = c.post("/api/live/cam/main/webrtc/whep", content=b"v=0 offer",
                   headers={"content-type": "application/sdp"})
        assert r.status_code == 201 and r.text == "v=0 answer"
        assert r.headers["location"] == "/api/live/cam/main/webrtc/whep/abc-123"
        assert "set-cookie" not in r.headers
        assert seen[1].content == b"v=0 offer"
        c.get("/api/live/cam/low/hls/index.m3u8")
        assert str(seen[2].url) == "http://localhost:8888/cam/low/index.m3u8"
        # Only the camera's own picture and its low-data copy, never cam/raw.
        assert c.get("/api/live/cam/raw/webrtc/").status_code == 404
        assert c.get("/api/live/nope/main/webrtc/").status_code == 404
        assert len(seen) == 3


# --- OAuth ----------------------------------------------------------------------------

def test_oauth_accounts(settings):
    settings.auth.admin_emails = ["boss@example.org"]
    # No account and no invite: refused.
    with pytest.raises(accounts.AccountError):
        accounts.oauth_user(settings, "google", "s1", "new@example.org", True, "New")
    # admin_emails may sign up (bootstrap without a password).
    boss = accounts.oauth_user(settings, "google", "s2", "boss@example.org", True, "Boss")
    assert accounts.get_user(boss)["is_admin"] and accounts.get_user(boss)["providers"] == \
        ["google"]
    # Same identity again: same account, even if the e-mail changed at the provider.
    assert accounts.oauth_user(settings, "google", "s2", "other@example.org", True, None) \
        == boss
    # An existing password account is linked by its verified e-mail...
    ann = accounts.create_user(settings, "ann@example.org", PASSWORD)["id"]
    assert accounts.oauth_user(settings, "github", "g1", "Ann@example.org", True, None) == ann
    # ...but never by an unverified one.
    with pytest.raises(accounts.AccountError):
        accounts.oauth_user(settings, "oidc", "o1", "ann@example.org", False, None)
    settings.auth.registration = "open"
    with pytest.raises(accounts.AccountError):
        accounts.oauth_user(settings, "oidc", "o2", "fresh@example.org", False, None)
    assert accounts.oauth_user(settings, "oidc", "o3", "fresh@example.org", True, None)


def _id_token(**claims) -> str:
    def part(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part(claims)}.sig"


def test_oidc_claims_are_checked():
    p = OAuthProviderConfig(id="g", type="google", client_id="cid", client_secret="x")
    good = {"iss": "https://accounts.google.com", "aud": "cid", "exp": time.time() + 60,
            "nonce": "n"}
    oauth._check_claims(p, oauth._jwt_claims(_id_token(**good)), "n")
    for bad in ({"aud": "other"}, {"exp": time.time() - 3600}, {"nonce": "x"},
                {"iss": "https://evil.example"}):
        with pytest.raises(oauth.OAuthError):
            oauth._check_claims(p, {**good, **bad}, "n")


def test_oauth_flow(settings, monkeypatch):
    settings.auth.oauth = [OAuthProviderConfig(id="google", type="google", client_id="cid",
                                               client_secret="secret")]
    accounts.create_user(settings, "ann@example.org", PASSWORD)

    async def authorize_url(p, flow, redirect_uri):
        assert redirect_uri == "http://testserver/api/auth/oauth/google/callback"
        return f"https://provider.example/auth?state={flow.state}"

    async def finish(p, flow, code, redirect_uri):
        assert code == "the-code"
        return oauth.ExternalUser("sub-1", "ann@example.org", True, "Ann")

    monkeypatch.setattr(oauth, "authorize_url", authorize_url)
    monkeypatch.setattr(oauth, "finish", finish)
    with app_client(settings, email=None) as c:
        assert c.get("/api/auth/state").json()["providers"] == [
            {"id": "google", "name": "Google", "type": "google"}]
        r = c.get("/api/auth/oauth/google/start?next=//evil.example", follow_redirects=False)
        assert r.status_code == 303
        state = r.headers["location"].split("state=")[1]
        # A forged state is refused.
        r = c.get("/api/auth/oauth/google/callback?code=the-code&state=forged",
                  follow_redirects=False)
        assert r.headers["location"].startswith("/login?error=")
        assert c.get("/api/cameras").status_code == 401
        c.get("/api/auth/oauth/google/start?next=/live", follow_redirects=False)
        state = c.cookies.get(api_auth.FLOW_COOKIE)
        flow_state = api_auth._unsign(settings, state)["state"]
        r = c.get(f"/api/auth/oauth/google/callback?code=the-code&state={flow_state}",
                  follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/live"
        assert c.get("/api/auth/state").json()["user"]["providers"] == ["google"]
        # A tampered flow cookie is ignored.
        assert api_auth._unsign(settings, state[:-1] + ("0" if state[-1] != "0" else "1")) \
            is None
