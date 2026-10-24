"""API keys: `Authorization: Bearer msk_…` acts as the key's user, without a session."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from foxcam import accounts
from foxcam.db import session_scope
from foxcam.models import ApiKey, utcnow
from foxcam.web.app import create_app

from .conftest import app_client


def _key(c: TestClient, **body) -> dict:
    body.setdefault("scopes", ["read", "write", "admin"])
    r = c.post("/api/auth/api-keys", json={"name": "script", **body})
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(settings, key: str) -> TestClient:
    c = TestClient(create_app(settings, background=False))
    c.headers["Authorization"] = f"Bearer {key}"
    return c


def test_a_key_acts_as_its_user(settings):
    with app_client(settings) as c:
        created = _key(c)
        key = created["key"]
        assert key.startswith("msk_") and created["prefix"] == key[:10]
        [listed] = c.get("/api/auth/api-keys").json()
        assert "key" not in listed and listed["last_used_at"] is None
    with _bearer(settings, key) as api:
        assert api.get("/api/cameras").status_code == 200
        assert api.get("/api/queue").status_code == 200  # admin scope: an admin
        r = api.post("/api/profiles", json={"id": "cars", "targets": ["car"]})
        assert r.status_code == 201, r.text
    with app_client(settings) as c:
        assert c.get("/api/auth/api-keys").json()[0]["last_used_at"] is not None


def test_only_with_the_users_rights(settings):
    with app_client(settings, email="user@example.org", admin=False) as c:
        r = c.post("/api/auth/api-keys", json={"name": "x", "scopes": ["read", "admin"]})
        assert r.status_code == 403  # only admins can hand out the admin scope
        key = _key(c, scopes=["read", "write"])["key"]
    with _bearer(settings, key) as api:
        assert api.get("/api/cameras").json() == []  # nothing shared with this user
        assert api.get("/api/queue").status_code == 403


def test_read_only_keys_only_read(settings):
    with app_client(settings) as c:
        created = _key(c, scopes=[])
        assert created["scopes"] == ["read"]  # always
    with _bearer(settings, created["key"]) as api:
        assert api.get("/api/profiles").status_code == 200
        r = api.patch("/api/cameras/cam", json={"name": "x"})
        assert r.status_code == 403 and "read-only" in r.text


def test_without_the_admin_scope_an_admins_key_is_no_admin(settings):
    with app_client(settings) as c:
        created = _key(c, scopes=["read", "write"])
    with _bearer(settings, created["key"]) as api:
        assert [c["id"] for c in api.get("/api/cameras").json()] == ["cam"]  # still sees all
        r = api.get("/api/users")
        assert r.status_code == 403 and "admin scope" in r.text
        assert api.post("/api/profiles", json={"id": "x", "targets": ["car"]}).status_code \
            == 403
        assert api.put("/api/cameras/cam/mask", json={"rects": []}).status_code == 200


def test_keys_cannot_manage_the_account(settings):
    with app_client(settings) as c:
        key = _key(c)["key"]
    with _bearer(settings, key) as api:
        assert api.post("/api/auth/api-keys", json={"name": "more"}).status_code == 401
        assert api.post("/api/auth/password", json={"new": "x" * 12}).status_code == 401


def test_revoked_expired_and_disabled(settings):
    with app_client(settings) as c:
        revoked = _key(c)
        expiring = _key(c, expires_days=1)
        kept = _key(c)
        assert c.delete(f"/api/auth/api-keys/{revoked['id']}").status_code == 200
    with session_scope() as s:
        s.get(ApiKey, expiring["id"]).expires_at = utcnow() - timedelta(seconds=1)
    for key in (revoked["key"], expiring["key"], "msk_made-up"):
        with _bearer(settings, key) as api:
            r = api.get("/api/cameras")
            assert r.status_code == 401 and "API key" in r.text
    with _bearer(settings, kept["key"]) as api:
        assert api.get("/api/cameras").status_code == 200
    with session_scope() as s:
        uid = s.get(ApiKey, kept["id"]).user_id
    accounts.create_user(settings, "admin2@example.org", "x" * 12, is_admin=True)
    accounts.update_user(uid, disabled=True)  # (not the last admin any more)
    with _bearer(settings, kept["key"]) as api:
        assert api.get("/api/cameras").status_code == 401


def test_other_users_keys_cannot_be_revoked(settings):
    with app_client(settings) as c:
        key_id = _key(c)["id"]
    with app_client(settings, email="user@example.org", admin=False) as c:
        assert c.delete(f"/api/auth/api-keys/{key_id}").status_code == 404
        assert c.get("/api/auth/api-keys").json() == []
