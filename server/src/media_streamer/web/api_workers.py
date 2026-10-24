"""Worker management for the web UI: access tokens and the auth mode."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import worker_tokens
from ..config import Settings
from .deps import settings_dep

router = APIRouter(prefix="/api/workers", tags=["workers"])


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    detector: str | None = Field(default=None, max_length=40)


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
        info, token = worker_tokens.create(body.name, body.detector)
    except worker_tokens.TokenError as e:
        raise HTTPException(400, str(e)) from e
    return {**info, "token": token}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int) -> dict[str, str]:
    try:
        worker_tokens.revoke(token_id)
    except worker_tokens.TokenError as e:
        raise HTTPException(404, str(e)) from e
    return {"status": "ok"}
