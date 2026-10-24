"""Tapo cloud account for the web UI: sign in (with MFA), list cameras, sign out.

The resulting session is the same file `media-streamer tapo-login` writes, so the ingest
process picks it up for `tapo_cloud` camera sources. The password is never stored.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)

PENDING_TTL_SECONDS = 600
MFA_EMAIL = 2


class TapoError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _ontapo():
    try:
        import ontapo
    except ImportError:
        return None
    return ontapo


@dataclass
class _PendingLogin:
    session: Any  # ontapo.OnTapo with an open MFA process
    mfa_type: int
    created: float


_pending: dict[str, _PendingLogin] = {}


def _cleanup_pending() -> None:
    now = time.monotonic()
    for key in [k for k, p in _pending.items() if now - p.created > PENDING_TTL_SECONDS]:
        _pending.pop(key, None)


def status(settings: Settings) -> dict[str, Any]:
    available = _ontapo() is not None
    path = settings.tapo_session_file
    email = None
    if path.exists():
        try:
            email = json.loads(path.read_text()).get("email") or None
        except (OSError, ValueError):
            pass
    from .ingest.tapo_cloud import session_expired

    return {"available": available, "connected": path.exists(), "email": email,
            "expired": path.exists() and session_expired(settings)}


async def login(settings: Settings, email: str, password: str) -> dict[str, Any]:
    ontapo = _require()
    from .ingest.tapo_cloud import save_session

    try:
        session = await ontapo.OnTapo.login(email, password)
    except ontapo.MFARequired as e:
        session = e.session
        types = list(e.supported_types or [])
        mfa_type = MFA_EMAIL if MFA_EMAIL in types or not types else types[0]
        try:
            await session.send_mfa_code(mfa_type)
        except ontapo.OnTapoError as err:
            await session.aclose()
            raise TapoError(f"could not send verification code: {err}") from err
        _cleanup_pending()
        login_id = secrets.token_urlsafe(16)
        _pending[login_id] = _PendingLogin(session, mfa_type, time.monotonic())
        return {"status": "mfa_required", "login_id": login_id,
                "delivery": "email" if mfa_type == MFA_EMAIL else "app"}
    except ontapo.AuthError as e:
        raise TapoError(f"sign-in failed: {e}", 401) from e
    except ontapo.OnTapoError as e:
        raise TapoError(f"Tapo cloud error: {e}", 502) from e
    try:
        save_session(settings, session)
    finally:
        await session.aclose()
    return {"status": "connected"}


async def submit_mfa(settings: Settings, login_id: str, code: str) -> dict[str, Any]:
    ontapo = _require()
    from .ingest.tapo_cloud import save_session

    _cleanup_pending()
    pending = _pending.get(login_id)
    if pending is None:
        raise TapoError("sign-in expired, please start again", 410)
    try:
        await pending.session.submit_mfa(code.strip(), pending.mfa_type)
    except ontapo.OnTapoError as e:
        # Keep the pending login so the user can retry a mistyped code.
        raise TapoError(f"verification failed: {e}", 401) from e
    _pending.pop(login_id, None)
    try:
        save_session(settings, pending.session)
    finally:
        await pending.session.aclose()
    return {"status": "connected"}


async def devices(settings: Settings) -> list[dict[str, Any]]:
    ontapo = _require()
    from .ingest.tapo_cloud import TapoSessionExpired, call_with_refresh, load_session

    if not settings.tapo_session_file.exists():
        raise TapoError("not signed in to Tapo", 401)
    session = load_session(settings)
    try:
        cams = await call_with_refresh(settings, session, session.cameras)
    except TapoSessionExpired as e:
        raise TapoError(str(e), 401) from e
    except ontapo.OnTapoError as e:
        raise TapoError(f"could not list Tapo cameras: {e}", 502) from e
    finally:
        await session.aclose()

    added = {c.source.device_id: c.id for c in settings.all_cameras(include_disabled=True)
             if c.source.type == "tapo_cloud" and c.source.device_id}
    added_by_name = {c.source.device_name: c.id for c in settings.all_cameras(include_disabled=True)
                     if c.source.type == "tapo_cloud" and c.source.device_name}
    return [{
        "device_id": c.device_id, "name": c.name, "model": c.model,
        "camera_id": added.get(c.device_id) or added_by_name.get(c.name),
    } for c in cams]


def logout(settings: Settings) -> None:
    from .ingest.tapo_cloud import expired_marker

    settings.tapo_session_file.unlink(missing_ok=True)
    expired_marker(settings).unlink(missing_ok=True)


def _require():
    ontapo = _ontapo()
    if ontapo is None:
        raise TapoError("Tapo cloud support is not installed on the server "
                        "(uv sync --all-packages --extra tapo-cloud)", 501)
    return ontapo
