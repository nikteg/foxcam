"""Accounts (admins), camera sharing (camera owners) and invite links."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import accounts
from ..accounts import AccountError, Principal
from ..config import Settings
from ..models import CameraRole
from .deps import check_camera, principal, require_admin, settings_dep

router = APIRouter(prefix="/api", tags=["users"])

Role = Literal["viewer", "editor", "owner"]


def _error(e: AccountError) -> HTTPException:
    return HTTPException(e.status, str(e))


# --- users (admins) -------------------------------------------------------------------

class UserIn(BaseModel):
    email: str = Field(max_length=254)
    name: str | None = Field(default=None, max_length=80)
    # None = the user signs in with OAuth only (or gets a password later).
    password: str | None = Field(default=None, max_length=accounts.PASSWORD_MAX)
    is_admin: bool = False


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    is_admin: bool | None = None
    disabled: bool | None = None


class PasswordIn(BaseModel):
    password: str = Field(max_length=accounts.PASSWORD_MAX)


@router.get("/users", dependencies=[Depends(require_admin)])
def list_users() -> list[dict[str, Any]]:
    return accounts.list_users()


@router.post("/users", status_code=201, dependencies=[Depends(require_admin)])
def create_user(body: UserIn, settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    try:
        return accounts.create_user(settings, body.email, body.password, body.name,
                                    body.is_admin)
    except AccountError as e:
        raise _error(e) from e


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch,
                p: Principal = Depends(require_admin)) -> dict[str, Any]:
    if user_id == p.user_id and (body.disabled or body.is_admin is False):
        raise HTTPException(409, "you cannot disable yourself or remove your own admin role")
    try:
        return accounts.update_user(user_id, name=body.name, is_admin=body.is_admin,
                                    disabled=body.disabled)
    except AccountError as e:
        raise _error(e) from e


@router.put("/users/{user_id}/password", dependencies=[Depends(require_admin)])
def set_password(user_id: int, body: PasswordIn) -> dict[str, str]:
    """Signs the user out everywhere."""
    try:
        accounts.set_password(user_id, body.password)
    except AccountError as e:
        raise _error(e) from e
    return {"status": "ok"}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, p: Principal = Depends(require_admin)) -> dict[str, str]:
    if user_id == p.user_id:
        raise HTTPException(409, "you cannot remove your own account")
    try:
        accounts.delete_user(user_id)
    except AccountError as e:
        raise _error(e) from e
    return {"status": "ok"}


@router.get("/users/directory")
def directory(p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    """People to suggest in the share dialog."""
    return accounts.directory(p) if p.user_id is not None else []


# --- sharing one camera (owners) ------------------------------------------------------

class ShareIn(BaseModel):
    email: str = Field(max_length=254)
    role: Role = "viewer"


class RoleIn(BaseModel):
    role: Role


def _owner(p: Principal, settings: Settings, camera_id: str) -> None:
    check_camera(p, camera_id, CameraRole.OWNER)
    if settings.camera(camera_id) is None:
        raise HTTPException(404, "unknown camera")
    if p.user_id is None:
        raise HTTPException(409, "accounts are turned off (auth.enabled: false)")


@router.get("/cameras/{camera_id}/access")
def camera_access(camera_id: str, p: Principal = Depends(principal),
                  settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    _owner(p, settings, camera_id)
    invites = [i for i in accounts.list_invites(p) if i["state"] == "open"
               and any(c["camera_id"] == camera_id for c in i["cameras"])]
    return {"members": accounts.camera_access(camera_id), "invites": invites}


@router.post("/cameras/{camera_id}/access", status_code=201)
def share(camera_id: str, body: ShareIn, p: Principal = Depends(principal),
          settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Share with an existing account (by e-mail); otherwise create an invite link."""
    _owner(p, settings, camera_id)
    try:
        uid = accounts.share_by_email(camera_id, body.email, body.role, p.user_id)
    except AccountError as e:
        raise _error(e) from e
    return {"user_id": uid, "role": body.role}


@router.put("/cameras/{camera_id}/access/{user_id}")
def set_role(camera_id: str, user_id: int, body: RoleIn, p: Principal = Depends(principal),
             settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    _owner(p, settings, camera_id)
    try:
        accounts.grant(camera_id, user_id, body.role, p.user_id)
    except AccountError as e:
        raise _error(e) from e
    return {"user_id": user_id, "role": body.role}


@router.delete("/cameras/{camera_id}/access/{user_id}")
def unshare(camera_id: str, user_id: int, p: Principal = Depends(principal),
            settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    """Owners remove anyone; everyone can remove themselves (stop seeing a camera)."""
    if user_id != p.user_id:
        _owner(p, settings, camera_id)
    accounts.revoke(camera_id, user_id)
    return {"status": "ok"}


# --- invite links ---------------------------------------------------------------------

class InviteCamera(BaseModel):
    camera_id: str = Field(max_length=60)
    role: Role = "viewer"


class InviteIn(BaseModel):
    cameras: list[InviteCamera] = Field(default=[], max_length=200)
    # Admins only: the new account becomes an admin.
    is_admin: bool = False
    note: str | None = Field(default=None, max_length=200)
    days: float = Field(default=7, gt=0, le=90)


@router.get("/invites")
def list_invites(p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return accounts.list_invites(p) if p.user_id is not None else []


@router.post("/invites", status_code=201)
def create_invite(body: InviteIn, p: Principal = Depends(principal),
                  settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """The link (``path``, relative to the web UI) is only returned here, once."""
    if p.user_id is None:
        raise HTTPException(409, "accounts are turned off (auth.enabled: false)")
    for c in body.cameras:
        if settings.camera(c.camera_id) is None:
            raise HTTPException(404, f"unknown camera {c.camera_id}")
    try:
        info, token = accounts.create_invite(p, [c.model_dump() for c in body.cameras],
                                             body.is_admin, body.note, body.days)
    except AccountError as e:
        raise _error(e) from e
    return {**info, "token": token, "path": f"/invite/{token}"}


@router.delete("/invites/{invite_id}")
def delete_invite(invite_id: int, p: Principal = Depends(principal)) -> dict[str, str]:
    try:
        accounts.delete_invite(p, invite_id)
    except AccountError as e:
        raise _error(e) from e
    return {"status": "ok"}
