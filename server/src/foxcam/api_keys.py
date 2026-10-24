"""API keys: scripts and other programs call the API as a user, without a browser session.

A key is sent as `Authorization: Bearer msk_…` and acts as the user who created it, with
that user's rights at the time of each request; a disabled user's keys stop working.
Scopes limit it further: `read` (GET, always), `write` (anything else the user may do)
and `admin` (an admin's admin powers: users, workers, profiles, settings…; only for
admins, off unless chosen). Keys can expire. Keys can't reach /api/auth/: no creating
more keys, changing your own password or signing in with one.
Only a SHA-256 of the key is stored; the key itself is shown once, when it is created.
"""

from __future__ import annotations

import dataclasses
import hashlib
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from . import accounts
from .accounts import AccountError, Principal
from .db import session_scope
from .models import ApiKey, User, utcnow

KEY_PREFIX = "msk_"
# Avoid a database write on every request; last_used is informational.
LAST_USED_RESOLUTION = timedelta(seconds=30)
MAX_KEYS_PER_USER = 50
SCOPES = ("read", "write", "admin")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _info(row: ApiKey) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "prefix": row.prefix, "scopes": _scopes(row),
            "created_at": row.created_at, "last_used_at": row.last_used_at,
            "expires_at": row.expires_at}


def _scopes(row: ApiKey) -> list[str]:
    return [s for s in SCOPES if s in (row.scopes or ["read"])]


def create(user_id: int, name: str, scopes: list[str],
           expires_days: int | None = None) -> tuple[dict[str, Any], str]:
    """Returns (key info, the key). The key itself is never stored."""
    name = name.strip()
    if not name or len(name) > 80:
        raise AccountError("name: 1 to 80 characters")
    unknown = set(scopes) - set(SCOPES)
    if unknown:
        raise AccountError(f"unknown scope: {', '.join(sorted(unknown))}")
    scopes = [s for s in SCOPES if s in {"read", *scopes}]
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    with session_scope() as s:
        count = len(s.scalars(select(ApiKey.id).where(ApiKey.user_id == user_id)).all())
        if count >= MAX_KEYS_PER_USER:
            raise AccountError(f"at most {MAX_KEYS_PER_USER} keys per account")
        u = s.get(User, user_id)
        if "admin" in scopes and (u is None or not u.is_admin):
            raise AccountError("only admins can give a key the admin scope", 403)
        row = ApiKey(user_id=user_id, name=name, token_hash=_hash(key),
                     prefix=key[:len(KEY_PREFIX) + 6], scopes=scopes,
                     expires_at=utcnow() + timedelta(days=expires_days) if expires_days
                     else None)
        s.add(row)
        s.flush()
        return _info(row), key


def list_keys(user_id: int) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(select(ApiKey).where(ApiKey.user_id == user_id)
                         .order_by(ApiKey.created_at.desc())).all()
        return [_info(r) for r in rows]


def revoke(user_id: int, key_id: int) -> None:
    with session_scope() as s:
        row = s.get(ApiKey, key_id)
        if row is None or row.user_id != user_id:
            raise AccountError("unknown key", 404)
        s.delete(row)


def principal(key: str) -> Principal | None:
    """The key's user, as they are now; None for an unknown, expired or disabled one."""
    if not key.startswith(KEY_PREFIX) or len(key) > 100:
        return None
    now = utcnow()
    with session_scope() as s:
        row = s.scalar(select(ApiKey).where(ApiKey.token_hash == _hash(key)))
        if row is None or (row.expires_at is not None and row.expires_at < now):
            return None
        u = s.get(User, row.user_id)
        if u is None or u.disabled:
            return None
        if row.last_used_at is None or now - row.last_used_at > LAST_USED_RESOLUTION:
            row.last_used_at = now
        p = accounts._principal(s, u)
        scopes = frozenset(_scopes(row))
        return dataclasses.replace(p, api_key_id=row.id, scopes=scopes,
                                   is_admin=p.is_admin and "admin" in scopes,
                                   all_cameras=p.is_admin)
