"""Login, setup, sign-up, OAuth and the signed-in user's own account.

Everything under /api/auth/ is reachable without a session (see deps.authenticate); the
routes that need one depend on `principal` explicitly. API keys don't work here (a key
can't make more keys or change the password).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from dataclasses import asdict
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .. import accounts, api_keys, oauth
from ..accounts import AccountError, Principal
from ..config import OAuthProviderConfig, Settings
from .deps import SESSION_COOKIE, current_principal, principal, settings_dep

router = APIRouter(prefix="/api/auth", tags=["auth"])

FLOW_COOKIE = "ms_oauth"
# Known device: set at every sign-in, it exempts this browser from the per-account login
# limit (someone failing from elsewhere can't lock the owner out). Grants no access.
DEVICE_COOKIE = "ms_device"
DEVICE_DAYS = 365
FLOW_SECONDS = 600


def _error(e: AccountError) -> HTTPException:
    return HTTPException(e.status, str(e))


# --- brute-force protection -----------------------------------------------------------

class _Limiter:
    """At most `limit` failed attempts per key (client address, e-mail) in `window` s."""

    def __init__(self, limit: int = 10, window: float = 900):
        self.limit, self.window = limit, window
        self.failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        q = self.failures.setdefault(key, deque())
        while q and time.monotonic() - q[0] > self.window:
            q.popleft()
        return q

    def check(self, *keys: str) -> None:
        if any(len(self._recent(k)) >= self.limit for k in keys):
            raise HTTPException(429, "too many attempts; try again in a few minutes")

    def fail(self, *keys: str) -> None:
        for k in keys:
            self._recent(k).append(time.monotonic())
        if len(self.failures) > 10_000:  # don't grow without bound
            self.failures.clear()

    def reset(self, key: str) -> None:
        self.failures.pop(key, None)


limiter = _Limiter()
# Per account, from anywhere (guessing from many addresses); known devices are exempt.
email_limiter = _Limiter(limit=100)


def _client(request: Request) -> str:
    return f"ip:{request.client.host if request.client else '?'}"


# --- cookies --------------------------------------------------------------------------

def _secure(request: Request) -> bool:
    """Also behind a proxy whose headers aren't trusted: public_url says https."""
    settings: Settings = request.app.state.settings
    base = settings.auth.base_url or settings.public_url or ""
    return request.url.scheme == "https" or base.startswith("https://")


def _start_session(request: Request, response: Response, settings: Settings,
                   user_id: int) -> None:
    token = accounts.create_session(settings, user_id, request.headers.get("user-agent"))
    response.set_cookie(SESSION_COOKIE, token, max_age=int(settings.auth.session_days * 86400),
                        httponly=True, samesite="lax", secure=_secure(request), path="/")
    if generation := accounts.device_key_for(user_id):
        device = _sign(settings, {"k": "device", "u": user_id, "g": generation,
                                  "t": int(time.time()), "n": secrets.token_urlsafe(8)})
        response.set_cookie(DEVICE_COOKIE, device, max_age=DEVICE_DAYS * 86400,
                            httponly=True, samesite="lax", secure=_secure(request),
                            path="/api/auth/")


def _known_device(request: Request, settings: Settings, email: str) -> bool:
    """This browser signed in to this account before, since its last password change."""
    data = _unsign(settings, request.cookies.get(DEVICE_COOKIE))
    # Same key as the OAuth flow cookie: the kind keeps one from passing for the other.
    if not isinstance(data, dict) or data.get("k") != "device" \
            or time.time() - data.get("t", 0) > DEVICE_DAYS * 86400:
        return False
    key = accounts.device_key(email)
    return key is not None and data.get("u") == key[0] and data.get("g") == key[1]


def _secret(settings: Settings) -> bytes:
    """Key for signing the short-lived OAuth flow cookie (data/secret.key)."""
    path = settings.data_dir / "secret.key"
    if path.is_file() and (key := path.read_bytes().strip()):
        return key
    key = secrets.token_hex(32).encode()
    path.write_bytes(key)
    path.chmod(0o600)
    return key


def _sign(settings: Settings, data: dict[str, Any]) -> str:
    # No padding: '=' would make the cookie value need quoting.
    body = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    mac = hmac.new(_secret(settings), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def _unsign(settings: Settings, value: str | None) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    body, mac = value.rsplit(".", 1)
    good = hmac.new(_secret(settings), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, good):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return None


# --- state ----------------------------------------------------------------------------

def _me(settings: Settings, p: Principal) -> dict[str, Any] | None:
    if p.user_id is None:  # auth disabled
        return None
    return accounts.get_user(p.user_id)


def auth_state(settings: Settings, p: Principal | None) -> dict[str, Any]:
    a = settings.auth
    return {
        "enabled": a.enabled,
        "setup_required": accounts.setup_required(settings),
        "password_login": a.password_login,
        "registration": a.registration,
        "providers": [{"id": o.id, "name": o.display_name, "type": o.type} for o in a.oauth],
        "user": _me(settings, p) if p else None,
        "signed_in": p is not None,
        "is_admin": bool(p and p.is_admin),
        "can_add_cameras": bool(p and (p.is_admin or a.users_can_add_cameras)),
    }


@router.get("/state")
def state(request: Request, settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Public: how to sign in, and who is signed in (null user = nobody)."""
    return auth_state(settings, current_principal(request))


class SetupIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=accounts.PASSWORD_MAX)
    name: str | None = Field(default=None, max_length=80)
    code: str = Field(max_length=40)


@router.post("/setup")
async def setup(body: SetupIn, request: Request, response: Response,
                settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Create the first admin account (needs the setup code from the server log)."""
    limiter.check(_client(request))
    try:
        uid = await asyncio.to_thread(accounts.create_first_admin, settings, body.email,
                                      body.password, body.name, body.code)
    except AccountError as e:
        if e.status == 403:
            limiter.fail(_client(request))
        raise _error(e) from e
    _start_session(request, response, settings, uid)
    return auth_state(settings, accounts.principal_for(uid))


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=accounts.PASSWORD_MAX)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response,
                settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    if not settings.auth.password_login:
        raise HTTPException(403, "sign-in with a password is turned off")
    email = body.email.strip().lower()
    # Per address and account, so others can't lock someone out from elsewhere; per
    # account a much higher limit, against guessing from many addresses.
    keys = (_client(request), f"{_client(request)}|email:{email}")
    limiter.check(*keys)
    # A known device is neither held back by nor counted towards the per-account limit.
    known = await asyncio.to_thread(_known_device, request, settings, email)
    if not known:
        email_limiter.check(f"email:{email}")
    try:
        uid = await asyncio.to_thread(accounts.authenticate, settings, body.email,
                                      body.password)
    except AccountError as e:
        limiter.fail(*keys)
        if not known:
            email_limiter.fail(f"email:{email}")
        raise _error(e) from e
    limiter.reset(keys[1])
    _start_session(request, response, settings, uid)
    return auth_state(settings, accounts.principal_for(uid))


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=accounts.PASSWORD_MAX)
    name: str | None = Field(default=None, max_length=80)
    invite: str | None = Field(default=None, max_length=100)


@router.post("/register")
async def register(body: RegisterIn, request: Request, response: Response,
                   settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    limiter.check(_client(request))
    try:
        uid = await asyncio.to_thread(accounts.register, settings, body.email, body.password,
                                      body.name, body.invite)
    except AccountError as e:
        limiter.fail(_client(request))
        raise _error(e) from e
    _start_session(request, response, settings, uid)
    return auth_state(settings, accounts.principal_for(uid))


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        accounts.end_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


# --- invites (the page an invite link opens) -------------------------------------------

@router.get("/invite/{token}")
def invite(token: str, request: Request,
           settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    limiter.check(_client(request))
    try:
        return accounts.invite_preview(settings, token)
    except AccountError as e:
        limiter.fail(_client(request))
        raise _error(e) from e


@router.post("/invite/{token}/accept")
def accept_invite(token: str, p: Principal = Depends(principal)) -> dict[str, Any]:
    if p.user_id is None:
        raise HTTPException(409, "accounts are turned off (auth.enabled: false)")
    try:
        return {"cameras": accounts.accept_invite(token, p.user_id)}
    except AccountError as e:
        raise _error(e) from e


# --- own account ----------------------------------------------------------------------

def _user(p: Principal) -> int:
    if p.user_id is None:
        raise HTTPException(409, "accounts are turned off (auth.enabled: false)")
    return p.user_id


class MeIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)


@router.patch("/me")
def update_me(body: MeIn, p: Principal = Depends(principal)) -> dict[str, Any]:
    return accounts.update_user(_user(p), name=body.name or "")


class PasswordIn(BaseModel):
    current: str | None = Field(default=None, max_length=accounts.PASSWORD_MAX)
    new: str = Field(max_length=accounts.PASSWORD_MAX)


@router.post("/password")
async def change_password(body: PasswordIn, request: Request,
                          p: Principal = Depends(principal)) -> dict[str, str]:
    """Also signs out the user's other browsers."""
    uid = _user(p)
    key = f"user:{uid}"
    limiter.check(key)
    try:
        await asyncio.to_thread(accounts.change_password, uid, body.current, body.new,
                                request.cookies.get(SESSION_COOKIE, ""))
    except AccountError as e:
        if e.status == 403:
            limiter.fail(key)
        raise _error(e) from e
    return {"status": "ok"}


@router.delete("/providers/{provider_id}")
def unlink_provider(provider_id: str, p: Principal = Depends(principal)) -> dict[str, str]:
    try:
        accounts.unlink_provider(_user(p), provider_id)
    except AccountError as e:
        raise _error(e) from e
    return {"status": "ok"}


# --- API keys -------------------------------------------------------------------------

class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    read_only: bool = False
    expires_days: int | None = Field(default=None, ge=1, le=3650)  # None = never


@router.get("/api-keys")
def list_api_keys(p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return api_keys.list_keys(_user(p))


@router.post("/api-keys", status_code=201)
def create_api_key(body: ApiKeyIn, p: Principal = Depends(principal)) -> dict[str, Any]:
    """The key itself is in the response only, never again."""
    try:
        info, key = api_keys.create(_user(p), body.name, body.read_only, body.expires_days)
    except AccountError as e:
        raise _error(e) from e
    return {**info, "key": key}


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: int, p: Principal = Depends(principal)) -> dict[str, str]:
    try:
        api_keys.revoke(_user(p), key_id)
    except AccountError as e:
        raise _error(e) from e
    return {"status": "ok"}


# --- OAuth ----------------------------------------------------------------------------

def _provider(settings: Settings, provider_id: str) -> OAuthProviderConfig:
    p = next((o for o in settings.auth.oauth if o.id == provider_id), None)
    if p is None or not settings.auth.enabled:
        raise HTTPException(404, "unknown sign-in provider")
    return p


def _base_url(request: Request, settings: Settings) -> str:
    base = settings.auth.base_url or settings.public_url
    return (base or str(request.base_url)).rstrip("/")


def _redirect_uri(request: Request, settings: Settings, provider_id: str) -> str:
    return f"{_base_url(request, settings)}/api/auth/oauth/{provider_id}/callback"


def _local_path(path: str | None) -> str:
    """Only same-site paths after sign-in (no open redirects)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


@router.get("/oauth/{provider_id}/start")
async def oauth_start(provider_id: str, request: Request, next: str | None = None,
                      invite: str | None = None, link: bool = False,
                      settings: Settings = Depends(settings_dep)) -> RedirectResponse:
    """Redirects to the provider. `link`: connect it to the signed-in account."""
    prov = _provider(settings, provider_id)
    if link and current_principal(request) is None:
        raise HTTPException(401, "sign in first")
    flow = oauth.new_flow(prov.id, _local_path(next), invite, link)
    try:
        url = await oauth.authorize_url(prov, flow, _redirect_uri(request, settings, prov.id))
    except oauth.OAuthError as e:
        raise HTTPException(502, str(e)) from e
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(FLOW_COOKIE, _sign(settings, {**asdict(flow), "t": time.time()}),
                    max_age=FLOW_SECONDS, httponly=True, samesite="lax",
                    secure=_secure(request), path="/api/auth/oauth/")
    return resp


def _back_to_login(message: str, flow: oauth.Flow | None = None) -> RedirectResponse:
    target = "/account" if flow and flow.link else "/login"
    if flow and flow.invite and not flow.link:
        target = f"/invite/{quote(flow.invite)}"
    resp = RedirectResponse(f"{target}?error={quote(message)}", status_code=303)
    resp.delete_cookie(FLOW_COOKIE, path="/api/auth/oauth/")
    return resp


@router.get("/oauth/{provider_id}/callback")
async def oauth_callback(provider_id: str, request: Request, code: str | None = None,
                         state: str | None = None, error: str | None = None,
                         error_description: str | None = None,
                         settings: Settings = Depends(settings_dep)) -> RedirectResponse:
    prov = _provider(settings, provider_id)
    data = _unsign(settings, request.cookies.get(FLOW_COOKIE))
    if not data or time.time() - data.pop("t", 0) > FLOW_SECONDS:
        return _back_to_login("The sign-in took too long or was started elsewhere; try again.")
    flow = oauth.Flow(**data)
    if error:
        return _back_to_login(error_description or error, flow)
    if flow.provider != prov.id or not code or not state \
            or not secrets.compare_digest(state, flow.state):
        return _back_to_login("The sign-in could not be verified; try again.", flow)
    try:
        ext = await oauth.finish(prov, flow, code, _redirect_uri(request, settings, prov.id))
    except oauth.OAuthError as e:
        return _back_to_login(str(e), flow)
    if not oauth.domain_allowed(prov, ext.email):
        return _back_to_login(f"{prov.display_name} accounts from this domain may not sign "
                              "in here.", flow)
    if flow.link:
        p = current_principal(request)
        if p is None or p.user_id is None:
            return _back_to_login("Sign in first.", flow)
        try:
            await asyncio.to_thread(accounts.link_identity, p.user_id, prov.id, ext.subject,
                                    ext.email, ext.email_verified)
        except AccountError as e:
            return _back_to_login(str(e), flow)
        resp = RedirectResponse("/account", status_code=303)
        resp.delete_cookie(FLOW_COOKIE, path="/api/auth/oauth/")
        return resp
    try:
        uid = await asyncio.to_thread(accounts.oauth_user, settings, prov.id, ext.subject,
                                      ext.email, ext.email_verified, ext.name, flow.invite)
    except AccountError as e:
        return _back_to_login(str(e), flow)
    resp = RedirectResponse(flow.next, status_code=303)
    resp.delete_cookie(FLOW_COOKIE, path="/api/auth/oauth/")
    _start_session(request, resp, settings, uid)
    return resp
