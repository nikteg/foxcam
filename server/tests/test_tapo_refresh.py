"""Token refresh for the shared Tapo cloud session file (with a fake ontapo)."""

from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest
from media_streamer import tapo_account
from media_streamer.ingest import tapo_cloud


class Unauthorized(Exception):
    pass


class FakeSession:
    refresh_ok = True
    refreshes = 0

    def __init__(self, token: str = "", refresh_token: str = ""):
        self.token = token
        self.refresh_token = refresh_token

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("token", ""), d.get("refreshToken", ""))

    def to_dict(self):
        return {"token": self.token, "refreshToken": self.refresh_token, "email": "a@b.c"}

    async def refresh(self):
        FakeSession.refreshes += 1
        if not FakeSession.refresh_ok:
            raise Unauthorized("refresh failed")
        self.token = f"t{FakeSession.refreshes + 1}"
        self.refresh_token = f"r{FakeSession.refreshes + 1}"


@pytest.fixture(autouse=True)
def fake_ontapo(monkeypatch):
    mod = types.ModuleType("ontapo")
    mod.Unauthorized = Unauthorized
    mod.OnTapoError = Exception
    mod.OnTapo = FakeSession
    monkeypatch.setitem(sys.modules, "ontapo", mod)
    FakeSession.refresh_ok = True
    FakeSession.refreshes = 0


def _store(settings, token="t1", refresh_token="r1"):
    tapo_cloud.save_session(settings, FakeSession(token, refresh_token))


def _stored(settings):
    return json.loads(settings.tapo_session_file.read_text())


def _expiring(session, valid="t2"):
    """A call that fails until the session holds the ``valid`` token."""
    async def call():
        if session.token != valid:
            raise Unauthorized("expired")
        return "ok"
    return call


def test_refreshes_once_and_saves(settings):
    _store(settings)
    session = tapo_cloud.load_session(settings)
    assert asyncio.run(tapo_cloud.call_with_refresh(settings, session, _expiring(session))) == "ok"
    assert FakeSession.refreshes == 1
    assert _stored(settings)["token"] == "t2"
    assert _stored(settings)["refreshToken"] == "r2"


def test_adopts_token_refreshed_by_someone_else(settings):
    _store(settings)
    stale = tapo_cloud.load_session(settings)
    _store(settings, "t2", "r2")  # e.g. another camera thread refreshed meanwhile
    assert asyncio.run(tapo_cloud.call_with_refresh(settings, stale, _expiring(stale))) == "ok"
    assert FakeSession.refreshes == 0  # did not burn the (possibly rotated) refresh token
    assert stale.refresh_token == "r2"


def test_rejected_refresh_marks_expired(settings):
    _store(settings)
    FakeSession.refresh_ok = False
    session = tapo_cloud.load_session(settings)
    with pytest.raises(tapo_cloud.TapoSessionExpired):
        asyncio.run(tapo_cloud.call_with_refresh(settings, session, _expiring(session)))
    assert tapo_cloud.session_expired(settings)
    assert tapo_account.status(settings)["expired"] is True

    # Further attempts don't hit TP-Link again until someone signs in.
    with pytest.raises(tapo_cloud.TapoSessionExpired):
        asyncio.run(tapo_cloud.refresh_session(settings, session))
    assert FakeSession.refreshes == 1

    _store(settings, "t9", "r9")  # a new sign-in clears the marker
    assert not tapo_cloud.session_expired(settings)
    assert tapo_account.status(settings)["expired"] is False


def test_logout_clears_expired_marker(settings):
    _store(settings)
    tapo_cloud.expired_marker(settings).touch()
    tapo_account.logout(settings)
    assert not settings.tapo_session_file.exists()
    assert not tapo_cloud.session_expired(settings)
