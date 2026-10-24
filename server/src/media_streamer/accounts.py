"""Accounts, sessions, camera sharing and invites (see docs/AUTH.md).

- A **user** signs in with e-mail and password and/or an OAuth provider. Admins can do
  everything; other users see only the cameras shared with them.
- A **session** is a random cookie token; only its SHA-256 is stored.
- **Camera access** gives a user a role on one camera: viewer, editor or owner. Whoever
  adds a camera owns it; owners share it with other accounts (by e-mail) or with an
  **invite** link, which also lets someone without an account create one.
- The very first account is created on the setup page with a one-time **setup code**
  that the server writes to its log and to ``data/setup-code``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update

from .config import Settings
from .db import session_scope
from .models import CameraAccess, CameraRole, Invite, OAuthIdentity, User, UserSession, utcnow

log = logging.getLogger(__name__)

PASSWORD_MIN = 8
PASSWORD_MAX = 256
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
INVITE_PREFIX = "msi_"
# Avoid a database write on every request; last_seen only needs to be roughly right.
SESSION_TOUCH = timedelta(minutes=5)
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


class AccountError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    """Who is making a request, and what they may see."""

    user_id: int | None  # None = auth disabled (everyone is an admin)
    email: str | None = None
    name: str | None = None
    is_admin: bool = False
    # camera id -> role (admins have every role on every camera without entries here).
    cameras: dict[str, str] = field(default_factory=dict)

    def role(self, camera_id: str) -> str | None:
        return CameraRole.OWNER if self.is_admin else self.cameras.get(camera_id)

    def can(self, camera_id: str, role: str = CameraRole.VIEWER) -> bool:
        return CameraRole.at_least(self.role(camera_id), role)

    def camera_ids(self) -> set[str] | None:
        """The cameras this principal may see; None = all of them."""
        return None if self.is_admin else set(self.cameras)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AccountError("enter a valid e-mail address")
    return email


# --- passwords ------------------------------------------------------------------------

def hash_password(password: str) -> str:
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AccountError(f"passwords need at least {PASSWORD_MIN} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or len(password) > PASSWORD_MAX:
        return False
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n),
                             r=int(r), p=int(p))
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


# Compared against when the account does not exist, so a login takes as long either way.
_DUMMY_HASH = hash_password("not-a-real-password")


# --- users ----------------------------------------------------------------------------

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def user_info(u: User, providers: list[str] | None = None) -> dict[str, Any]:
    return {"id": u.id, "email": u.email, "name": u.name, "display_name": u.name or u.email,
            "is_admin": u.is_admin, "disabled": u.disabled,
            "email_verified": bool(u.email_verified), "has_password": u.password_hash is not None,
            "providers": providers if providers is not None else [],
            "created_at": _iso(u.created_at), "last_login_at": _iso(u.last_login_at)}


def user_count() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(User)) or 0


def _admin_by_config(settings: Settings, email: str) -> bool:
    """Only for verified addresses (User.email_verified): anyone can type one in."""
    return email in settings.auth.admin_emails


def create_user(settings: Settings, email: str, password: str | None = None,
                name: str | None = None, is_admin: bool = False,
                email_verified: bool = True) -> dict[str, Any]:
    """``email_verified``: False for self sign-up (nobody vouches for the address)."""
    email = normalize_email(email)
    pw_hash = hash_password(password) if password is not None else None
    with session_scope() as s:
        if s.scalar(select(User).where(User.email == email)) is not None:
            raise AccountError("an account with this e-mail already exists", 409)
        u = User(email=email, name=(name or "").strip() or None, password_hash=pw_hash,
                 email_verified=email_verified,
                 is_admin=is_admin or (email_verified and _admin_by_config(settings, email)))
        s.add(u)
        s.flush()
        return user_info(u)


def authenticate(settings: Settings, email: str, password: str) -> int:
    """The user id for a correct e-mail and password."""
    try:
        email = normalize_email(email)
    except AccountError:
        email = ""
    with session_scope() as s:
        u = s.scalar(select(User).where(User.email == email)) if email else None
        ok = verify_password(password, u.password_hash if u else _DUMMY_HASH)
        if u is None or not ok:
            raise AccountError("wrong e-mail or password", 401)
        if u.disabled:
            raise AccountError("this account is disabled", 403)
        _logged_in(settings, u)
        return u.id


def _logged_in(settings: Settings, u: User) -> None:
    u.last_login_at = utcnow()
    if u.email_verified and _admin_by_config(settings, u.email):
        u.is_admin = True


def get_user(user_id: int) -> dict[str, Any]:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AccountError("unknown user", 404)
        providers = list(s.scalars(select(OAuthIdentity.provider)
                                   .where(OAuthIdentity.user_id == user_id)))
        return user_info(u, providers)


def list_users() -> list[dict[str, Any]]:
    with session_scope() as s:
        providers: dict[int, list[str]] = {}
        for uid, provider in s.execute(select(OAuthIdentity.user_id, OAuthIdentity.provider)):
            providers.setdefault(uid, []).append(provider)
        return [user_info(u, providers.get(u.id, []))
                for u in s.scalars(select(User).order_by(User.created_at))]


def _admins_left(s, without: int) -> int:
    return s.scalar(select(func.count()).select_from(User).where(
        User.is_admin.is_(True), User.disabled.is_(False), User.id != without)) or 0


def update_user(user_id: int, *, name: str | None = None, is_admin: bool | None = None,
                disabled: bool | None = None) -> dict[str, Any]:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AccountError("unknown user", 404)
        losing_admin = u.is_admin and (is_admin is False or disabled is True)
        if losing_admin and _admins_left(s, user_id) == 0:
            raise AccountError("the last admin cannot be removed or disabled", 409)
        if name is not None:
            u.name = name.strip() or None
        if is_admin is not None:
            u.is_admin = is_admin
        if disabled is not None:
            u.disabled = disabled
            if disabled:
                s.execute(delete(UserSession).where(UserSession.user_id == user_id))
        s.flush()
    return get_user(user_id)


def delete_user(user_id: int) -> None:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AccountError("unknown user", 404)
        if u.is_admin and _admins_left(s, user_id) == 0:
            raise AccountError("the last admin cannot be removed", 409)
        # SQLite only cascades with foreign keys on; delete explicitly to be sure.
        for model in (UserSession, OAuthIdentity, CameraAccess):
            s.execute(delete(model).where(model.user_id == user_id))
        s.delete(u)


def set_password(user_id: int, password: str, keep_session: str | None = None) -> None:
    """New password; signs out every other browser of the user."""
    pw_hash = hash_password(password)
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AccountError("unknown user", 404)
        u.password_hash = pw_hash
        q = delete(UserSession).where(UserSession.user_id == user_id)
        if keep_session:
            q = q.where(UserSession.token_hash != _sha256(keep_session))
        s.execute(q)


def change_password(user_id: int, current: str | None, new: str, session_token: str) -> None:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AccountError("unknown user", 404)
        if u.password_hash is not None and not verify_password(current or "", u.password_hash):
            raise AccountError("the current password is wrong", 403)
    set_password(user_id, new, keep_session=session_token)


def unlink_provider(user_id: int, provider: str) -> None:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AccountError("unknown user", 404)
        others = s.scalar(select(func.count()).select_from(OAuthIdentity).where(
            OAuthIdentity.user_id == user_id, OAuthIdentity.provider != provider)) or 0
        if u.password_hash is None and others == 0:
            raise AccountError("set a password first, or you could not sign in any more", 409)
        s.execute(delete(OAuthIdentity).where(OAuthIdentity.user_id == user_id,
                                              OAuthIdentity.provider == provider))


# --- sessions -------------------------------------------------------------------------

def create_session(settings: Settings, user_id: int, user_agent: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    with session_scope() as s:
        s.add(UserSession(user_id=user_id, token_hash=_sha256(token), created_at=now,
                          last_seen_at=now,
                          expires_at=now + timedelta(days=settings.auth.session_days),
                          user_agent=(user_agent or "")[:200] or None))
        # Housekeeping: forget expired sessions now and then.
        s.execute(delete(UserSession).where(UserSession.expires_at < now))
    return token


def session_principal(settings: Settings, token: str) -> Principal | None:
    if not token or len(token) > 100:
        return None
    now = utcnow()
    with session_scope() as s:
        row = s.scalar(select(UserSession).where(UserSession.token_hash == _sha256(token)))
        if row is None or row.expires_at < now:
            return None
        u = s.get(User, row.user_id)
        if u is None or u.disabled:
            return None
        if now - row.last_seen_at > SESSION_TOUCH:  # sliding expiry
            row.last_seen_at = now
            row.expires_at = now + timedelta(days=settings.auth.session_days)
        return _principal(s, u)


def _principal(s, u: User) -> Principal:
    cams = {} if u.is_admin else dict(s.execute(
        select(CameraAccess.camera_id, CameraAccess.role)
        .where(CameraAccess.user_id == u.id)).all())
    return Principal(user_id=u.id, email=u.email, name=u.name, is_admin=u.is_admin,
                     cameras=cams)


def principal_for(user_id: int) -> Principal:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AccountError("unknown user", 404)
        return _principal(s, u)


def end_session(token: str) -> None:
    with session_scope() as s:
        s.execute(delete(UserSession).where(UserSession.token_hash == _sha256(token)))


# --- first account --------------------------------------------------------------------

def setup_required(settings: Settings) -> bool:
    return settings.auth.enabled and user_count() == 0


def setup_code(settings: Settings) -> str:
    """The one-time code for creating the first account (kept until it is used)."""
    path = settings.data_dir / "setup-code"
    if path.is_file() and (code := path.read_text().strip()):
        return code
    code = "-".join(secrets.token_hex(3) for _ in range(3))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(code + "\n")
    path.chmod(0o600)
    return code


def announce_setup(settings: Settings) -> None:
    if setup_required(settings):
        log.warning("no accounts yet: open the web UI and create the first admin with setup "
                    "code %s (also in %s), or run `media-streamer user add <e-mail> --admin`",
                    setup_code(settings), settings.data_dir / "setup-code")


def create_first_admin(settings: Settings, email: str, password: str, name: str | None,
                       code: str) -> int:
    if not setup_required(settings):
        raise AccountError("setup is already done; sign in instead", 409)
    if not hmac.compare_digest(code.strip().lower(), setup_code(settings)):
        raise AccountError("wrong setup code (see the server log or data/setup-code)", 403)
    info = create_user(settings, email, password, name, is_admin=True)
    (settings.data_dir / "setup-code").unlink(missing_ok=True)
    return info["id"]


# --- camera access --------------------------------------------------------------------

def _check_role(role: str) -> None:
    if role not in CameraRole.ORDER:
        raise AccountError(f"role must be one of {', '.join(CameraRole.ORDER)}")


def grant(camera_id: str, user_id: int, role: str, granted_by: int | None = None) -> None:
    _check_role(role)
    with session_scope() as s:
        if s.get(User, user_id) is None:
            raise AccountError("unknown user", 404)
        row = s.get(CameraAccess, (camera_id, user_id))
        if row is None:
            s.add(CameraAccess(camera_id=camera_id, user_id=user_id, role=role,
                               granted_by=granted_by))
        else:
            row.role = role


def share_by_email(camera_id: str, email: str, role: str, granted_by: int | None) -> int:
    email = normalize_email(email)
    with session_scope() as s:
        uid = s.scalar(select(User.id).where(User.email == email))
    if uid is None:
        raise AccountError("there is no account with this e-mail; send an invite link instead",
                           404)
    grant(camera_id, uid, role, granted_by)
    return uid


def revoke(camera_id: str, user_id: int) -> None:
    with session_scope() as s:
        s.execute(delete(CameraAccess).where(CameraAccess.camera_id == camera_id,
                                             CameraAccess.user_id == user_id))


def camera_access(camera_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(select(CameraAccess, User).join(User, User.id == CameraAccess.user_id)
                         .where(CameraAccess.camera_id == camera_id)
                         .order_by(CameraAccess.created_at)).all()
        return [{"user_id": u.id, "email": u.email, "name": u.name,
                 "display_name": u.name or u.email, "role": a.role,
                 "created_at": _iso(a.created_at)} for a, u in rows]


def forget_camera(camera_id: str) -> None:
    """A removed camera's shares and unused invites go too (a new camera could get the
    same id). Invites left with nothing to give are deleted."""
    with session_scope() as s:
        s.execute(delete(CameraAccess).where(CameraAccess.camera_id == camera_id))
        for inv in s.scalars(select(Invite).where(Invite.used_at.is_(None))):
            cams = [c for c in inv.cameras or [] if c["camera_id"] != camera_id]
            if len(cams) == len(inv.cameras or []):
                continue
            if cams or inv.is_admin:
                inv.cameras = cams
            else:
                s.delete(inv)


def directory(principal: Principal) -> list[dict[str, Any]]:
    """Accounts to suggest when sharing: everyone for admins, else people the user already
    shares a camera with."""
    with session_scope() as s:
        q = select(User).where(User.disabled.is_(False)).order_by(User.email)
        if not principal.is_admin:
            mine = select(CameraAccess.camera_id).where(CameraAccess.user_id == principal.user_id)
            q = q.where(User.id.in_(select(CameraAccess.user_id)
                                    .where(CameraAccess.camera_id.in_(mine))))
        return [{"id": u.id, "email": u.email, "display_name": u.name or u.email}
                for u in s.scalars(q) if u.id != principal.user_id]


# --- invites --------------------------------------------------------------------------

def _invite_info(inv: Invite, creator: User | None = None) -> dict[str, Any]:
    now = utcnow()
    state = "used" if inv.used_at else "expired" if inv.expires_at < now else "open"
    return {"id": inv.id, "cameras": inv.cameras or [], "is_admin": inv.is_admin,
            "note": inv.note, "created_at": _iso(inv.created_at),
            "expires_at": _iso(inv.expires_at), "used_at": _iso(inv.used_at),
            "used_by": inv.used_by, "state": state, "created_by": inv.created_by,
            "created_by_name": (creator.name or creator.email) if creator else None}


def create_invite(principal: Principal, cameras: list[dict[str, str]], is_admin: bool = False,
                  note: str | None = None, days: float = 7) -> tuple[dict[str, Any], str]:
    """Returns (invite info, token). The token is only shown once."""
    if is_admin and not principal.is_admin:
        raise AccountError("only admins can invite admins", 403)
    seen: dict[str, str] = {}
    for c in cameras:
        _check_role(c["role"])
        if not principal.can(c["camera_id"], CameraRole.OWNER):
            raise AccountError(f"you can only share cameras you own ({c['camera_id']})", 403)
        seen[c["camera_id"]] = c["role"]
    if not seen and not principal.is_admin:
        raise AccountError("pick at least one camera to share")
    token = INVITE_PREFIX + secrets.token_urlsafe(24)
    with session_scope() as s:
        inv = Invite(token_hash=_sha256(token), created_by=principal.user_id,
                     cameras=[{"camera_id": k, "role": v} for k, v in seen.items()],
                     is_admin=is_admin, note=(note or "").strip()[:200] or None,
                     expires_at=utcnow() + timedelta(days=days))
        s.add(inv)
        s.flush()
        return _invite_info(inv), token


def _open_invite(s, token: str) -> Invite:
    inv = s.scalar(select(Invite).where(Invite.token_hash == _sha256(token or "")))
    if inv is None:
        raise AccountError("this invite link is not valid", 404)
    if inv.used_at is not None:
        raise AccountError("this invite link has already been used", 410)
    if inv.expires_at < utcnow():
        raise AccountError("this invite link has expired", 410)
    return inv


def invite_preview(settings: Settings, token: str) -> dict[str, Any]:
    """What an invite gives, for the page the link opens (no login needed)."""
    with session_scope() as s:
        inv = _open_invite(s, token)
        creator = s.get(User, inv.created_by) if inv.created_by else None
        names = {c.id: c.display_name for c in settings.all_cameras(include_disabled=True)}
        return {"from": (creator.name or creator.email) if creator else None,
                "is_admin": inv.is_admin, "note": inv.note, "expires_at": _iso(inv.expires_at),
                "cameras": [{**c, "name": names.get(c["camera_id"], c["camera_id"])}
                            for c in inv.cameras or []]}


def accept_invite(token: str, user_id: int) -> list[str]:
    """Grants what the invite gives (never lowers an existing role). Returns camera ids."""
    with session_scope() as s:
        inv = _open_invite(s, token)
        u = s.get(User, user_id)
        if u is None:
            raise AccountError("unknown user", 404)
        # Claim it first, atomically: two requests at once can't both use it.
        claimed = s.execute(update(Invite).where(Invite.id == inv.id, Invite.used_at.is_(None))
                            .values(used_at=utcnow(), used_by=user_id)).rowcount
        if claimed != 1:
            raise AccountError("this invite link has already been used", 410)
        for c in inv.cameras or []:
            row = s.get(CameraAccess, (c["camera_id"], user_id))
            if row is None:
                s.add(CameraAccess(camera_id=c["camera_id"], user_id=user_id, role=c["role"],
                                   granted_by=inv.created_by))
            elif not CameraRole.at_least(row.role, c["role"]):
                row.role = c["role"]
        if inv.is_admin:
            u.is_admin = True
        return [c["camera_id"] for c in inv.cameras or []]


def list_invites(principal: Principal) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(Invite, User).outerjoin(User, User.id == Invite.created_by) \
            .order_by(Invite.created_at.desc()).limit(200)
        if not principal.is_admin:
            q = q.where(Invite.created_by == principal.user_id)
        return [_invite_info(inv, u) for inv, u in s.execute(q).all()]


def delete_invite(principal: Principal, invite_id: int) -> None:
    with session_scope() as s:
        inv = s.get(Invite, invite_id)
        if inv is None or (not principal.is_admin and inv.created_by != principal.user_id):
            raise AccountError("unknown invite", 404)
        s.delete(inv)


# --- sign-up --------------------------------------------------------------------------

def register(settings: Settings, email: str, password: str, name: str | None,
             invite: str | None) -> int:
    """Create an account with a password (registration: open, or with an invite)."""
    if not settings.auth.password_login:
        raise AccountError("sign-up with a password is turned off", 403)
    email = normalize_email(email)
    if invite:
        with session_scope() as s:
            _open_invite(s, invite)  # fail before creating the account
    elif settings.auth.registration != "open":
        # admin_emails don't skip the invite here: nothing proves the address is theirs
        # (they sign up through OAuth, or with `media-streamer user add`).
        raise AccountError("you need an invite link to create an account", 403)
    uid = create_user(settings, email, password, name, email_verified=False)["id"]
    if invite:
        accept_invite(invite, uid)
    return uid


def oauth_user(settings: Settings, provider: str, subject: str, email: str | None,
               email_verified: bool, name: str | None, invite: str | None = None) -> int:
    """The account for an external identity: linked before, matched by verified e-mail, or
    created (when registration allows it)."""
    email = normalize_email(email) if email else None
    with session_scope() as s:
        ident = s.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == provider,
                                                     OAuthIdentity.subject == subject))
        u = s.get(User, ident.user_id) if ident else None
        if u is None and email and email_verified:
            u = s.scalar(select(User).where(User.email == email))
            if u is not None and not u.email_verified:
                # Anyone could have signed up with this address and a password: never
                # hand that account to (or take it from) the address' owner by itself.
                raise AccountError("an account with this e-mail exists, but its address is "
                                   "not confirmed: sign in with its password and connect "
                                   f"{provider} on the account page, or ask an admin", 409)
            if u is not None:
                s.add(OAuthIdentity(user_id=u.id, provider=provider, subject=subject,
                                    email=email))
        if u is None:
            if not email or not email_verified:
                raise AccountError("the provider did not confirm an e-mail address", 403)
            if invite:
                _open_invite(s, invite)
            elif not (settings.auth.registration == "open"
                      or _admin_by_config(settings, email)):
                raise AccountError("there is no account for this e-mail; ask for an invite "
                                   "link", 403)
            u = User(email=email, name=(name or "").strip() or None, email_verified=True,
                     is_admin=_admin_by_config(settings, email))
            s.add(u)
            s.flush()
            s.add(OAuthIdentity(user_id=u.id, provider=provider, subject=subject, email=email))
        if u.disabled:
            raise AccountError("this account is disabled", 403)
        _logged_in(settings, u)
        uid = u.id
    if invite:
        accept_invite(invite, uid)
    return uid


def link_identity(user_id: int, provider: str, subject: str, email: str | None,
                  email_verified: bool = False) -> None:
    """Connect an external account to the signed-in user (from the account page). A
    provider confirming the account's own address verifies it."""
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is not None and email and email_verified and normalize_email(email) == u.email:
            u.email_verified = True
        ident = s.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == provider,
                                                     OAuthIdentity.subject == subject))
        if ident is not None and ident.user_id != user_id:
            raise AccountError("this external account is linked to another user", 409)
        if ident is None:
            s.execute(delete(OAuthIdentity).where(OAuthIdentity.user_id == user_id,
                                                  OAuthIdentity.provider == provider))
            s.add(OAuthIdentity(user_id=user_id, provider=provider, subject=subject,
                                email=email))
