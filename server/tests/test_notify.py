"""Notifications: matching, outbox delivery and retries, ntfy / webhook / Apprise senders."""

from __future__ import annotations

import base64
import email.header
import json
import sys
import types
from datetime import timedelta

import httpx
import pytest
from foxcam import notify
from foxcam.config import ChannelConfig
from foxcam.db import session_scope
from foxcam.models import (
    Detection,
    Event,
    Notification,
    NotificationStatus,
    SegmentStatus,
    utcnow,
)
from foxcam.pipeline import events
from foxcam.web.queries import save_camera_state

from .conftest import add_detection, add_job, add_segment, app_client, needs_ffmpeg


class Recorder:
    """httpx transport that records requests and answers with `status`."""

    def __init__(self, status: int = 200):
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, text="ok" if self.status < 400 else "nope")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _channel(settings, **fields) -> ChannelConfig:
    ch = ChannelConfig.model_validate(fields)
    settings.notifications.channels.append(ch)
    return ch


def _fox_event(settings, with_file=False, confidence=0.9, label="fox") -> int:
    seg = add_segment(settings, status=SegmentStatus.DONE, with_file=with_file,
                      start=utcnow().replace(microsecond=0) - timedelta(minutes=5))
    job = add_job(seg, state="done")
    add_detection(seg, job, 500, label=label, confidence=confidence)
    events.assign_events(settings)
    with session_scope() as s:
        return s.query(Event).order_by(Event.id.desc()).first().id


def _notes():
    with session_scope() as s:
        return [(n.channel_id, n.kind, n.status, n.error) for n in s.query(Notification)]


def test_new_events_are_queued_for_matching_channels(settings):
    _channel(settings, id="all", type="webhook", url="http://hook")
    _channel(settings, id="other-cam", type="webhook", url="http://hook", cameras=["nope"])
    _channel(settings, id="cats", type="webhook", url="http://hook", labels=["cat"])
    _channel(settings, id="sure", type="webhook", url="http://hook", min_confidence=0.95)
    _channel(settings, id="clips", type="webhook", url="http://hook", when=["event.clip"])
    _fox_event(settings)
    assert _notes() == [("all", "event.new", "pending", None)]
    events.assign_events(settings)  # same event again: no duplicate
    assert len(_notes()) == 1


def test_quiet_hours_and_cooldown_skip(settings):
    now = utcnow()
    start = (now - timedelta(hours=1)).strftime("%H:%M")
    end = (now + timedelta(hours=1)).strftime("%H:%M")
    settings.notifications.timezone = "UTC"
    _channel(settings, id="night", type="webhook", url="http://hook", quiet_hours=f"{start}-{end}")
    _channel(settings, id="calm", type="webhook", url="http://hook", cooldown_minutes=30)
    _fox_event(settings)
    with session_scope() as s:  # a second, separate event on the same camera
        s.add(Event(camera_id="cam", profile_id="default", label="fox", first_ts=now,
                    last_ts=now, max_confidence=0.9))
        s.flush()
        ev = s.query(Event).order_by(Event.id.desc()).first()
        notify.enqueue(s, settings, ev, "event.new")
    assert sorted(_notes()) == [
        ("calm", "event.new", "pending", None),
        ("calm", "event.new", "skipped", "cooldown"),
        ("night", "event.new", "skipped", "quiet hours"),
        ("night", "event.new", "skipped", "quiet hours"),
    ]


def test_old_events_are_skipped(settings):
    _channel(settings, id="all", type="webhook", url="http://hook")
    settings.notifications.max_age_minutes = 15
    now = utcnow()
    with session_scope() as s:
        old = Event(camera_id="cam", profile_id="default", label="fox",
                    first_ts=now - timedelta(hours=2), last_ts=now - timedelta(minutes=20),
                    max_confidence=0.9)
        s.add(old)
        s.flush()
        notify.enqueue(s, settings, old, "event.new")
    assert _notes() == [("all", "event.new", "skipped", "too old")]
    settings.notifications.max_age_minutes = 0  # no limit
    with session_scope() as s:
        notify.enqueue(s, settings, s.get(Event, old.id), "event.clip")
    assert ("all", "event.clip", "skipped", "too old") not in _notes()


def test_webhook_payload_and_signature(settings):
    settings.public_url = "http://cams.local:8000"
    _channel(settings, id="hook", type="webhook", url="http://hook.local/in", secret="s3cret",
             headers={"X-Extra": "1"})
    event_id = _fox_event(settings)
    rec = Recorder()
    assert notify.deliver_pending(settings, rec.client()) == 1
    [req] = rec.requests
    body = req.content
    assert req.headers["X-Foxcam-Signature"] == notify.signature("s3cret", body)
    assert req.headers["X-Extra"] == "1"
    data = json.loads(body)
    assert data["type"] == "event.new"
    assert data["event"]["id"] == event_id and data["event"]["label"] == "fox"
    assert data["event"]["camera_name"] == "Cam" and data["event"]["confidence"] == 0.9
    assert data["links"]["events"] == "http://cams.local:8000/events?camera=cam"
    assert data["links"]["event"] == \
        f"http://cams.local:8000/events?camera=cam&details={event_id}"
    assert _notes() == [("hook", "event.new", "sent", None)]


@needs_ffmpeg
def test_ntfy_attaches_censored_image_with_utf8_title(settings):
    settings.cameras[0].name = "Trädgården"
    save_camera_state("cam", [[0.0, 0.0, 0.5, 0.5]], private=False)
    _channel(settings, id="phone", type="ntfy", topic="foxes", token="tk_1", priority=4,
             tags=["camera"])
    _fox_event(settings, with_file=True)
    rec = Recorder()
    notify.deliver_pending(settings, rec.client())
    [req] = rec.requests
    assert req.method == "PUT" and str(req.url) == "https://ntfy.sh/foxes"
    assert req.headers["Authorization"] == "Bearer tk_1"
    assert req.content[:2] == b"\xff\xd8"  # a JPEG
    title = str(email.header.make_header(email.header.decode_header(req.headers["Title"])))
    assert title == "Fox – Trädgården"
    assert req.headers["Priority"] == "4" and req.headers["Tags"] == "camera,fox_face"


def test_ntfy_header_never_folds_long_utf8_text():
    text = "12:47:54 – 92 % sure\\nProfile: Wildlife and people\\nSeen for 2 min 28 s"
    value = notify._header(text)
    assert value.isascii() and "\n" not in value and "\r" not in value
    assert str(email.header.make_header(email.header.decode_header(value))) == text


def test_ntfy_without_image_posts_json(settings):
    settings.public_url = "https://cams.example.org"
    _channel(settings, id="phone", type="ntfy", server="https://ntfy.example.org/",
             topic="foxes", username="me", password="pw", include_image=False)
    _fox_event(settings)
    rec = Recorder()
    notify.deliver_pending(settings, rec.client())
    [req] = rec.requests
    assert req.method == "POST" and str(req.url) == "https://ntfy.example.org/"
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"me:pw").decode()
    msg = json.loads(req.content)
    assert msg["topic"] == "foxes" and msg["title"] == "Fox – Cam"
    # Tapping shows the notification; its button opens the event.
    assert "click" not in msg
    assert msg["actions"] == [{"action": "view", "label": "Open event",
                               "url": "https://cams.example.org/events?camera=cam&details=1"}]
    assert "90 % sure" in msg["message"] and "Profile: " in msg["message"]


def test_failures_are_retried_then_given_up(settings):
    _channel(settings, id="hook", type="webhook", url="http://hook.local/in")
    _fox_event(settings)
    rec = Recorder(status=500)
    assert notify.deliver_pending(settings, rec.client()) == 0
    with session_scope() as s:
        note = s.query(Notification).one()
        assert note.status == "pending" and note.attempts == 1 and "500" in note.error
        assert note.next_attempt_at > utcnow()
        note.attempts = notify.MAX_ATTEMPTS - 1
        note.next_attempt_at = utcnow()
    notify.deliver_pending(settings, rec.client())
    assert _notes()[0][2] == NotificationStatus.FAILED


def test_clip_ready_notifies_once(settings):
    _channel(settings, id="clips", type="webhook", url="http://hook", when=["event.clip"])
    event_id = _fox_event(settings)
    with session_scope() as s:
        ev = s.get(Event, event_id)
        notify.enqueue(s, settings, ev, "event.clip")
        notify.enqueue(s, settings, ev, "event.clip")  # clip rebuilt later
    assert _notes() == [("clips", "event.clip", "pending", None)]


def test_apprise_channel(settings, monkeypatch):
    sent = {}

    class FakeApprise:
        def add(self, url):
            sent["url"] = url
            return True

        def notify(self, title, body, attach=None):
            sent.update(title=title, body=body, attach=attach)
            return True

    monkeypatch.setitem(sys.modules, "apprise", types.SimpleNamespace(
        Apprise=FakeApprise, AppriseAttachment=lambda path: path))
    _channel(settings, id="tg", type="apprise", url="tgram://bot/chat", include_image=False)
    _fox_event(settings)
    assert notify.deliver_pending(settings) == 1
    assert sent["url"] == "tgram://bot/chat" and sent["title"] == "Fox – Cam"


def test_message_times_are_in_the_configured_zone(settings):
    settings.notifications.timezone = "Asia/Tokyo"  # UTC+9, no daylight saving
    ts = utcnow().replace(hour=12, minute=0, second=5, microsecond=0)
    data = {"type": "event.new", "event": {
        "label": "fox", "camera_name": "Cam", "profile_name": "Foxes", "confidence": 0.91,
        "first_ts": notify._iso(ts), "last_ts": notify._iso(ts + timedelta(seconds=75)),
        "detection_count": 4, "is_night": True, "detectors": ["a", "b"],
        "detector_names": ["Model A", "Model B"], "agreement": 2, "clip_duration_s": None}}
    _, body = notify.texts(settings, data)
    lines = body.splitlines()
    assert "21:00:05" in lines[0] and "91 % sure" in lines[0] and "night" in lines[0]
    assert lines[1:] == ["Profile: Foxes", "Seen for 1 min 15 s (4 detections)",
                         "Models: Model A, Model B (2 agree)"]


def test_channel_validation():
    with pytest.raises(ValueError, match="topic"):
        ChannelConfig.model_validate({"id": "x", "type": "ntfy"})
    with pytest.raises(ValueError, match="http"):
        ChannelConfig.model_validate({"id": "x", "type": "webhook", "url": "ftp://x"})
    with pytest.raises(ValueError):
        ChannelConfig.model_validate({"id": "x", "type": "webhook", "url": "http://x",
                                      "quiet_hours": "25:00-07:00"})


def test_notifications_api(settings, monkeypatch):
    _channel(settings, id="from-config", type="ntfy", topic="t", token="secret-token")
    with app_client(settings) as c:
        body = {"id": "phone", "type": "ntfy", "topic": "foxes", "token": "tk_abc",
                "when": ["event.new", "event.clip"], "quiet_hours": "", "name": " "}
        assert c.post("/api/notifications/channels", json=body).status_code == 201
        assert c.post("/api/notifications/channels", json=body).status_code == 400
        bad = {"id": "x", "type": "ntfy"}
        assert "topic" in c.post("/api/notifications/channels", json=bad).json()["detail"]

        listed = {ch["id"]: ch for ch in c.get("/api/notifications").json()["channels"]}
        assert listed["phone"]["has_token"] and "token" not in listed["phone"]
        assert listed["from-config"]["managed"] == "config"
        assert "secret-token" not in json.dumps(c.get("/api/settings").json())

        # Editing without re-entering the token keeps it.
        r = c.put("/api/notifications/channels/phone", json={**body, "token": "", "priority": 5})
        assert r.status_code == 200
        assert notify.channel(settings, "phone").token == "tk_abc"
        assert c.put("/api/notifications/channels/from-config",
                     json={**body, "id": "from-config"}).status_code == 400

        calls = []
        monkeypatch.setattr(notify, "send", lambda ch, *a, **k: calls.append(ch.id))
        r = c.post("/api/notifications/channels/phone/test")
        assert r.json()["ok"] and calls == ["phone"]

        assert c.delete("/api/notifications/channels/phone").status_code == 200
        assert c.delete("/api/notifications/channels/from-config").status_code == 400
        assert c.get("/api/notifications/deliveries").json() == []


def test_yaml_channel_triggers(tmp_path):
    from foxcam.config import load_settings

    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"data_dir: {tmp_path}\nnotifications:\n  channels:\n"
                   "    - {id: p, type: ntfy, topic: t, when: [event.new, event.clip]}\n")
    assert load_settings(cfg).notifications.channels[0].when == ["event.new", "event.clip"]
    cfg.write_text(cfg.read_text().replace("when:", "on:"))
    with pytest.raises(ValueError, match="use `when:`"):
        load_settings(cfg)


def test_events_marked_wrong_do_not_notify(settings):
    _channel(settings, id="hook", type="webhook", url="http://hook")
    _channel(settings, id="clips", type="webhook", url="http://hook", when=["event.clip"])
    event_id = _fox_event(settings)
    with session_scope() as s:
        s.query(Detection).filter_by(event_id=event_id).update({"feedback": False})
        notify.enqueue(s, settings, s.get(Event, event_id), "event.clip")
    rec = Recorder()
    assert notify.deliver_pending(settings, rec.client()) == 0
    assert rec.requests == []
    assert _notes() == [("hook", "event.new", "skipped", "marked wrong")]
