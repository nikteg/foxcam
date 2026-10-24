"""Tunables changed by admins in the web UI (Settings page): stored in the database, win
over config.yaml, applied without a restart in every process."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from media_streamer import mediamtx, runtime_settings
from media_streamer.config import Settings
from media_streamer.db import session_scope
from media_streamer.models import Segment, utcnow
from media_streamer.pipeline import retention

from .conftest import add_segment, app_client, needs_ffmpeg


def _by_key(body):
    return {s["key"]: s for s in body["settings"]}


def test_ui_values_win_over_config_yaml_until_reset(settings):
    with app_client(settings) as c:
        rows = _by_key(c.get("/api/settings/runtime").json())
        assert rows["processing.sample_fps"]["source"] == "default"
        assert rows["segment_seconds"] == {"key": "segment_seconds", "value": 2,
                                           "config_value": 2, "source": "config",
                                           "config_source": "config",
                                           "nullable": False, "ge": 1, "le": 3600}
        assert rows["retention.max_disk_gb"]["nullable"] is True

        r = c.put("/api/settings/runtime",
                  json={"values": {"processing.sample_fps": 1, "processing.max_width": 640,
                                   "retention.max_disk_gb": 50, "public_url": "http://box:8000/"}})
        assert r.status_code == 200, r.text
        rows = _by_key(r.json())
        assert rows["processing.sample_fps"]["value"] == 1
        assert rows["processing.sample_fps"]["source"] == "ui"
        # Applied to the server's own settings, which everything reads.
        assert settings.processing.max_width == 640
        assert settings.retention.max_disk_gb == 50
        assert settings.public_url == "http://box:8000"

        r = c.put("/api/settings/runtime", json={"reset": ["processing.max_width"]})
        assert _by_key(r.json())["processing.max_width"]["source"] == "default"
        assert settings.processing.max_width == 1280


def test_values_are_checked_with_the_config_bounds(settings):
    with app_client(settings) as c:
        for values in ({"processing.sample_fps": 0}, {"processing.max_width": 50},
                       {"segment_seconds": 7200}, {"retention.segments_days": -1},
                       {"retention.max_disk_gb": 0}, {"processing.sample_fps": None},
                       {"public_url": "box:8000"}, {"auth.enabled": False},
                       {"storage.s3.secret_access_key": "x"}):
            r = c.put("/api/settings/runtime", json={"values": values})
            assert r.status_code == 422, values
        assert c.put("/api/settings/runtime", json={"reset": ["worker_token"]}).status_code == 422
        # A null cap is allowed: no disk cap.
        r = c.put("/api/settings/runtime", json={"values": {"retention.max_disk_gb": None}})
        assert r.status_code == 200
    assert settings.processing.sample_fps == 2.0


def test_admins_only(settings):
    with app_client(settings, email="viewer@example.org", admin=False) as c:
        assert c.get("/api/settings/runtime").status_code == 403
        assert c.put("/api/settings/runtime",
                     json={"values": {"processing.sample_fps": 1}}).status_code == 403


def test_other_processes_pick_changes_up(settings):
    # ingest and compaction have their own Settings, read from the same config.yaml.
    other = Settings.model_validate(settings.model_dump(exclude_unset=True))
    runtime_settings.refresh(other, force=True)
    runtime_settings.update(settings, {"events.merge_gap_seconds": 120,
                                       "compaction.enabled": False}, [])
    assert other.events.merge_gap_seconds == 5
    assert runtime_settings.refresh(other, force=True) == {"events.merge_gap_seconds",
                                                           "compaction.enabled"}
    assert other.events.merge_gap_seconds == 120 and other.compaction.enabled is False
    # Reset: config.yaml's value comes back, not the built-in default.
    runtime_settings.update(settings, {}, ["events.merge_gap_seconds"])
    runtime_settings.refresh(other, force=True)
    assert other.events.merge_gap_seconds == 5


def test_segment_length_goes_to_mediamtx(settings):
    runtime_settings.update(settings, {"segment_seconds": 300}, [])
    conf = mediamtx.desired_paths(settings)["cam"]
    assert conf["recordSegmentDuration"] == "300s"
    assert settings.segment_seconds_for("cam") == 300


@needs_ffmpeg
def test_retention_uses_the_ui_values(settings):
    settings.retention.segments_days = 30
    old = add_segment(settings, start=utcnow().replace(microsecond=0) - timedelta(days=8))
    runtime_settings.update(settings, {"retention.segments_days": 7}, [])
    assert retention.run_retention(settings)["segments_age"] == 1
    with session_scope() as s:
        assert s.get(Segment, old).deleted_at is not None


def test_cli(settings, capsys):
    from media_streamer.cli import _settings_command

    def run(cmd, **kw):
        return _settings_command(settings, SimpleNamespace(settings_cmd=cmd, **kw))

    assert run("set", key="processing.sample_fps", value="0.5") == 0
    assert settings.processing.sample_fps == 0.5
    assert run("set", key="public_url", value="null") == 0
    assert run("set", key="processing.sample_fps", value="100") == 1
    assert run("reset", keys=[]) == 0
    assert runtime_settings.stored() == {}
    assert "processing.sample_fps" in capsys.readouterr().out
