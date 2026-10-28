"""Web push for everyone signed in: the channels you may turn on, and your devices on them.

Admins manage channels in api_notify.py; here anyone turns a web push channel on or off for
the device they are using (admins on every web push channel, others on the ones that are
open to users). Each device only gets events from cameras its user can see (notify.py).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import notify, webpush
from ..accounts import Principal
from ..config import ChannelConfig, Settings
from .deps import principal, settings_dep

router = APIRouter(prefix="/api/push", tags=["notifications"])


class DeviceIn(BaseModel):
    """A browser's PushSubscription (its toJSON()), and what to call the device."""

    endpoint: str = Field(max_length=2000)
    keys: dict[str, str]
    name: str | None = Field(default=None, max_length=80)


def _channel(settings: Settings, p: Principal, channel_id: str) -> ChannelConfig:
    ch = next((c for c in notify.push_channels(settings, p.is_admin) if c.id == channel_id),
              None)
    if ch is None:
        raise HTTPException(404, "unknown channel")
    return ch


@router.get("")
def overview(p: Principal = Depends(principal),
             settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    channels = []
    for ch in notify.push_channels(settings, p.is_admin):
        mine = [d for d in notify.devices(ch.id) if d["user_id"] == p.user_id]
        channels.append({"id": ch.id, "name": ch.display_name,
                         "devices": [{k: d[k] for k in ("id", "name", "endpoint_id")}
                                     for d in mine]})
    return {"key": webpush.public_key(settings), "channels": channels}


@router.post("/channels/{channel_id}/devices", status_code=201)
def add_device(channel_id: str, body: DeviceIn, p: Principal = Depends(principal),
               settings: Settings = Depends(settings_dep)) -> dict:
    ch = _channel(settings, p, channel_id)
    try:
        device_id = notify.add_device(ch, body.endpoint, body.keys.get("p256dh", ""),
                                      body.keys.get("auth", ""), body.name, p.user_id)
    except notify.NotifyError as e:
        raise HTTPException(400, str(e)) from e
    return {"id": device_id, "endpoint_id": notify.endpoint_id(body.endpoint)}


@router.delete("/channels/{channel_id}/devices/{device_id}")
def remove_device(channel_id: str, device_id: int, p: Principal = Depends(principal),
                  settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    _channel(settings, p, channel_id)
    try:
        notify.remove_device(channel_id, device_id, None if p.is_admin else p.user_id)
    except notify.NotifyError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "ok"}
