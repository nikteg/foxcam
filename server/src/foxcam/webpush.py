"""Web push: notifications straight to the web app, no app or service in between.

A browser (on iOS: the home screen app, from iOS 16.4) subscribes through its push service
(Apple, Google, Mozilla) and hands us an endpoint URL and two keys. To notify it we encrypt
the message for that browser (RFC 8291, aes128gcm) and POST it to the endpoint, signed with
our own key pair (VAPID, RFC 8292) so the push service knows it comes from us. The key pair
is made once and kept in the data directory; its public half is what browsers subscribe with,
so changing it would cut off every device.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings

KEY_FILE = "webpush-vapid-key.pem"
TTL_SECONDS = 24 * 3600  # a phone that is off for a day still gets it
RECORD_SIZE = 4096
# The push services want a contact in the signature: an https URL or a mailto address.
CONTACT = "https://github.com/nikteg/foxcam"


class Gone(Exception):
    """The subscription no longer exists (the app was removed or notifications turned off)."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# --- our key pair (VAPID) --------------------------------------------------------------


def _private_key(settings: Settings) -> ec.EllipticCurvePrivateKey:
    """Made once; created with a hard link so processes starting together agree on one."""
    path: Path = settings.data_dir / KEY_FILE
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.write_bytes(pem)
        tmp.chmod(0o600)
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return key


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962,
                            serialization.PublicFormat.UncompressedPoint)


def public_key(settings: Settings) -> str:
    """What browsers subscribe with (applicationServerKey), base64url."""
    return b64url(_raw_public(_private_key(settings).public_key()))


def _vapid(settings: Settings, endpoint: str) -> str:
    """Authorization header: a short-lived ES256 token for the push service's origin."""
    key = _private_key(settings)
    parts = urlsplit(endpoint)
    head = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64url(json.dumps({"aud": f"{parts.scheme}://{parts.netloc}",
                                "exp": int(time.time()) + 3600, "sub": CONTACT}).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(),
                                         ec.ECDSA(hashes.SHA256())))
    token = f"{head}.{claims}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={public_key(settings)}"


# --- encryption (RFC 8291) -------------------------------------------------------------


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt(message: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """The request body for one browser: a header with the salt and our one-off public key,
    then the message encrypted with a key only that browser can work out."""
    ua_public = unb64url(p256dh)
    salt = salt or os.urandom(16)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(server_key.public_key())
    shared = server_key.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(unb64url(auth), shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, message + b"\x02", None)  # \x02: last record
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public + ciphertext


# --- sending ---------------------------------------------------------------------------


@dataclass
class Device:
    endpoint: str
    p256dh: str
    auth: str


def send(settings: Settings, device: Device, message: dict[str, Any], client: httpx.Client,
         *, urgent: bool = True) -> None:
    """Raises Gone when the device unsubscribed, httpx.HTTPError / RuntimeError otherwise."""
    body = encrypt(json.dumps(message).encode(), device.p256dh, device.auth)
    r = client.post(device.endpoint, content=body, headers={
        "Authorization": _vapid(settings, device.endpoint),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SECONDS),
        "Urgency": "high" if urgent else "normal",
    })
    if r.status_code in (404, 410):
        raise Gone
    if r.status_code >= 400:
        raise RuntimeError(f"push service answered {r.status_code}: {r.text.strip()[:200]}")
