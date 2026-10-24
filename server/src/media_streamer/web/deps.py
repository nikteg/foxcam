from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request

from .. import accounts, worker_tokens
from ..accounts import Principal
from ..config import CameraConfig, Settings
from ..mediamtx import MediaMtx
from ..models import CameraRole
from ..worker_tokens import Identity

SESSION_COOKIE = "ms_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Everyone is an admin when auth is turned off (auth.enabled: false).
OPEN_ACCESS = Principal(user_id=None, is_admin=True)
_UNSET = object()


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def mediamtx_dep(request: Request) -> MediaMtx:
    return request.app.state.mediamtx


def require_worker(request: Request,
                   authorization: str | None = Header(default=None)) -> Identity:
    """Authenticate a worker request (see worker_tokens.py for the open/locked modes)."""
    shared = request.app.state.settings.worker_token
    bearer = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") \
        else None
    if bearer:
        if shared and secrets.compare_digest(bearer, shared):
            return Identity(name=None)
        identity = worker_tokens.verify(bearer)
        if identity:
            return identity
        raise HTTPException(401, "invalid worker token")
    if request.app.state.settings.open_worker_access:
        return Identity(name=None)  # explicitly opened for a trusted network
    raise HTTPException(401, "worker token required (create one under Worker queue → Add worker)")


# --- people ---------------------------------------------------------------------------

def current_principal(request: Request) -> Principal | None:
    """The signed-in user (from the session cookie), cached on the request."""
    settings: Settings = request.app.state.settings
    if not settings.auth.enabled:
        return OPEN_ACCESS
    cached = getattr(request.state, "principal", _UNSET)
    if cached is _UNSET:
        token = request.cookies.get(SESSION_COOKIE)
        cached = accounts.session_principal(settings, token) if token else None
        request.state.principal = cached
    return cached


def _same_origin(request: Request) -> bool:
    """Browsers send Origin with every POST/PUT/PATCH/DELETE; a foreign one is a cross-site
    request riding on the session cookie."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # not a browser, or a browser that would not send cookies cross-site
    host = urlsplit(origin).netloc.lower()
    settings: Settings = request.app.state.settings
    allowed = {request.headers.get("host", "").lower()}
    for url in (settings.public_url, settings.auth.base_url):
        if url:
            allowed.add(urlsplit(url).netloc.lower())
    return host in allowed


def authenticate(request: Request) -> None:
    """App-wide guard: every /api route needs a signed-in user, except the worker API
    (worker tokens) and /api/auth/ (login, setup, OAuth; protected routes there depend on
    `principal` themselves). New routes are therefore private by default."""
    path = request.url.path
    if not path.startswith("/api/") or path.startswith("/api/worker/"):
        return
    if request.method not in SAFE_METHODS and not _same_origin(request):
        raise HTTPException(403, "cross-site request refused")
    if path.startswith("/api/auth/"):
        return
    if current_principal(request) is None:
        raise HTTPException(401, "sign in first")


def principal(request: Request) -> Principal:
    p = current_principal(request)
    if p is None:
        raise HTTPException(401, "sign in first")
    return p


def require_admin(p: Principal = Depends(principal)) -> Principal:
    if not p.is_admin:
        raise HTTPException(403, "only admins can do this")
    return p


def check_camera(p: Principal, camera_id: str, role: str = CameraRole.VIEWER) -> None:
    """404 for cameras the user cannot see (their existence is not revealed), 403 when the
    user's role is too low."""
    if not p.can(camera_id, CameraRole.VIEWER):
        raise HTTPException(404, "unknown camera")
    if not p.can(camera_id, role):
        raise HTTPException(403, f"you need the {role} role on this camera")


def camera_for(p: Principal, settings: Settings, camera_id: str,
               role: str = CameraRole.VIEWER) -> CameraConfig:
    check_camera(p, camera_id, role)
    cam = settings.camera(camera_id)
    if cam is None:
        raise HTTPException(404, "unknown camera")
    return cam


def safe_path(base: Path, rel: str | None) -> Path:
    if not rel:
        raise HTTPException(404, "file not available")
    path = (base / rel).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise HTTPException(404, "file not found")
    return path
