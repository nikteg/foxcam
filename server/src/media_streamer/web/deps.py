from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Header, HTTPException, Request

from .. import worker_tokens
from ..config import Settings
from ..mediamtx import MediaMtx
from ..worker_tokens import Identity


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
    if not shared and not worker_tokens.any_active():
        return Identity(name=None)  # open mode
    raise HTTPException(401, "worker token required (create one under Worker queue → Add worker)")


def safe_path(base: Path, rel: str | None) -> Path:
    if not rel:
        raise HTTPException(404, "file not available")
    path = (base / rel).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise HTTPException(404, "file not found")
    return path
