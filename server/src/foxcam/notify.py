"""Event notifications: webhooks, ntfy, and ~100 more services through Apprise (shared
channels), and each user's own web push notifications (personal).

Channels come from ``config.yaml`` (``notifications.channels``, read-only in the UI) or are
added in the web UI. Personal notifications go to the devices a user turned on, with rules the
user sets on their Account page, and only for cameras they can see. When an event starts (``event.new``) or its clip is ready
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
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import ffmpeg, webpush
from .config import ChannelConfig, NotificationRules, PersonalRules, Settings
from .db import db_ready, session_scope
from .models import (
    CameraAccess,
    CameraRole,
    Detection,
    Event,
    MaskVersion,
    Notification,
    NotificationChannel,
    NotificationPrefs,
    NotificationStatus,
    PushDevice,
    Segment,
    User,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 6  # ~30 s, 1, 2, 4, 8 min between tries
TIMEOUT = 20
SECRET_FIELDS = ("token", "password", "secret")
IMAGE_TYPES = ("ntfy", "apprise")  # webhooks get a link
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


# --- personal notifications (web push) ------------------------------------------------

OPEN = "open"  # the owner when sign-in is turned off (everyone is the same admin)
PERSONAL = "user:"  # outbox channel_id of a user's own notifications: user:<owner>


def owner_key(user_id: int | None) -> str:
    return OPEN if user_id is None else str(user_id)


def endpoint_id(endpoint: str) -> str:
    """Lets a browser recognise its own device without the UI ever seeing endpoints."""
    return hashlib.sha256(endpoint.encode()).hexdigest()[:16]


def personal_rules(owner: str) -> PersonalRules:
    with session_scope() as s:
        row = s.get(NotificationPrefs, owner)
        return PersonalRules.model_validate(row.config if row else {})


def save_personal_rules(owner: str, data: dict[str, Any]) -> PersonalRules:
    try:
        rules = PersonalRules.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise NotifyError(f"{loc + ': ' if loc else ''}{first['msg']}") from e
    with session_scope() as s:
        row = s.get(NotificationPrefs, owner)
        if row is None:
            s.add(NotificationPrefs(owner=owner, config=rules.model_dump()))
        else:
            row.config = rules.model_dump()
    return rules


def devices(owner: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(select(PushDevice).where(PushDevice.owner == owner)
                         .order_by(PushDevice.created_at)).all()
        return [{"id": d.id, "name": d.name, "endpoint_id": endpoint_id(d.endpoint),
                 "created_at": _iso(d.created_at), "last_sent_at": _iso(d.last_sent_at)}
                for d in rows]


def add_device(owner: str, endpoint: str, p256dh: str, auth: str, name: str | None) -> int:
    """Turn notifications on for a browser. The same browser again (new keys after a
    reinstall, or another user signed in on it) updates its one row."""
    if not endpoint.startswith("https://"):
        raise NotifyError("a push endpoint is an https URL")
    try:
        if len(webpush.unb64url(p256dh)) != 65 or len(webpush.unb64url(auth)) != 16:
            raise ValueError
    except ValueError:
        raise NotifyError("the browser's push keys are not valid") from None
    with session_scope() as s:
        row = s.scalar(select(PushDevice).where(PushDevice.endpoint == endpoint))
        if row is None:
            row = PushDevice(endpoint=endpoint)
            s.add(row)
        row.owner, row.p256dh, row.auth, row.name = owner, p256dh, auth, name
        s.flush()
        return row.id


def remove_device(owner: str, device_id: int) -> None:
    with session_scope() as s:
        row = s.get(PushDevice, device_id)
        if row is None or row.owner != owner:
            raise NotifyError("unknown device")
        s.delete(row)


def _may_see(s: Session, settings: Settings, owner: str, camera_id: str) -> bool:
    """Whether a user may get an event from this camera (as in the web UI)."""
    if owner == OPEN:
        return not settings.auth.enabled  # devices from before sign-in was turned on: no
    user = s.get(User, int(owner))
    if user is None or user.disabled:
        return False
    if user.is_admin:
        return True
    role = s.scalar(select(CameraAccess.role).where(CameraAccess.user_id == user.id,
                                                    CameraAccess.camera_id == camera_id))
    return role is not None and CameraRole.at_least(role, CameraRole.VIEWER)


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


def _local(settings: Settings, dt: datetime, zone: str | None = None) -> datetime:
    """A stored (naive UTC) time in `zone`, else notifications.timezone, else the server's."""
    zone = zone or settings.notifications.timezone
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(zone) if zone else None)


def _in_quiet_hours(settings: Settings, quiet: str, now: datetime,
                    zone: str | None = None) -> bool:
    local = _local(settings, now, zone).time()
    start, end = (time.fromisoformat(t) for t in quiet.split("-"))
    return start <= local < end if start < end else local >= start or local < end


def _matches(ch: NotificationRules, event: Event, kind: str) -> bool:
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


def _recipients(s: Session, settings: Settings,
                ) -> list[tuple[str, NotificationRules, str | None]]:
    """(outbox channel id, rules, time zone): every enabled channel, and every user with a
    device whose own notifications are on."""
    out: list[tuple[str, NotificationRules, str | None]] = [
        (ch.id, ch, None) for ch in all_channels(settings)]
    owners = s.scalars(select(PushDevice.owner).distinct()).all()
    saved = {p.owner: p.config for p in s.scalars(
        select(NotificationPrefs).where(NotificationPrefs.owner.in_(owners)))}
    for owner in owners:
        rules = PersonalRules.model_validate(saved.get(owner, {}))
        if rules.enabled:
            out.append((PERSONAL + owner, rules, rules.timezone))
    return out


def enqueue(s: Session, settings: Settings, event: Event, kind: str) -> int:
    """Queue `kind` for every matching channel and user (once each per event)."""
    now = utcnow()
    queued = 0
    if event.quiet or (event.id is not None and marked_wrong(s, event.id)):
        return 0
    for key, rules, zone in _recipients(s, settings):
        if not _matches(rules, event, kind):
            continue
        if key.startswith(PERSONAL) and \
                not _may_see(s, settings, key.removeprefix(PERSONAL), event.camera_id):
            continue
        exists = s.scalar(select(Notification.id).where(
            Notification.channel_id == key, Notification.event_id == event.id,
            Notification.kind == kind))
        if exists:
            continue
        status, reason = NotificationStatus.PENDING, None
        if rules.quiet_hours and _in_quiet_hours(settings, rules.quiet_hours, now, zone):
            status, reason = NotificationStatus.SKIPPED, "quiet hours"
        elif rules.cooldown_minutes and kind == "event.new" and s.scalar(
                select(func.count()).select_from(Notification).where(
                    Notification.channel_id == key, Notification.camera_id == event.camera_id,
                    Notification.kind == "event.new",
                    Notification.status.in_([NotificationStatus.PENDING,
                                              NotificationStatus.SENT]),
                    Notification.created_at >= now - timedelta(minutes=rules.cooldown_minutes))):
            status, reason = NotificationStatus.SKIPPED, "cooldown"
        s.add(Notification(channel_id=key, event_id=event.id, camera_id=event.camera_id,
                           kind=kind, status=status, error=reason))
        queued += status == NotificationStatus.PENDING
    return queued


# --- message content -----------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _when(settings: Settings, dt: datetime, zone: str | None = None) -> str:
    """"14:05:09", with the day when it was not today (a backlog sends old events late)."""
    local, today = _local(settings, dt, zone), _local(settings, utcnow(), zone).date()
    if local.date() == today:
        return local.strftime("%H:%M:%S")
    if local.date() == today - timedelta(days=1):
        return local.strftime("yesterday %H:%M:%S")
    return local.strftime("%a %-d %b %H:%M:%S")


def _seconds(s: float) -> str:
    s = round(s)
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60} s" if s % 60 else f"{s // 60} min"


def payload(settings: Settings, event_id: int, kind: str) -> dict[str, Any]:
    """The JSON document webhooks receive (also the source for ntfy / Apprise texts)."""
    with session_scope() as s:
        ev = s.get(Event, event_id)
        if ev is None:
            raise NotifyError("event no longer exists")
        cam = settings.camera(ev.camera_id)
        profile = settings.profile(ev.profile_id)
        detectors = [d.display_name if (d := profile and profile.detector(i)) else i
                     for i in ev.detectors or []]
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
                "detectors": ev.detectors or [], "detector_names": detectors,
                "agreement": ev.agreement or 1,
                "confirmed": ev.confirmed is not False,
                "clip_ready": bool(ev.clip_path and not ev.clip_dirty),
                "clip_duration_s": ev.clip_duration_s,
            },
            # Links into the (private) web UI; null without public_url in the config.
            "links": {
                # The event's details (opened in the camera's event list).
                "event": f"{base}/events?camera={ev.camera_id}&details={ev.id}" if base
                else None,
                "events": f"{base}/events?camera={ev.camera_id}" if base else None,
                "thumbnail": f"{base}/api/events/{ev.id}/thumb" if base and ev.thumb_path
                else None,
                "clip": f"{base}/api/events/{ev.id}/clip" if base and ev.clip_path else None,
            },
        }
    return data


def texts(settings: Settings, data: dict[str, Any],
          zone: str | None = None) -> tuple[str, str]:
    """Title ("Fox – Garden") and a few lines of facts about the event."""
    ev = data["event"]
    title = f"{ev['label'].capitalize()} – {ev['camera_name']}"
    first = datetime.fromisoformat(ev["first_ts"].rstrip("Z"))
    last = datetime.fromisoformat(ev["last_ts"].rstrip("Z")) if ev.get("last_ts") else first
    head = [_when(settings, first, zone), f"{round(ev['confidence'] * 100)} % sure"]
    if ev["is_night"]:
        head.append("night")
    lines = [" – ".join(head), f"Profile: {ev['profile_name']}"]
    count = ev.get("detection_count") or 1
    seen = f"{count} detection{'s' if count != 1 else ''}"
    if (last - first).total_seconds() >= 1:
        seen = f"Seen for {_seconds((last - first).total_seconds())} ({seen})"
    lines.append(seen[0].upper() + seen[1:])
    names = ev.get("detector_names") or ev.get("detectors") or []
    if len(names) > 1 or (ev.get("agreement") or 1) > 1:
        lines.append(f"Models: {', '.join(names)} ({ev.get('agreement') or 1} agree)")
    if data["type"] == "event.clip" and ev["clip_duration_s"]:
        lines.append(f"Clip ready ({_seconds(ev['clip_duration_s'])})")
    body = "\n".join(lines)
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
    """HTTP headers are ASCII; ntfy accepts RFC 2047 for anything else (å, ä, ö, emoji).
    Long values are split into several encoded words joined by spaces, not folded onto a new
    line as in email: a line break is not allowed in an HTTP header."""
    return value if value.isascii() else Header(value, "utf-8").encode(linesep=" ")


def send_ntfy(ch: ChannelConfig, settings: Settings, data: dict[str, Any],
              image: Path | None, client: httpx.Client) -> None:
    title, body = texts(settings, data)
    label = data["event"]["label"].lower()
    tags = [*ch.tags, *([LABEL_EMOJI[label]] if label in LABEL_EMOJI else [])]
    links = data["links"]
    # No click URL: tapping the notification shows it in full (picture and all), and its
    # buttons open the event or the clip.
    actions = [{"action": "view", "label": label, "url": links[key]}
               for key, label in (("event", "Open event"), ("clip", "Open clip"))
               if links.get(key)]
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
        if actions:
            headers["Actions"] = "; ".join(f"view, {a['label']}, {a['url']}" for a in actions)
        r = client.put(url, content=image.read_bytes(), headers=headers, auth=auth)
    else:
        message = {"topic": ch.topic, "title": title, "message": body, "tags": tags,
                   "priority": ch.priority}
        if actions:
            message["actions"] = actions
        r = client.post(ch.server.rstrip("/") + "/", json=message, headers=headers, auth=auth)
    if r.status_code >= 400:
        raise NotifyError(f"ntfy answered {r.status_code}: {r.text.strip()[:200]}")


def send_personal(settings: Settings, owner: str, data: dict[str, Any],
                  client: httpx.Client) -> None:
    """To every device of the user. Sent when it reached at least one; devices whose
    subscription is gone are forgotten."""
    rules = personal_rules(owner)
    title, body = texts(settings, data, rules.timezone)
    ev = data["event"]
    message = {"title": title, "body": body,
               # Opened in the app on tap; relative, so it works without public_url.
               "url": f"/events?camera={quote(ev['camera_id'])}&details={ev['id']}"
               if ev["id"] else "/events",
               # The clip-ready notification replaces the event's first one.
               "tag": f"event-{ev['id']}"}
    with session_scope() as s:
        # Access can change while a notification waits: check again.
        if ev["id"] and not _may_see(s, settings, owner, ev["camera_id"]):
            raise NotifyError("no longer allowed to see this camera")
        targets = [(d.id, d.name, webpush.Device(d.endpoint, d.p256dh, d.auth))
                   for d in s.scalars(select(PushDevice).where(PushDevice.owner == owner))]
    if not targets:
        raise NotifyError("no device has notifications turned on")
    sent, gone, errors = [], [], []
    for device_id, name, device in targets:
        try:
            webpush.send(settings, device, message, client, urgent=data["type"] != "event.clip")
            sent.append(device_id)
        except webpush.Gone:
            gone.append(device_id)
        except (httpx.HTTPError, RuntimeError) as e:
            errors.append(f"{name or 'device'}: {e}")
    with session_scope() as s:
        if gone:
            s.execute(delete(PushDevice).where(PushDevice.id.in_(gone)))
        for row in s.scalars(select(PushDevice).where(PushDevice.id.in_(sent))):
            row.last_sent_at = utcnow()
    if errors and sent:
        log.warning("web push to user %s reached %d device(s), not: %s", owner, len(sent),
                    "; ".join(errors))
    if not sent:
        raise NotifyError("; ".join(errors) or "every device has turned notifications off")


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
    if link := data["links"].get("event") or data["links"]["events"]:
        body += f"\n{link}"
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
        with session_scope() as s:
            # Marked wrong while it waited: not sent.
            if event_id is not None and marked_wrong(s, event_id):
                note = s.get(Notification, note_id)
                note.status, note.error = NotificationStatus.SKIPPED, "marked wrong"
                continue
        error, final = _deliver(settings, channel_id, event_id, kind, client)
        with session_scope() as s:
            note = s.get(Notification, note_id)
            if note is None:
                continue
            note.attempts += 1
            if error is None:
                note.status, note.sent_at, note.error = NotificationStatus.SENT, utcnow(), None
                sent += 1
            elif final or note.attempts >= MAX_ATTEMPTS or error == "event no longer exists":
                note.status, note.error = NotificationStatus.FAILED, error[:500]
            else:
                note.error = error[:500]
                note.next_attempt_at = utcnow() + timedelta(seconds=30 * 2 ** (note.attempts - 1))
        if error:
            log.warning("notification %s via %s failed: %s", note_id, channel_id, error)
    return sent


def _deliver(settings: Settings, channel_id: str, event_id: int, kind: str,
             client: httpx.Client | None) -> tuple[str | None, bool]:
    """Send one outbox row: (error or None, whether retrying is pointless)."""
    if channel_id.startswith(PERSONAL):
        owner = channel_id.removeprefix(PERSONAL)
        if not personal_rules(owner).enabled:
            return "turned off", True
        own = client is None
        client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
        try:
            send_personal(settings, owner, payload(settings, event_id, kind), client)
        except NotifyError as e:
            return str(e), str(e) == "no longer allowed to see this camera"
        finally:
            if own:
                client.close()
        return None, False
    ch = channel(settings, channel_id)
    if ch is None or not ch.enabled:
        return "channel removed or disabled", True
    try:
        data = payload(settings, event_id, kind)
        image = event_image(settings, event_id) if ch.include_image and \
            ch.type in IMAGE_TYPES else None
        send(ch, settings, data, image, client)
    except NotifyError as e:
        return str(e), False
    return None, False


def _test_data(settings: Settings, event_id: int | None) -> dict[str, Any]:
    """A test message: about `event_id`, else made up."""
    if event_id is not None:
        return payload(settings, event_id, "test")
    now = _iso(utcnow())
    return {"type": "test", "sent_at": now,
            "event": {"id": 0, "camera_id": "test", "camera_name": "Test camera",
                      "profile_id": "default", "profile_name": "Test", "label": "fox",
                      "confidence": 0.9, "first_ts": now, "last_ts": now,
                      "detection_count": 1, "is_night": False, "clip_ready": False,
                      "clip_duration_s": None, "detectors": [], "detector_names": [],
                      "agreement": 1},
            "links": {"event": None,
                      "events": f"{settings.public_url}/events" if settings.public_url
                      else None, "thumbnail": None, "clip": None}}


def test(settings: Settings, ch: ChannelConfig) -> dict[str, Any]:
    """Send a test notification now, based on the latest event if there is one."""
    with session_scope() as s:
        latest = s.scalar(select(Event.id).order_by(Event.first_ts.desc()).limit(1))
    image = event_image(settings, latest) if latest is not None and \
        ch.type in IMAGE_TYPES else None
    try:
        send(ch, settings, _test_data(settings, latest), image)
    except NotifyError as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "detail": f"sent to {ch.display_name}"}


def test_personal(settings: Settings, owner: str) -> dict[str, Any]:
    """A test to the user's devices, about the latest event they may see."""
    with session_scope() as s:
        latest = next((ev_id for ev_id, cam in s.execute(
            select(Event.id, Event.camera_id).order_by(Event.first_ts.desc()).limit(200))
            if _may_see(s, settings, owner, cam)), None)
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            send_personal(settings, owner, _test_data(settings, latest), client)
    except NotifyError as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "detail": "sent"}


def deliveries(limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(select(Notification, Event.label).join(Event, isouter=True)
                         .order_by(Notification.id.desc()).limit(limit)).all()
        users = {str(u.id): u.name or u.email for u in s.scalars(select(User))}
        return [{"id": n.id, "channel_id": n.channel_id, "event_id": n.event_id,
                 # A user's own notifications: whose.
                 "user": users.get(n.channel_id.removeprefix(PERSONAL), "–")
                 if n.channel_id.startswith(PERSONAL) else None,
                 "camera_id": n.camera_id, "label": label, "kind": n.kind,
                 "status": n.status, "attempts": n.attempts, "error": n.error,
                 "created_at": _iso(n.created_at), "sent_at": _iso(n.sent_at)}
                for n, label in rows]


def cleanup(days: int = 30) -> int:
    with session_scope() as s:
        return s.execute(delete(Notification).where(
            Notification.created_at < utcnow() - timedelta(days=days),
            Notification.status != NotificationStatus.PENDING)).rowcount

