# Notifications and webhooks

Foxcam can tell you when something is seen. Everyone can get their own push notifications
in the Foxcam app ([below](#your-own-notifications-web-push)); admins also set up shared
channels:

| Channel | Use it for | Image |
|---|---|---|
| **ntfy** | push notifications to your phone via [ntfy](https://ntfy.sh) (ntfy.sh or self-hosted) | ✅ attached |
| **webhook** | Home Assistant, Node-RED, n8n, Zapier, your own code: a JSON `POST` per event | link only |
| **Apprise** | ~100 services: Telegram, Discord, Slack, Pushover, Gotify, Signal, Matrix, e-mail, … | ✅ where supported |

Channels are added in the web UI (**Settings → Notifications**, with a *Send test* button
and the latest deliveries) or in `config.yaml`. Each channel chooses:

- **When**: `event.new` (an event starts: fast) and/or `event.clip` (its clip is ready, with
  a link to it).
- **Filters**: cameras, detection profiles, labels, and "Notify from" (`min_confidence`): only
  events whose best match is at least this are sent. Events below it are still made, just
  not sent to this channel. Which detections are matches at all is the profile's match
  threshold ("Match from").
- **Quiet hours** (e.g. `23:00-06:00`, in the time zone set under Settings → Notifications,
  `notifications.timezone` in the config) and a **cooldown**
  (at most one *event starts* per camera per N minutes).
- **Image**: the frame of the best detection with its box and label (e.g. "fox 94%"),
  **censor areas blacked out**,
  so masked areas never leave the server through a notification either.

Delivery goes through an outbox: if a service is down, Foxcam retries with backoff
(30 s, 1, 2, 4, 8 min) before giving up; the UI shows sent / waiting / failed / skipped.
An event marked wrong (one of its detections marked wrong, none correct) sends nothing more:
notifications still waiting for it are skipped. Detections marked wrong and objects standing
still join no event, so they notify nobody, and neither do events made again by
Settings → Detection → Maintenance → Rebuild events.

Set `public_url` (how you open the UI, e.g. `http://192.168.1.10:8000`) so notifications
can link to the event and its clip; `scripts/dev.sh` sets it to this machine's LAN address
(`FOXCAM_PUBLIC_URL`) when the config has none. Those links point at your own server, so they only
open where the UI is reachable (home network, VPN).

```yaml
public_url: http://192.168.1.10:8000

notifications:
  timezone: Europe/Stockholm        # quiet hours and times in messages (also in Settings)
  channels:
    - id: phone
      name: My phone
      type: ntfy
      topic: foxes-at-the-cabin-7f3k  # hard to guess: ntfy.sh topics are public
      when: [event.new, event.clip]
      profiles: [wildlife]
      quiet_hours: "23:00-06:00"
      priority: 4                       # 1 min … 5 urgent

    - id: home-assistant
      type: webhook
      url: http://homeassistant.local:8123/api/webhook/foxcam-events
      secret: ${WEBHOOK_SECRET}

    - id: telegram
      type: apprise
      url: tgram://${TELEGRAM_BOT_TOKEN}/${TELEGRAM_CHAT_ID}
      min_confidence: 0.7               # "Notify from": best match at least 70 %
      min_agreement: 2                  # only when two of the profile's models saw it
```

> Use `when:`, not `on:` — YAML reads a bare `on` as the boolean `true`.

`min_agreement` (default 1) is for profiles with several detectors (models, see
[ARCHITECTURE.md](ARCHITECTURE.md#detection-profiles)): the channel is notified once that
many of them have seen the event. Each event notifies a channel at most once per trigger,
so a second model joining an event never sends it again.

## Your own notifications (web push)

Notifications straight from Foxcam to your own phone or computer, without another app. They
are not a channel: everyone sets up their own on their **Account** page, and only gets
events from cameras they can see (admins: all of them).

- **Turn on here** turns them on for the device you are using; do it on each device. The
  page lists your devices (and when each was last notified); **Remove** stops one.
- **What to notify about**: the same rules as a channel (when, cameras, profiles, labels,
  how sure, quiet hours, cooldown), just for you. Quiet hours and the times in messages are
  in your browser's time zone, saved with the rules.
- **Send test** sends one to all your devices, about the latest event you can see.

Where it works:

- **iPhone and iPad** (iOS 16.4 or later): only in the home screen app. In Safari use Share →
  **Add to Home Screen**, open Foxcam from there, and turn them on in it.
- **Android, Mac, Windows, Linux**: Chrome, Edge, Firefox and Safari, also in a normal tab.
- Needs HTTPS (or `localhost`), like any web push.

The text is the same as ntfy's. Tapping it opens the event in Foxcam; the "clip ready"
notification replaces the event's first one. There is no picture: iOS doesn't show images in
web push notifications.

Access is checked again when a notification is sent, so someone whose access to a camera was
taken away while it waited doesn't get it. Admins see everyone's deliveries in the list under
Settings → Notifications, but not their rules or devices.

The server signs its messages with a key pair it makes on first use
(`<data_dir>/webpush-vapid-key.pem`). Keep it with your backups: browsers subscribe to that
key, so a new one means turning notifications on again on every device. A device that
removed the app or blocked notifications is forgotten the next time a send to it fails.

## ntfy

Install the ntfy app (Android, iOS, web), subscribe to your topic, done. Options:
`server` (default `https://ntfy.sh`), `topic`, `token` (protected topics, `tk_…`) or
`username` / `password`, `priority` 1–5 and extra `tags` (emoji short codes become emojis;
the label adds one, e.g. 🦊 for `fox`).

A message says when the event started (with the day when it was not today), how sure the
match is, day or night, the profile, how long it was seen and in how many detections, the
models when several agree, and the clip's length once it is ready. Without a time zone
(Settings → Notifications) the times are the server's own clock, which is UTC in Docker.
Tapping it shows the whole notification (picture and all) rather than opening a page: its
**Open event** button opens the event's details, and **Open clip** the clip once it is ready.

On the public ntfy.sh anyone who knows a topic name can read it: pick a long random name,
use access control, or self-host ntfy.

## Webhooks

A `POST` with `Content-Type: application/json` per notification:

```json
{
  "type": "event.new",
  "sent_at": "2026-10-24T13:05:12Z",
  "event": {
    "id": 42, "camera_id": "garden", "camera_name": "Garden",
    "profile_id": "wildlife", "profile_name": "Wildlife",
    "label": "fox", "confidence": 0.91,
    "first_ts": "2026-10-24T13:04:58Z", "last_ts": "2026-10-24T13:05:03Z",
    "detection_count": 12, "is_night": true,
    "detectors": ["speciesnet", "yolo"], "detector_names": ["SpeciesNet", "YOLO-World"],
    "agreement": 2,
    "clip_ready": false, "clip_duration_s": null
  },
  "links": {
    "event": "http://192.168.1.10:8000/events?camera=garden&details=42",
    "events": "http://192.168.1.10:8000/events?camera=garden",
    "thumbnail": null,
    "clip": null
  }
}
```

`type` is `event.new`, `event.clip` (clip ready; `links.clip` filled in) or `test`. Any
`2xx` counts as delivered. Extra request headers can be set per channel (`headers:`).

With a `secret`, the raw body is signed: `X-Foxcam-Signature: sha256=<hex>`
(HMAC-SHA256). Verify it before trusting the request:

```python
import hashlib, hmac

def valid(body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

### Home Assistant

Create an automation with a **Webhook** trigger (webhook id `foxcam-events`,
*local only* if the server is on your LAN) and use the payload, e.g.:

```yaml
triggers:
  - trigger: webhook
    webhook_id: foxcam-events
    allowed_methods: [POST]
conditions:
  - condition: template
    value_template: "{{ trigger.json.event.label == 'fox' }}"
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: "{{ trigger.json.event.label | title }} – {{ trigger.json.event.camera_name }}"
      message: "{{ (trigger.json.event.confidence * 100) | round }}% confidence"
      data:
        url: "{{ trigger.json.links.events }}"
```

## Apprise

[Apprise](https://github.com/caronc/apprise) speaks to ~100 services with one URL each,
e.g. `tgram://bottoken/chatid` (Telegram), `discord://webhook_id/webhook_token`,
`pover://user@token` (Pushover), `gotify://host/token`, `mailtos://user:pass@gmail.com`.
See the [Apprise wiki](https://github.com/caronc/apprise/wiki) for all of them. It needs the
`notify` extra (`uv sync --all-packages --extra notify`; the Docker image includes it).
Apprise URLs usually contain tokens: the UI never shows them again after saving.
