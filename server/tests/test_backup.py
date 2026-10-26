"""Settings export and import (backup.py): a round trip, secrets, and format versions."""

from __future__ import annotations

import pytest
from foxcam import backup, camera_store, notify, profiles, runtime_settings
from foxcam.db import session_scope
from foxcam.models import AppSetting, CameraState, ManagedCamera, NotificationChannel
from foxcam.web.queries import save_camera_state, save_motion

from .conftest import app_client


def _set_up(settings):
    runtime_settings.update(settings, {"processing.concurrency": 3}, [])
    profiles.save_profile(settings, {"id": "pets", "targets": ["cat", "dog"]}, create=True)
    camera_store.add_camera(settings, name="Yard", camera_id="yard",
                            source={"type": "rtsp", "url": "rtsp://user:pw@cam.local/s1"})
    camera_store.add_camera(settings, name="Shed", camera_id="shed",
                            source={"type": "rtsp", "url": "rtsp://cam2.local/s1"})
    save_camera_state("shed", [[0.0, 0.0, 0.5, 0.5]], private=False)
    save_motion("shed", "high", [[0.1, 0.1, 0.2, 0.2]])
    profiles.set_camera_profiles(settings, "shed", ["pets"])
    notify.save_channel(settings, {"id": "phone", "type": "ntfy", "topic": "foxes",
                                   "token": "tk_secret"}, create=True)


def _wipe():
    with session_scope() as s:
        for model in (AppSetting, ManagedCamera, CameraState, NotificationChannel):
            s.query(model).delete()
    from foxcam.models import DetectionProfile

    with session_scope() as s:
        s.query(DetectionProfile).delete()


def test_export_has_the_format_version_and_no_secrets(settings):
    _set_up(settings)
    data = backup.export(settings)
    assert data["config_format_version"] == backup.CONFIG_FORMAT_VERSION
    text = str(data)
    assert "pw@" not in text and "tk_secret" not in text
    yard = next(c for c in data["cameras"] if c["id"] == "yard")
    assert yard["login_removed"] and "cam.local" in yard["source"]["url"]
    assert data["notifications"][0]["secrets_removed"] == ["token"]


def test_round_trip_restores_what_has_no_secrets(settings):
    _set_up(settings)
    data = backup.export(settings)
    _wipe()
    report = backup.import_(settings, data)
    assert report["imported"]["profiles"] == 1 and report["imported"]["settings"] == 1
    # The camera with a login cannot come back without it; the other one does.
    assert [s["id"] for s in report["skipped"] if s["reason"] == "login"] == ["yard"]
    assert {c.id for c in camera_store.ui_cameras()} == {"shed"}
    with session_scope() as s:
        st = s.get(CameraState, "shed")
        assert st.motion_sensitivity == "high" and st.profiles == ["pets"]
    # The channel is back, but needs its token again.
    assert notify.channel(settings, "phone").token is None
    assert report["attention"] == [{"kind": "notifications", "id": "phone", "reason": "secret"}]


def test_import_over_existing_keeps_their_secrets(settings):
    _set_up(settings)
    data = backup.export(settings)
    data["cameras"][0]["name"] = "Front yard"
    backup.import_(settings, data)
    assert settings.camera("yard").name == "Front yard"
    assert "user:pw@" in settings.camera("yard").source.url  # the stream login stays
    assert notify.channel(settings, "phone").token == "tk_secret"


def test_newer_formats_are_refused_and_older_ones_upgraded(settings, monkeypatch):
    with pytest.raises(backup.BackupError, match="newer"):
        backup.upgrade({"config_format_version": backup.CONFIG_FORMAT_VERSION + 1})
    with pytest.raises(backup.BackupError, match="not a Foxcam"):
        backup.upgrade({"cameras": []})
    # A future format 2 that renamed a key: a format-1 file goes through the migration.
    monkeypatch.setattr(backup, "CONFIG_FORMAT_VERSION", 2)
    monkeypatch.setitem(backup.MIGRATIONS, 1,
                        lambda d: {**d, "notifications": d.pop("channels", [])})
    data = backup.upgrade({"config_format_version": 1, "channels": [{"id": "x"}]})
    assert data["config_format_version"] == 2 and data["notifications"] == [{"id": "x"}]


def test_api_export_downloads_and_import_reports(settings):
    _set_up(settings)
    with app_client(settings) as c:
        r = c.get("/api/admin/settings-export")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        assert c.post("/api/admin/settings-import", json=r.json()).status_code == 200
        assert c.post("/api/admin/settings-import",
                      json={"config_format_version": 99}).status_code == 400
    with app_client(settings, email="viewer@example.org", admin=False) as c:
        assert c.get("/api/admin/settings-export").status_code == 403
