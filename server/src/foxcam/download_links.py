"""Short-lived download links that work without the session cookie.

An iOS home screen app shows a downloaded file in a full-screen viewer with no way back, and
Safari (which has a proper download manager) does not share the app's sign-in. So the app
asks for a token signed for the signed-in user and a few minutes, adds it to its download
links (``?dl=``) and opens them in Safari. The token acts as that user for GETs of files
only, so the usual camera access checks still apply. One token serves every download on a
page: the app fetches it once, not one per file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time

from sqlalchemy import select

from . import mediamtx_auth
from .config import Settings
from .db import session_scope
from .models import User

KEY_FILE = "download-link-key"
TTL_SECONDS = 600
# Only files: recordings, archives, event clips and the training-set export.
PATHS = re.compile(r"^/api/(segments/\d+/download|archives/\d+/download|events/\d+/clip|"
                   r"detections/feedback-export)$")


def _key(settings: Settings) -> bytes:
    return mediamtx_auth.stored(settings.data_dir / KEY_FILE).encode()


def _sig(settings: Settings, user_id: int, exp: int) -> str:
    mac = hmac.new(_key(settings), f"download|{user_id}|{exp}".encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()[:24]).decode().rstrip("=")


def sign(settings: Settings, user_id: int, ttl: int = TTL_SECONDS) -> str:
    """The value of the ``dl`` query parameter, for any file the user may download."""
    exp = int(time.time()) + ttl
    return f"{user_id}.{exp}.{_sig(settings, user_id, exp)}"


def verify(settings: Settings, token: str, path: str) -> int | None:
    """The user the token was made for, if it is not expired and ``path`` is a file."""
    try:
        uid, exp, sig = token.split(".")
        user_id, expires = int(uid), int(exp)
    except ValueError:
        return None
    if expires < time.time() or not PATHS.match(path):
        return None
    if not hmac.compare_digest(sig, _sig(settings, user_id, expires)):
        return None
    with session_scope() as s:
        disabled = s.scalar(select(User.disabled).where(User.id == user_id))
    return None if disabled is None or disabled else user_id
