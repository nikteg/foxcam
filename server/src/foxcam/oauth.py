"""Sign in with Google, Microsoft, GitHub or any OpenID Connect provider.

Authorization-code flow with PKCE. The ID token comes straight from the provider's token
endpoint over TLS, so (as OpenID Connect allows) its claims are checked without verifying
the signature: issuer, audience, expiry and nonce.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import OAuthProviderConfig

_DISCOVERY_TTL = 3600
_discovery: dict[str, tuple[float, dict[str, Any]]] = {}

GITHUB = {"authorization_endpoint": "https://github.com/login/oauth/authorize",
          "token_endpoint": "https://github.com/login/oauth/access_token"}


class OAuthError(Exception):
    pass


@dataclass
class ExternalUser:
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


@dataclass
class Flow:
    """What the callback needs to finish a login; kept in a signed cookie meanwhile."""

    provider: str
    state: str
    nonce: str
    verifier: str
    next: str = "/"
    invite: str | None = None
    link: bool = False  # connect to the signed-in account instead of signing in


def issuer(p: OAuthProviderConfig) -> str | None:
    if p.issuer:
        return p.issuer.rstrip("/")
    return {"google": "https://accounts.google.com",
            "microsoft": "https://login.microsoftonline.com/common/v2.0"}.get(p.type)


def _scopes(p: OAuthProviderConfig) -> str:
    if p.scopes:
        return " ".join(p.scopes)
    return "read:user user:email" if p.type == "github" else "openid email profile"


async def _metadata(p: OAuthProviderConfig) -> dict[str, Any]:
    if p.type == "github":
        return GITHUB
    iss = issuer(p)
    cached = _discovery.get(iss)
    if cached and time.monotonic() - cached[0] < _DISCOVERY_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{iss}/.well-known/openid-configuration")
            r.raise_for_status()
            meta = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise OAuthError(f"{p.display_name}: could not read the provider configuration "
                         f"({e})") from e
    _discovery[iss] = (time.monotonic(), meta)
    return meta


def new_flow(provider: str, next_path: str = "/", invite: str | None = None,
             link: bool = False) -> Flow:
    return Flow(provider=provider, state=secrets.token_urlsafe(24),
                nonce=secrets.token_urlsafe(16), verifier=secrets.token_urlsafe(48),
                next=next_path, invite=invite, link=link)


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def authorize_url(p: OAuthProviderConfig, flow: Flow, redirect_uri: str) -> str:
    meta = await _metadata(p)
    params = {"response_type": "code", "client_id": p.client_id, "redirect_uri": redirect_uri,
              "scope": _scopes(p), "state": flow.state, "code_challenge_method": "S256",
              "code_challenge": _challenge(flow.verifier)}
    if p.type != "github":
        params["nonce"] = flow.nonce
    if p.type == "google":
        params["prompt"] = "select_account"
    return f"{meta['authorization_endpoint']}?{urlencode(params)}"


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise OAuthError("the provider sent an unreadable ID token") from e


def _check_claims(p: OAuthProviderConfig, claims: dict[str, Any], nonce: str) -> None:
    aud = claims.get("aud")
    if p.client_id not in (aud if isinstance(aud, list) else [aud]):
        raise OAuthError("ID token is for another application")
    if float(claims.get("exp", 0)) < time.time() - 60:
        raise OAuthError("ID token has expired")
    if claims.get("nonce") != nonce:
        raise OAuthError("ID token does not belong to this sign-in")
    iss = issuer(p)
    # Microsoft's multi-tenant endpoints issue tokens from the user's own tenant.
    multi_tenant = p.type == "microsoft" and any(
        t in (iss or "") for t in ("/common/", "/organizations/", "/consumers/"))
    if not multi_tenant and claims.get("iss", "").rstrip("/") != iss:
        raise OAuthError("ID token is from another issuer")


async def finish(p: OAuthProviderConfig, flow: Flow, code: str,
                 redirect_uri: str) -> ExternalUser:
    meta = await _metadata(p)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": p.client_id, "client_secret": p.client_secret,
            "code_verifier": flow.verifier}
    async with httpx.AsyncClient(timeout=15, headers={"Accept": "application/json"}) as c:
        try:
            r = await c.post(meta["token_endpoint"], data=data)
            tokens = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise OAuthError(f"{p.display_name}: token request failed ({e})") from e
        if r.status_code >= 400 or "error" in tokens:
            raise OAuthError(f"{p.display_name}: {tokens.get('error_description') or tokens.get('error') or r.status_code}")
        if p.type == "github":
            return await _github_user(c, tokens["access_token"])
        if "id_token" not in tokens:
            raise OAuthError(f"{p.display_name}: no ID token (is 'openid' in the scopes?)")
        claims = _jwt_claims(tokens["id_token"])
        _check_claims(p, claims, flow.nonce)
        email = claims.get("email")
        verified = claims.get("email_verified")
        if (email is None or verified is None) and meta.get("userinfo_endpoint"):
            info = (await c.get(meta["userinfo_endpoint"], headers={
                "Authorization": f"Bearer {tokens['access_token']}"})).json()
            if info.get("sub") == claims.get("sub"):
                email = email or info.get("email")
                verified = info.get("email_verified", verified)
        if p.type == "microsoft" and verified is None:
            # Microsoft omits email_verified; trust only addresses of the account's tenant.
            verified = bool(email) and claims.get("xms_edov") in (True, "1", "true")
        return ExternalUser(subject=str(claims["sub"]), email=email,
                            email_verified=verified in (True, "true"),
                            name=claims.get("name") or claims.get("preferred_username"))


async def _github_user(c: httpx.AsyncClient, access_token: str) -> ExternalUser:
    headers = {"Authorization": f"Bearer {access_token}",
               "Accept": "application/vnd.github+json"}
    try:
        user = (await c.get("https://api.github.com/user", headers=headers)).json()
        emails = (await c.get("https://api.github.com/user/emails", headers=headers)).json()
    except (httpx.HTTPError, ValueError) as e:
        raise OAuthError(f"GitHub: could not read the account ({e})") from e
    primary = next((e for e in emails if isinstance(e, dict) and e.get("primary")), None)
    return ExternalUser(subject=str(user["id"]), email=primary and primary["email"],
                        email_verified=bool(primary and primary.get("verified")),
                        name=user.get("name") or user.get("login"))


def domain_allowed(p: OAuthProviderConfig, email: str | None) -> bool:
    if not p.allowed_domains:
        return True
    domain = (email or "").rsplit("@", 1)[-1].lower()
    return domain in {d.lower().lstrip("@") for d in p.allowed_domains}
