"""Your own notifications: web push to your devices, with your own rules.

Anyone signed in turns notifications on for the device they are using and chooses which
events (Account page). Admins only manage the shared channels (api_notify.py); they don't
see or change anyone's own notifications. Each user only gets cameras they can see.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import notify, webpush
from ..accounts import Principal
from ..config import Settings
from .deps import principal, settings_dep

router = APIRouter(prefix="/api/push", tags=["notifications"])


class DeviceIn(BaseModel):
    """A browser's PushSubscription (its toJSON()), and what to call the device."""

    endpoint: str = Field(max_length=2000)
    keys: dict[str, str]
    name: str | None = Field(default=None, max_length=80)


def _owner(p: Principal) -> str:
    return notify.owner_key(p.user_id)


def _error(e: notify.NotifyError) -> HTTPException:
    return HTTPException(400, str(e))


@router.get("")
def overview(p: Principal = Depends(principal),
             settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    return {"key": webpush.public_key(settings),
            "rules": notify.personal_rules(_owner(p)).model_dump(),
            "devices": notify.devices(_owner(p))}


@router.put("/rules")
def save_rules(body: dict[str, Any], p: Principal = Depends(principal)) -> dict[str, Any]:
    try:
        return notify.save_personal_rules(_owner(p), body).model_dump()
    except notify.NotifyError as e:
        raise _error(e) from e


@router.post("/devices", status_code=201)
def add_device(body: DeviceIn, p: Principal = Depends(principal)) -> dict:
    try:
        device_id = notify.add_device(_owner(p), body.endpoint, body.keys.get("p256dh", ""),
                                      body.keys.get("auth", ""), body.name)
    except notify.NotifyError as e:
        raise _error(e) from e
    return {"id": device_id, "endpoint_id": notify.endpoint_id(body.endpoint)}


@router.delete("/devices/{device_id}")
def remove_device(device_id: int, p: Principal = Depends(principal)) -> dict[str, str]:
    try:
        notify.remove_device(_owner(p), device_id)
    except notify.NotifyError as e:
        raise _error(e) from e
    return {"status": "ok"}


@router.post("/test")
async def test(p: Principal = Depends(principal),
               settings: Settings = Depends(settings_dep)) -> dict:
    return await asyncio.to_thread(notify.test_personal, settings, _owner(p))
