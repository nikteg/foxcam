"""Web push: message encryption and signatures, delivery to devices, the device API."""

from __future__ import annotations

import base64
import json
import struct

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from foxcam import notify, webpush
from foxcam.db import session_scope
from foxcam.models import CameraAccess, CameraRole, PushSubscription

from .conftest import app_client, sign_in
from .test_notify import _channel, _fox_event, _notes


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


def test_events_are_pushed_to_every_device(settings):
    ch = _channel(settings, id="app", type="webpush", when=["event.new", "event.clip"])
    phone, ipad = Browser("https://web.push.apple.com/a"), Browser("https://fcm.googleapis.com/b")
    for b, name in ((phone, "iPhone"), (ipad, "iPad")):
        sub = b.subscription()
        notify.add_device(ch, sub["endpoint"], sub["keys"]["p256dh"], sub["keys"]["auth"],
                          name, None)
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
    assert _notes() == [("app", "event.new", "sent", None)]
    assert all(d["last_sent_at"] for d in notify.devices("app"))


def test_devices_that_unsubscribed_are_forgotten(settings):
    ch = _channel(settings, id="app", type="webpush")
    sub = Browser().subscription()
    notify.add_device(ch, sub["endpoint"], sub["keys"]["p256dh"], sub["keys"]["auth"], None,
                      None)
    _fox_event(settings)
    assert notify.deliver_pending(settings, PushService(410).client()) == 0
    assert notify.devices("app") == []
    [(_, _, status, error)] = _notes()
    assert status == "pending" and "turned notifications off" in error


def test_a_channel_without_devices_says_so(settings):
    ch = _channel(settings, id="app", type="webpush")
    result = notify.test(settings, ch)
    assert not result["ok"] and "no device" in result["detail"]


def test_device_api(settings, monkeypatch):
    browser = Browser()
    with app_client(settings) as c:
        body = {"id": "app", "type": "webpush", "name": "This app"}
        assert c.post("/api/notifications/channels", json=body).status_code == 201
        overview = c.get("/api/notifications").json()
        assert len(webpush.unb64url(overview["webpush_key"])) == 65
        assert overview["channels"][0]["devices"] == []

        r = c.post("/api/push/channels/app/devices",
                   json={**browser.subscription(), "name": "iPhone"})
        assert r.status_code == 201
        assert r.json()["endpoint_id"] == notify.endpoint_id(browser.endpoint)
        # Subscribing again (new keys after a reinstall) updates the same device.
        assert c.post("/api/push/channels/app/devices",
                      json={**browser.subscription(), "name": "iPhone"}).status_code == 201
        [device] = c.get("/api/notifications").json()["channels"][0]["devices"]
        assert device["name"] == "iPhone" and device["endpoint_id"] == r.json()["endpoint_id"]
        listed = json.dumps(c.get("/api/notifications").json())
        assert browser.endpoint not in listed and browser.p256dh not in listed

        bad = {**browser.subscription(), "keys": {"p256dh": "x", "auth": "y"}}
        assert c.post("/api/push/channels/app/devices", json=bad).status_code == 400
        assert c.post("/api/push/channels/nope/devices",
                      json=browser.subscription()).status_code == 404
        ntfy = {"id": "phone", "type": "ntfy", "topic": "t"}
        assert c.post("/api/notifications/channels", json=ntfy).status_code == 201
        assert c.post("/api/push/channels/phone/devices",
                      json=browser.subscription()).status_code == 404

        assert c.delete(f"/api/push/channels/app/devices/{device['id']}"
                        ).status_code == 200
        assert c.delete(f"/api/push/channels/app/devices/{device['id']}"
                        ).status_code == 400

        # Removing the channel forgets its devices.
        c.post("/api/push/channels/app/devices", json=browser.subscription())
        assert c.delete("/api/notifications/channels/app").status_code == 200
        with session_scope() as s:
            assert s.query(PushSubscription).count() == 0


def test_b64url_round_trip():
    data = bytes(range(40))
    assert webpush.unb64url(webpush.b64url(data)) == data
    assert "=" not in webpush.b64url(data)
    assert webpush.b64url(data) == base64.urlsafe_b64encode(data).decode().rstrip("=")


def test_users_turn_on_open_channels_for_their_own_devices(settings):
    _channel(settings, id="admins", type="webpush")
    _channel(settings, id="family", type="webpush", open_to_users=True)
    mine, theirs = Browser("https://push.example.com/mine"), Browser("https://push.example.com/b")
    with app_client(settings, email="user@example.org", admin=False) as c:
        r = c.get("/api/push").json()
        assert [ch["id"] for ch in r["channels"]] == ["family"] and len(r["key"]) == 87
        assert c.post("/api/push/channels/admins/devices",
                      json=mine.subscription()).status_code == 404
        assert c.post("/api/push/channels/family/devices",
                      json={**mine.subscription(), "name": "iPhone"}).status_code == 201
        # Admin routes stay closed.
        assert c.get("/api/notifications").status_code == 403

        # Someone else's device: listed for admins, not removable (or shown) here.
        ch = notify.channel(settings, "family")
        other = theirs.subscription()
        other_id = notify.add_device(ch, other["endpoint"], other["keys"]["p256dh"],
                                     other["keys"]["auth"], "iPad", None)
        [device] = c.get("/api/push").json()["channels"][0]["devices"]
        assert device["name"] == "iPhone" and set(device) == {"id", "name", "endpoint_id"}
        assert c.delete(f"/api/push/channels/family/devices/{other_id}").status_code == 400
        assert c.delete(f"/api/push/channels/family/devices/{device['id']}").status_code == 200
        assert [d["name"] for d in notify.devices("family")] == ["iPad"]


def test_devices_only_get_cameras_their_user_can_see(settings, monkeypatch):
    ch = _channel(settings, id="family", type="webpush", open_to_users=True)
    with app_client(settings, email="admin@example.org") as c:
        viewer = sign_in(c, settings, "viewer@example.org")
        stranger = sign_in(c, settings, "stranger@example.org")
    with session_scope() as s:
        s.add(CameraAccess(user_id=viewer, camera_id="cam", role=CameraRole.VIEWER))
    browsers = {uid: Browser(f"https://push.example.com/{uid}") for uid in (viewer, stranger)}
    for uid, b in browsers.items():
        sub = b.subscription()
        notify.add_device(ch, sub["endpoint"], sub["keys"]["p256dh"], sub["keys"]["auth"],
                          None, uid)
    _fox_event(settings)
    service = PushService()
    assert notify.deliver_pending(settings, service.client()) == 1
    assert [str(r.url) for r in service.requests] == [browsers[viewer].endpoint]

    # Nobody on the channel may see the camera: nothing goes out, and that is no failure.
    with session_scope() as s:
        s.query(CameraAccess).delete()
    sent = []
    monkeypatch.setattr(webpush, "send", lambda *a, **k: sent.append(a))
    assert notify.test(settings, ch)["ok"] and sent == []
