"""Per-worker access tokens, created from the web UI.

Every worker request needs `Authorization: Bearer <token>`: a per-worker token created in
the web UI (or with `media-streamer worker-token`), or the shared `worker_token` from the
config. Until one exists no worker can connect. `open_worker_access: true` lets any client
act as a worker without a token (only for a trusted test network).

A per-worker token also fixes the worker's identity: the server uses the token's name as
`worker_id`, whatever the client sends.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from .db import session_scope
from .models import WorkerToken, utcnow
from .profiles import normalize_tags

TOKEN_PREFIX = "msw_"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
# Avoid a database write on every request; last_used is informational.
LAST_USED_RESOLUTION = timedelta(seconds=30)


class TokenError(ValueError):
    pass


@dataclass
class Identity:
    """Who is calling the worker API."""

    name: str | None  # None = shared config token or open access (client picks worker_id)
    tags: tuple[str, ...] = ()  # capability tags granted to the token


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(name: str, detector: str | None = None,
           tags: list[str] | None = None) -> tuple[dict[str, Any], str]:
    """Returns (token info, plaintext token). The plaintext is never stored."""
    name = name.strip()
    if not NAME_RE.match(name):
        raise TokenError("name: letters, digits, '.', '_' or '-' (max 63 characters)")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with session_scope() as s:
        existing = s.scalar(select(WorkerToken).where(WorkerToken.name == name))
        if existing is not None and existing.revoked_at is None:
            raise TokenError(f"a worker named '{name}' already exists")
        if existing is not None:  # re-use the name of a revoked worker
            s.delete(existing)
            s.flush()
        row = WorkerToken(name=name, token_hash=_hash(token), detector=detector,
                          tags=normalize_tags(tags) or None)
        s.add(row)
        s.flush()
        return _info(row), token


def verify(token: str) -> Identity | None:
    """Identity for a valid, unrevoked token."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    now = utcnow()
    with session_scope() as s:
        row = s.scalar(select(WorkerToken).where(WorkerToken.token_hash == _hash(token),
                                                 WorkerToken.revoked_at.is_(None)))
        if row is None:
            return None
        if row.last_used_at is None or now - row.last_used_at > LAST_USED_RESOLUTION:
            row.last_used_at = now
        return Identity(name=row.name, tags=tuple(row.tags or ()))


def set_tags(token_id: int, tags: list[str]) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(WorkerToken, token_id)
        if row is None or row.revoked_at is not None:
            raise TokenError("unknown worker token")
        row.tags = normalize_tags(tags) or None
        return _info(row)


def ensure(name: str, path, tags: list[str] | None = None) -> str:
    """A valid token for `name`, kept in the file `path` (mode 0600) for local tooling.

    Reuses the saved token while it is still valid, otherwise replaces the worker's token.
    """
    if path.exists():
        saved = path.read_text().strip()
        identity = verify(saved)
        if identity and identity.name == name:
            return saved
    with session_scope() as s:
        row = s.scalar(select(WorkerToken).where(WorkerToken.name == name,
                                                 WorkerToken.revoked_at.is_(None)))
        if row is not None:
            row.revoked_at = utcnow()
    _, token = create(name, tags=tags)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n")
    path.chmod(0o600)
    return token


def revoke(token_id: int) -> None:
    with session_scope() as s:
        row = s.get(WorkerToken, token_id)
        if row is None:
            raise TokenError("unknown worker token")
        if row.revoked_at is None:
            row.revoked_at = utcnow()


def list_tokens() -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(select(WorkerToken).where(WorkerToken.revoked_at.is_(None))
                         .order_by(WorkerToken.created_at)).all()
        return [_info(r) for r in rows]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _info(row: WorkerToken) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "detector": row.detector, "tags": row.tags or [],
            "created_at": _iso(row.created_at), "last_used_at": _iso(row.last_used_at)}
