"""Worker management for the web UI: access tokens and the auth mode."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import worker_tokens
from ..config import Settings
from ..db import session_scope
from ..models import Worker, utcnow
from .deps import settings_dep
from .queries import WORKER_ONLINE_SECONDS

router = APIRouter(prefix="/api/workers", tags=["workers"])


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    detector: str | None = Field(default=None, max_length=40)
    tags: list[str] = Field(default=[], max_length=20)


class TagsIn(BaseModel):
    tags: list[str] = Field(max_length=20)


@router.get("/auth")
def auth_mode(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    tokens = worker_tokens.list_tokens()
    locked = bool(settings.worker_token) or bool(tokens)
    return {"mode": "locked" if locked else "open", "shared_token": bool(settings.worker_token),
            "tokens": len(tokens)}


@router.get("/tokens")
def list_tokens() -> list[dict[str, Any]]:
    return worker_tokens.list_tokens()


@router.post("/tokens", status_code=201)
def create_token(body: TokenIn) -> dict[str, Any]:
    """The plaintext token is only returned here, once."""
    try:
        info, token = worker_tokens.create(body.name, body.detector, body.tags)
    except worker_tokens.TokenError as e:
        raise HTTPException(400, str(e)) from e
    return {**info, "token": token}


@router.put("/tokens/{token_id}/tags")
def set_token_tags(token_id: int, body: TagsIn) -> dict[str, Any]:
    try:
        return worker_tokens.set_tags(token_id, body.tags)
    except worker_tokens.TokenError as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/{worker_id}")
def remove_worker(worker_id: str) -> dict[str, str]:
    """Forget an offline worker (it reappears if it connects again; revoke its token to
    keep it out)."""
    now = utcnow()
    with session_scope() as s:
        w = s.get(Worker, worker_id)
        if w is None:
            raise HTTPException(404, "unknown worker")
        if (now - w.last_seen).total_seconds() < WORKER_ONLINE_SECONDS:
            raise HTTPException(409, "worker is online; stop it first")
        s.delete(w)
    return {"status": "ok"}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int) -> dict[str, str]:
    try:
        worker_tokens.revoke(token_id)
    except worker_tokens.TokenError as e:
        raise HTTPException(404, str(e)) from e
    return {"status": "ok"}
