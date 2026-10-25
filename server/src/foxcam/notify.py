"""Event notifications: webhooks, ntfy, and ~100 more services through Apprise.

Channels come from ``config.yaml`` (``notifications.channels``, read-only in the UI) or are
added in the web UI. When an event starts (``event.new``) or its clip is ready
(``event.clip``), every matching channel gets a row in the ``notification`` outbox; the
background loop sends it and retries with backoff when the service is unreachable.
See docs/NOTIFICATIONS.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, time, timedelta
from email.header import Header
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import ffmpeg
from .config import ChannelConfig, Settings
from .db import db_ready, session_scope
from .models import (
    Detection,
    Event,
    MaskVersion,
    Notification,
    NotificationChannel,
    NotificationStatus,
    Segment,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 6  # ~30 s, 1, 2, 4, 8 min between tries
TIMEOUT = 20
SECRET_FIELDS = ("token", "password", "secret")
# ntfy turns tags that are emoji short codes into emojis.
LABEL_EMOJI = {"fox": "fox_face", "red fox": "fox_face", "cat": "cat", "dog": "dog",
               "person": "bust_in_silhouette", "car": "car", "truck": "truck",
               "bicycle": "bike", "bird": "bird", "deer": "deer", "badger": "badger",
               "hedgehog": "hedgehog", "rabbit": "rabbit"}


class NotifyError(RuntimeError):
    pass


# --- channels ------------------------------------------------------------------------


def ui_channels() -> list[ChannelConfig]:
    if not db_ready():
        return []
    with session_scope() as s:
        rows = s.scalars(select(NotificationChannel)
                         .order_by(NotificationChannel.created_at)).all()
        return [ChannelConfig.model_validate({**r.config, "id": r.id, "managed": "ui"})
                for r in rows]


def all_channels(settings: Settings, include_disabled: bool = False) -> list[ChannelConfig]:
    channels = [*settings.notifications.channels, *ui_channels()]
    return channels if include_disabled else [c for c in channels if c.enabled]


def channel(settings: Settings, channel_id: str) -> ChannelConfig | None:
    return next((c for c in all_channels(settings, True) if c.id == channel_id), None)


def save_channel(settings: Settings, data: dict[str, Any], *, create: bool) -> ChannelConfig:
    existing = channel(settings, data.get("id", ""))
    if existing is not None and existing.managed == "config":
        raise NotifyError("channels from config.yaml can only be changed in config.yaml")
    if create and existing is not None:
        raise NotifyError(f"a channel with id '{data['id']}' already exists")
    if not create and existing is None:
        raise NotifyError("unknown channel")
    if existing is not None:  # empty secret fields keep the stored value
        for key in SECRET_FIELDS:
            if not data.get(key):
                data[key] = getattr(existing, key)
        if existing.type == "apprise" and not data.get("url"):
            data["url"] = existing.url
    try:
        ch = ChannelConfig.model_validate({**data, "managed": "ui"})
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise NotifyError(f"{loc + ': ' if loc else ''}{first['msg']}") from e
    except ValueError as e:
        raise NotifyError(str(e)) from e
    stored = ch.model_dump(exclude={"id", "managed"})
    with session_scope() as s:
        row = s.get(NotificationChannel, ch.id)
        if row is None:
            s.add(NotificationChannel(id=ch.id, config=stored))
        else:
            row.config = stored
    return ch


def delete_channel(channel_id: str) -> None:
    with session_scope() as s:
        row = s.get(NotificationChannel, channel_id)
        if row is None:
            raise NotifyError("only channels added in the web UI can be removed here")
        s.delete(row)
        s.execute(delete(Notification).where(Notification.channel_id == channel_id,
                                             Notification.status == NotificationStatus.PENDING))


def public_channel(ch: ChannelConfig) -> dict[str, Any]:
    """Channel as shown in the UI: secrets are never sent back, only whether they are set."""
    data = ch.model_dump()
    for key in SECRET_FIELDS:
        data[f"has_{key}"] = bool(data.pop(key))
    if ch.type == "apprise" and ch.url:  # Apprise URLs embed tokens: show the scheme only
        data["url"] = ch.url.split("://", 1)[0] + "://…"
    data["display_name"] = ch.display_name
    return data


# --- enqueueing ----------------------------------------------------------------------


def _in_quiet_hours(settings: Settings, quiet: str, now: datetime) -> bool:
    tz = ZoneInfo(settings.notifications.timezone) if settings.notifications.timezone else None
    local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).time()
    start, end = (time.fromisoformat(t) for t in quiet.split("-"))
    return start <= local < end if start < end else local >= start or local < end


def _matches(ch: ChannelConfig, event: Event, kind: str) -> bool:
    if kind not in ch.when:
        return False
    if ch.cameras is not None and event.camera_id not in ch.cameras:
        return False
    if ch.profiles is not None and event.profile_id not in ch.profiles:
        return False
    if ch.labels is not None and event.label.lower() not in {x.lower() for x in ch.labels}:
        return False
    if event.confirmed is False:
        return False  # waiting for the profile's confirming detector (cascade)
    if (event.agreement or 1) < ch.min_agreement:
        return False  # checked again when another model's detections join the event
    return event.max_confidence >= ch.min_confidence


def marked_wrong(s: Session, event_id: int) -> bool:
    """One of the event's detections is marked wrong and none correct: no match, so no
    notification (like the Status page's event list)."""
    marks = set(s.scalars(select(Detection.feedback).where(
        Detection.event_id == event_id, Detection.feedback.is_not(None))))
    return False in marks and True not in marks


def enqueue(s: Session, settings: Settings, event: Event, kind: str) -> int:
    """Queue `kind` for every matching channel (once per channel and event)."""
    now = utcnow()
    queued = 0
    if event.quiet or (event.id is not None and marked_wrong(s, event.id)):
        return 0
    for ch in all_channels(settings):
        if not _matches(ch, event, kind):
            continue
        exists = s.scalar(select(Notification.id).where(
            Notification.channel_id == ch.id, Notification.event_id == event.id,
            Notification.kind == kind))
        if exists:
            continue
        status, reason = NotificationStatus.PENDING, None
        if ch.quiet_hours and _in_quiet_hours(settings, ch.quiet_hours, now):
            status, reason = NotificationStatus.SKIPPED, "quiet hours"
        elif ch.cooldown_minutes and kind == "event.new" and s.scalar(
                select(func.count()).select_from(Notification).where(
                    Notification.channel_id == ch.id, Notification.camera_id == event.camera_id,
                    Notification.kind == "event.new",
                    Notification.status.in_([NotificationStatus.PENDING,
                                              NotificationStatus.SENT]),
                    Notification.created_at >= now - timedelta(minutes=ch.cooldown_minutes))):
            status, reason = NotificationStatus.SKIPPED, "cooldown"
        s.add(Notification(channel_id=ch.id, event_id=event.id, camera_id=event.camera_id,
                           kind=kind, status=status, error=reason))
        queued += status == NotificationStatus.PENDING
    return queued


# --- message content -----------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _local_hm(settings: Settings, dt: datetime) -> str:
    tz = ZoneInfo(settings.notifications.timezone) if settings.notifications.timezone else None
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%H:%M")


def payload(settings: Settings, event_id: int, kind: str) -> dict[str, Any]:
    """The JSON document webhooks receive (also the source for ntfy / Apprise texts)."""
    with session_scope() as s:
        ev = s.get(Event, event_id)
        if ev is None:
            raise NotifyError("event no longer exists")
        cam = settings.camera(ev.camera_id)
        profile = settings.profile(ev.profile_id)
        base = settings.public_url
        data = {
            "type": kind,
            "sent_at": _iso(utcnow()),
            "event": {
                "id": ev.id, "camera_id": ev.camera_id,
                "camera_name": cam.display_name if cam else ev.camera_id,
                "profile_id": ev.profile_id,
                "profile_name": profile.display_name if profile else ev.profile_id,
                "label": ev.label, "confidence": round(ev.max_confidence, 3),
                "first_ts": _iso(ev.first_ts), "last_ts": _iso(ev.last_ts),
                "detection_count": ev.detection_count, "is_night": ev.is_night,
                "detectors": ev.detectors or [], "agreement": ev.agreement or 1,
                "confirmed": ev.confirmed is not False,
                "clip_ready": bool(ev.clip_path and not ev.clip_dirty),
                "clip_duration_s": ev.clip_duration_s,
            },
            # Links into the (private) web UI; null without public_url in the config.
            "links": {
                "events": f"{base}/events?camera={ev.camera_id}" if base else None,
                "thumbnail": f"{base}/api/events/{ev.id}/thumb" if base and ev.thumb_path
                else None,
                "clip": f"{base}/api/events/{ev.id}/clip" if base and ev.clip_path else None,
            },
        }
    return data


def texts(settings: Settings, data: dict[str, Any]) -> tuple[str, str]:
    ev = data["event"]
    title = f"{ev['label'].capitalize()} – {ev['camera_name']}"
    when = _local_hm(settings, datetime.fromisoformat(ev["first_ts"].rstrip("Z")))
    parts = [f"{round(ev['confidence'] * 100)}%", when, ev["profile_name"]]
    if ev["is_night"]:
        parts.append("night")
    body = " – ".join(str(p) for p in parts if p)
    if data["type"] == "event.clip" and ev["clip_duration_s"]:
        body += f"\nClip ready ({round(ev['clip_duration_s'])} s)"
    if data["type"] == "test":
        title = f"Test: {title}"
    return title, body


def event_image(settings: Settings, event_id: int) -> Path | None:
    """Frame of the event's best detection with its box, censor areas blacked out."""
    from .pipeline.compaction import segment_source  # deferred: pipeline imports notify

    with session_scope() as s:
        ev = s.get(Event, event_id)
        det = s.get(Detection, ev.best_detection_id) if ev and ev.best_detection_id else None
        if det is None:
            return None
        seg = s.get(Segment, det.segment_id)
        footage = segment_source(settings, seg) if seg is not None else None
        if footage is None:
            return None
        mask = s.get(MaskVersion, seg.mask_version_id) if seg.mask_version_id else None
        src, offset, box = footage.path, footage.offset_s + det.offset_ms / 1000, det.bbox
        rects = mask.rects if mask else []
        det_id = det.id
    dst = settings.thumbs_dir / "notify" / f"event-{event_id}-{det_id}.jpg"
    if not dst.exists():
        try:
            ffmpeg.thumbnail(src, dst, offset, 960, box, masks=rects)
        except Exception as e:  # noqa: BLE001 - send without image rather than not at all
            log.warning("notification image for event %s failed: %s", event_id, e)
            return None
    return dst


# --- senders -------------------------------------------------------------------------


def _header(value: str) -> str:
    """HTTP headers are ASCII; ntfy accepts RFC 2047 for anything else (å, ä, ö, emoji)."""
    return value if value.isascii() else Header(value, "utf-8").encode()


def send_ntfy(ch: ChannelConfig, settings: Settings, data: dict[str, Any],
              image: Path | None, client: httpx.Client) -> None:
    title, body = texts(settings, data)
    label = data["event"]["label"].lower()
    tags = [*ch.tags, *([LABEL_EMOJI[label]] if label in LABEL_EMOJI else [])]
    links = data["links"]
    auth = None
    headers: dict[str, str] = {}
    if ch.token:
        headers["Authorization"] = f"Bearer {ch.token}"
    elif ch.username:
        auth = (ch.username, ch.password or "")
    url = f"{ch.server.rstrip('/')}/{ch.topic}"
    if image is not None:
        # Upload the image as an attachment; the text goes in headers.
        headers.update({"Title": _header(title), "Message": _header(body.replace("\n", "\\n")),
                        "Priority": str(ch.priority), "Filename": image.name})
        if tags:
            headers["Tags"] = _header(",".join(tags))
        if links["events"]:
            headers["Click"] = links["events"]
        if links["clip"]:
            headers["Actions"] = f"view, Open clip, {links['clip']}"
        r = client.put(url, content=image.read_bytes(), headers=headers, auth=auth)
    else:
        message = {"topic": ch.topic, "title": title, "message": body, "tags": tags,
                   "priority": ch.priority}
        if links["events"]:
            message["click"] = links["events"]
        if links["clip"]:
            message["actions"] = [{"action": "view", "label": "Open clip", "url": links["clip"]}]
        r = client.post(ch.server.rstrip("/") + "/", json=message, headers=headers, auth=auth)
    if r.status_code >= 400:
        raise NotifyError(f"ntfy answered {r.status_code}: {r.text.strip()[:200]}")


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def send_webhook(ch: ChannelConfig, data: dict[str, Any], client: httpx.Client) -> None:
    body = json.dumps(data).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "foxcam",
               **ch.headers}
    if ch.secret:
        headers["X-Foxcam-Signature"] = signature(ch.secret, body)
    r = client.post(ch.url, content=body, headers=headers)
    if r.status_code >= 400:
        raise NotifyError(f"webhook answered {r.status_code}: {r.text.strip()[:200]}")


def send_apprise(ch: ChannelConfig, settings: Settings, data: dict[str, Any],
                 image: Path | None) -> None:
    try:
        import apprise
    except ImportError as e:
        raise NotifyError("Apprise is not installed: uv sync --all-packages --extra notify"
                          ) from e
    title, body = texts(settings, data)
    if data["links"]["events"]:
        body += f"\n{data['links']['events']}"
    app = apprise.Apprise()
    if not app.add(ch.url):
        raise NotifyError("Apprise does not understand this URL")
    attach = apprise.AppriseAttachment(str(image)) if image else None
    if not app.notify(title=title, body=body, attach=attach):
        raise NotifyError("Apprise could not deliver the notification (see server log)")


def send(ch: ChannelConfig, settings: Settings, data: dict[str, Any], image: Path | None,
         client: httpx.Client | None = None) -> None:
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        if ch.type == "ntfy":
            send_ntfy(ch, settings, data, image if ch.include_image else None, client)
        elif ch.type == "webhook":
            send_webhook(ch, data, client)
        else:
            send_apprise(ch, settings, data, image if ch.include_image else None)
    except httpx.HTTPError as e:
        raise NotifyError(f"{type(e).__name__}: {e}") from e
    finally:
        if own:
            client.close()


# --- delivery loop -------------------------------------------------------------------


def deliver_pending(settings: Settings, client: httpx.Client | None = None,
                    limit: int = 20) -> int:
    """Send due notifications. Returns how many were delivered."""
    now = utcnow()
    with session_scope() as s:
        due = s.execute(select(Notification.id, Notification.channel_id,
                               Notification.event_id, Notification.kind)
                        .where(Notification.status == NotificationStatus.PENDING,
                               Notification.next_attempt_at <= now)
                        .order_by(Notification.id).limit(limit)).all()
    sent = 0
    for note_id, channel_id, event_id, kind in due:
        ch = channel(settings, channel_id)
        error = None
        with session_scope() as s:
            # Marked wrong while it waited: not sent.
            if event_id is not None and marked_wrong(s, event_id):
                note = s.get(Notification, note_id)
                note.status, note.error = NotificationStatus.SKIPPED, "marked wrong"
                continue
        if ch is None or not ch.enabled:
            error = "channel removed or disabled"
        else:
            try:
                data = payload(settings, event_id, kind)
                image = event_image(settings, event_id) if ch.include_image and \
                    ch.type != "webhook" else None
                send(ch, settings, data, image, client)
            except NotifyError as e:
                error = str(e)
        with session_scope() as s:
            note = s.get(Notification, note_id)
            if note is None:
                continue
            note.attempts += 1
            if error is None:
                note.status, note.sent_at, note.error = NotificationStatus.SENT, utcnow(), None
                sent += 1
            elif ch is None or not ch.enabled or note.attempts >= MAX_ATTEMPTS \
                    or error == "event no longer exists":
                note.status, note.error = NotificationStatus.FAILED, error[:500]
            else:
                note.error = error[:500]
                note.next_attempt_at = utcnow() + timedelta(seconds=30 * 2 ** (note.attempts - 1))
        if error:
            log.warning("notification %s via %s failed: %s", note_id, channel_id, error)
    return sent


def test(settings: Settings, ch: ChannelConfig) -> dict[str, Any]:
    """Send a test notification now, based on the latest event if there is one."""
    with session_scope() as s:
        latest = s.scalar(select(Event.id).order_by(Event.first_ts.desc()).limit(1))
    if latest is not None:
        data = payload(settings, latest, "test")
        image = event_image(settings, latest) if ch.type != "webhook" else None
    else:
        now = _iso(utcnow())
        data = {"type": "test", "sent_at": now,
                "event": {"id": 0, "camera_id": "test", "camera_name": "Test camera",
                          "profile_id": "default", "profile_name": "Test", "label": "fox",
                          "confidence": 0.9, "first_ts": now, "last_ts": now,
                          "detection_count": 1, "is_night": False, "clip_ready": False,
                          "clip_duration_s": None},
                "links": {"events": f"{settings.public_url}/events" if settings.public_url
                          else None, "thumbnail": None, "clip": None}}
        image = None
    try:
        send(ch, settings, data, image)
    except NotifyError as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "detail": f"sent to {ch.display_name}"}


def deliveries(limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(select(Notification, Event.label).join(Event, isouter=True)
                         .order_by(Notification.id.desc()).limit(limit)).all()
        return [{"id": n.id, "channel_id": n.channel_id, "event_id": n.event_id,
                 "camera_id": n.camera_id, "label": label, "kind": n.kind,
                 "status": n.status, "attempts": n.attempts, "error": n.error,
                 "created_at": _iso(n.created_at), "sent_at": _iso(n.sent_at)}
                for n, label in rows]


def cleanup(days: int = 30) -> int:
    with session_scope() as s:
        return s.execute(delete(Notification).where(
            Notification.created_at < utcnow() - timedelta(days=days),
            Notification.status != NotificationStatus.PENDING)).rowcount

