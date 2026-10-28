"""Notification channels (ntfy, webhooks, Apprise, web push) and recent deliveries."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import notify, webpush
from ..config import PROFILE_ID, Settings
from .deps import require_admin, settings_dep

router = APIRouter(prefix="/api/notifications", tags=["notifications"],
                   dependencies=[Depends(require_admin)])


class ChannelIn(BaseModel):
    id: str = Field(pattern=PROFILE_ID, max_length=40)
    name: str | None = Field(default=None, max_length=80)
    type: Literal["ntfy", "webhook", "apprise", "webpush"]
    enabled: bool = True
    when: list[Literal["event.new", "event.clip"]] = ["event.new"]
    cameras: list[str] | None = None
    profiles: list[str] | None = None
    labels: list[str] | None = None
    min_confidence: float = Field(default=0, ge=0, le=1)
    min_agreement: int = Field(default=1, ge=1, le=10)
    quiet_hours: str | None = None
    cooldown_minutes: float = Field(default=0, ge=0)
    include_image: bool = True
    server: str = "https://ntfy.sh"
    topic: str | None = None
    # Secrets: leave empty when editing to keep the stored value.
    token: str | None = None
    username: str | None = None
    password: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    tags: list[str] = []
    open_to_users: bool = False
    url: str | None = None
    secret: str | None = None
    headers: dict[str, str] = {}


def _error(e: notify.NotifyError) -> HTTPException:
    return HTTPException(400, str(e))


def _clean(body: ChannelIn) -> dict[str, Any]:
    data = body.model_dump()
    for key in ("name", "topic", "token", "username", "password", "url", "secret",
                "quiet_hours"):
        if isinstance(data.get(key), str) and not data[key].strip():
            data[key] = None
    return data


@router.get("")
def overview(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    try:
        import apprise  # noqa: F401

        apprise_ok = True
    except ImportError:
        apprise_ok = False
    channels = []
    for c in notify.all_channels(settings, True):
        data = notify.public_channel(c)
        if c.type == "webpush":
            data["devices"] = notify.devices(c.id)
        channels.append(data)
    return {
        "channels": channels,
        "public_url": settings.public_url,
        "timezone": settings.notifications.timezone,
        "apprise": apprise_ok,
        "webpush_key": webpush.public_key(settings),
    }


@router.get("/deliveries")
def deliveries(limit: int = Query(30, le=200)) -> list[dict[str, Any]]:
    return notify.deliveries(limit)


@router.post("/channels", status_code=201)
def create_channel(body: ChannelIn, settings: Settings = Depends(settings_dep)) -> dict:
    try:
        ch = notify.save_channel(settings, _clean(body), create=True)
    except notify.NotifyError as e:
        raise _error(e) from e
    return {"id": ch.id}


@router.put("/channels/{channel_id}")
def update_channel(channel_id: str, body: ChannelIn,
                   settings: Settings = Depends(settings_dep)) -> dict:
    if body.id != channel_id:
        raise HTTPException(400, "the id of a channel cannot be changed")
    try:
        ch = notify.save_channel(settings, _clean(body), create=False)
    except notify.NotifyError as e:
        raise _error(e) from e
    return {"id": ch.id}


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: str) -> dict[str, str]:
    try:
        notify.delete_channel(channel_id)
    except notify.NotifyError as e:
        raise _error(e) from e
    return {"status": "ok"}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: str, settings: Settings = Depends(settings_dep)) -> dict:
    ch = notify.channel(settings, channel_id)
    if ch is None:
        raise HTTPException(404, "unknown channel")
    return await asyncio.to_thread(notify.test, settings, ch)

