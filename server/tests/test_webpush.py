"""Web push: message encryption and signatures, each user's own notifications and devices."""

from __future__ import annotations

import base64
import json
import struct
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from foxcam import notify, webpush
from foxcam.db import session_scope
from foxcam.models import CameraAccess, CameraRole, Event, PushDevice

from .conftest import app_client, sign_in
from .test_notify import _fox_event, _notes


class Browser:
    """What a browser does with a push: its own key pair, and decrypting what arrives."""

    def __init__(self, endpoint: str = "https://push.example.com/send/abc"):
        self.endpoint = endpoint
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"

    @property
    def p256dh(self) -> str:
        return webpush.b64url(webpush._raw_public(self.key.public_key()))

    def subscription(self) -> dict:
        return {"endpoint": self.endpoint,
                "keys": {"p256dh": self.p256dh, "auth": webpush.b64url(self.auth)}}

    def decrypt(self, body: bytes) -> dict:
        """RFC 8291 from the receiving end."""
        salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
        as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
        assert rs == 4096 and idlen == 65
        shared = self.key.exchange(
            ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
        ua_public = webpush.unb64url(self.p256dh)
        ikm = webpush._hkdf(self.auth, shared,
                            b"WebPush: info\x00" + ua_public + as_public, 32)
        cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1])


class PushService:
    """httpx transport standing in for Apple's / Google's push service."""

    def __init__(self, status: int = 201):
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, text="" if self.status < 400 else "nope")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _check_vapid(settings, header: str, endpoint: str) -> dict:
    """The Authorization header is a valid ES256 token from our key, for the push service."""
    token, key = header.removeprefix("vapid t=").split(", k=")
    assert key == webpush.public_key(settings)
    head, claims, sig = token.split(".")
    raw = webpush.unb64url(sig)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64url(key))
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        public.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:  # pragma: no cover - the assertion below explains
        raise AssertionError("VAPID signature does not verify") from None
    assert json.loads(webpush.unb64url(head)) == {"typ": "JWT", "alg": "ES256"}
    data = json.loads(webpush.unb64url(claims))
    assert data["aud"] == "https://" + endpoint.split("/")[2]
    assert data["sub"].startswith(("https://", "mailto:"))
    return data


def test_encrypted_message_opens_with_the_browsers_key():
    browser = Browser()
    body = webpush.encrypt(b'{"title": "Fox \xe2\x80\x93 Garden"}', browser.p256dh,
                           webpush.b64url(browser.auth))
    assert browser.decrypt(body) == {"title": "Fox – Garden"}
    other = webpush.encrypt(b"{}", browser.p256dh, webpush.b64url(browser.auth))
    assert body[:16] != other[:16]  # a fresh salt (and key) every time


def test_encryption_matches_the_rfc_8291_example():
    """RFC 8291 section 5: same keys and salt give the same bytes as the standard."""
    server_key = ec.derive_private_key(int.from_bytes(webpush.unb64url(
        "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = webpush.encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg", salt=webpush.unb64url("DGv6ra1nlYgDCS1FRnbzlw"),
        server_key=server_key)
    assert webpush.b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocIn"
        "mYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNW"
        "QexSgSxsj_Qulcy4a-fN")


def test_key_pair_is_made_once(settings):
    first = webpush.public_key(settings)
    assert len(webpush.unb64url(first)) == 65 and webpush.public_key(settings) == first
    assert (settings.data_dir / webpush.KEY_FILE).stat().st_mode & 0o777 == 0o600


def _device(owner: str, browser: Browser, name: str | None = None) -> int:
    sub = browser.subscription()
    return notify.add_device(owner, sub["endpoint"], sub["keys"]["p256dh"],
                             sub["keys"]["auth"], name)


def _users(settings) -> tuple[int, int, int]:
    """An admin, a viewer of camera "cam", and a user who sees no cameras."""
    with app_client(settings, email="admin@example.org") as c:
        admin = sign_in(c, settings, "admin@example.org", admin=True)
        viewer = sign_in(c, settings, "viewer@example.org")
        stranger = sign_in(c, settings, "stranger@example.org")
    with session_scope() as s:
        s.add(CameraAccess(user_id=viewer, camera_id="cam", role=CameraRole.VIEWER))
    return admin, viewer, stranger


def test_events_are_pushed_to_every_device_of_the_user(settings):
    settings.auth.enabled = False  # one implicit admin: the "open" owner
    phone, ipad = Browser("https://web.push.apple.com/a"), Browser("https://fcm.googleapis.com/b")
    _device(notify.OPEN, phone, "iPhone")
    _device(notify.OPEN, ipad, "iPad")
    notify.save_personal_rules(notify.OPEN, {"when": ["event.new", "event.clip"]})
    event_id = _fox_event(settings)
    service = PushService()
    assert notify.deliver_pending(settings, service.client()) == 1
    assert [str(r.url) for r in service.requests] == [phone.endpoint, ipad.endpoint]
    for req, browser in zip(service.requests, (phone, ipad), strict=True):
        assert req.headers["Content-Encoding"] == "aes128gcm"
        assert req.headers["Urgency"] == "high" and int(req.headers["TTL"]) > 3600
        _check_vapid(settings, req.headers["Authorization"], browser.endpoint)
        msg = browser.decrypt(req.content)
        assert msg["title"] == "Fox – Cam"
        assert msg["url"] == f"/events?camera=cam&details={event_id}"
        assert msg["tag"] == f"event-{event_id}"
    assert _notes() == [("user:open", "event.new", "sent", None)]
    assert all(d["last_sent_at"] for d in notify.devices(notify.OPEN))


def test_each_user_gets_only_their_cameras_and_their_own_rules(settings):
    admin, viewer, stranger = _users(settings)
    browsers = {uid: Browser(f"https://push.example.com/{uid}") for uid in (admin, viewer,
                                                                             stranger)}
    for uid, b in browsers.items():
        _device(str(uid), b)
    notify.save_personal_rules(str(admin), {"labels": ["cat"]})  # the admin wants cats only
    _fox_event(settings)
    service = PushService()
    assert notify.deliver_pending(settings, service.client()) == 1
    assert [str(r.url) for r in service.requests] == [browsers[viewer].endpoint]
    assert _notes() == [(f"user:{viewer}", "event.new", "sent", None)]


def test_turned_off_quiet_hours_and_time_zone_are_per_user(settings):
    _, viewer, _ = _users(settings)
    _device(str(viewer), Browser())
    notify.save_personal_rules(str(viewer), {"enabled": False})
    event_id = _fox_event(settings)
    assert _notes() == []
    # Quiet hours around now in the user's own zone: skipped, not sent.
    now = datetime.now(ZoneInfo("Pacific/Auckland"))
    quiet = f"{now - timedelta(hours=1):%H:%M}-{now + timedelta(hours=1):%H:%M}"
    notify.save_personal_rules(str(viewer), {"quiet_hours": quiet,
                                             "timezone": "Pacific/Auckland"})
    with session_scope() as s:
        notify.enqueue(s, settings, s.get(Event, event_id), "event.new")
    assert [n[2:] for n in _notes()] == [("skipped", "quiet hours")]


def test_devices_that_unsubscribed_are_forgotten(settings):
    settings.auth.enabled = False
    _device(notify.OPEN, Browser())
    _fox_event(settings)
    assert notify.deliver_pending(settings, PushService(410).client()) == 0
    assert notify.devices(notify.OPEN) == []
    [(_, _, status, error)] = _notes()
    assert status == "pending" and "turned notifications off" in error


def test_open_devices_stop_once_sign_in_is_on(settings):
    _device(notify.OPEN, Browser())  # turned on while sign-in was off
    _fox_event(settings)
    assert _notes() == []


def test_access_is_checked_again_when_sending(settings):
    _, viewer, _ = _users(settings)
    _device(str(viewer), Browser())
    _fox_event(settings)
    with session_scope() as s:
        s.query(CameraAccess).delete()
    assert notify.deliver_pending(settings, PushService().client()) == 0
    [(_, _, status, error)] = _notes()
    assert status == "failed" and "no longer allowed" in error


def test_personal_api(settings, monkeypatch):
    browser = Browser()
    with app_client(settings, email="user@example.org", admin=False) as c:
        r = c.get("/api/push").json()
        assert len(webpush.unb64url(r["key"])) == 65 and r["devices"] == []
        assert r["rules"]["enabled"] and r["rules"]["when"] == ["event.new"]

        r = c.post("/api/push/devices", json={**browser.subscription(), "name": "iPhone"})
        assert r.status_code == 201
        assert r.json()["endpoint_id"] == notify.endpoint_id(browser.endpoint)
        # Again (new keys after a reinstall): the same device.
        assert c.post("/api/push/devices", json=browser.subscription()).status_code == 201
        [device] = c.get("/api/push").json()["devices"]
        assert device["endpoint_id"] == r.json()["endpoint_id"]
        listed = json.dumps(c.get("/api/push").json())
        assert browser.endpoint not in listed and browser.p256dh not in listed
        bad = {**browser.subscription(), "keys": {"p256dh": "x", "auth": "y"}}
        assert c.post("/api/push/devices", json=bad).status_code == 400

        rules = {"when": ["event.clip"], "labels": ["fox"], "quiet_hours": "22:00-06:00",
                 "timezone": "Europe/Stockholm"}
        assert c.put("/api/push/rules", json=rules).json()["labels"] == ["fox"]
        assert c.get("/api/push").json()["rules"]["timezone"] == "Europe/Stockholm"
        assert c.put("/api/push/rules", json={"timezone": "Mars/Base"}).status_code == 400
        assert c.put("/api/push/rules", json={"when": []}).status_code == 400

        sent = []
        monkeypatch.setattr(webpush, "send", lambda *a, **k: sent.append(a))
        assert c.post("/api/push/test").json()["ok"] and len(sent) == 1

        assert c.delete(f"/api/push/devices/{device['id']}").status_code == 200
        assert c.delete(f"/api/push/devices/{device['id']}").status_code == 400
        assert c.post("/api/push/test").json() == {
            "ok": False, "detail": "no device has notifications turned on"}
        # Not the admin routes.
        assert c.get("/api/notifications").status_code == 403


def test_users_cannot_touch_each_others_devices(settings):
    _, viewer, _ = _users(settings)
    theirs = _device(str(viewer), Browser())
    with app_client(settings, email="other@example.org", admin=False) as c:
        assert c.get("/api/push").json()["devices"] == []
        assert c.delete(f"/api/push/devices/{theirs}").status_code == 400
    with session_scope() as s:
        assert s.query(PushDevice).count() == 1


def test_b64url_round_trip():
    data = bytes(range(40))
    assert webpush.unb64url(webpush.b64url(data)) == data
    assert "=" not in webpush.b64url(data)
    assert webpush.b64url(data) == base64.urlsafe_b64encode(data).decode().rstrip("=")
