# Notifications and webhooks

Foxcam can tell you when something is seen:

| Channel | Use it for | Image |
|---|---|---|
| **ntfy** | push notifications to your phone via [ntfy](https://ntfy.sh) (ntfy.sh or self-hosted) | ✅ attached |
| **webhook** | Home Assistant, Node-RED, n8n, Zapier, your own code: a JSON `POST` per event | link only |
| **Apprise** | ~100 services: Telegram, Discord, Slack, Pushover, Gotify, Signal, Matrix, e-mail, … | ✅ where supported |

Channels are added in the web UI (**Settings → Notifications**, with a *Send test* button
and the latest deliveries) or in `config.yaml`. Each channel chooses:

- **When**: `event.new` (an event starts: fast) and/or `event.clip` (its clip is ready, with
  a link to it).
- **Filters**: cameras, detection profiles, labels and a minimum confidence.
- **Quiet hours** (e.g. `23:00-06:00`, in `notifications.timezone`) and a **cooldown**
  (at most one *event starts* per camera per N minutes).
- **Image**: the frame of the best detection with its box, **censor areas blacked out**,
  so masked areas never leave the server through a notification either.

Delivery goes through an outbox: if a service is down, Foxcam retries with backoff
(30 s, 1, 2, 4, 8 min) before giving up; the UI shows sent / waiting / failed / skipped.

Set `public_url` (how you open the UI, e.g. `http://192.168.1.10:8000`) so notifications
can link to the event and its clip; `scripts/dev.sh` sets it to this machine's LAN address
(`MEDIA_STREAMER_PUBLIC_URL`) when the config has none. Those links point at your own server, so they only
open where the UI is reachable (home network, VPN).

```yaml
public_url: http://192.168.1.10:8000

notifications:
  timezone: Europe/Stockholm        # for quiet hours and times in messages
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
      url: http://homeassistant.local:8123/api/webhook/media-streamer-events
      secret: ${WEBHOOK_SECRET}

    - id: telegram
      type: apprise
      url: tgram://${TELEGRAM_BOT_TOKEN}/${TELEGRAM_CHAT_ID}
      min_confidence: 0.7
      min_agreement: 2                  # only when two of the profile's models saw it
```

> Use `when:`, not `on:` — YAML reads a bare `on` as the boolean `true`.

`min_agreement` (default 1) is for profiles with several detectors (models, see
[ARCHITECTURE.md](ARCHITECTURE.md#detection-profiles)): the channel is notified once that
many of them have seen the event. Each event notifies a channel at most once per trigger,
so a second model joining an event never sends it again.

## ntfy

Install the ntfy app (Android, iOS, web), subscribe to your topic, done. Options:
`server` (default `https://ntfy.sh`), `topic`, `token` (protected topics, `tk_…`) or
`username` / `password`, `priority` 1–5 and extra `tags` (emoji short codes become emojis;
the label adds one, e.g. 🦊 for `fox`). Messages carry a click-through link to the events
page and, for `event.clip`, an **Open clip** button.

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
    "detectors": ["speciesnet", "yolo"], "agreement": 2,
    "clip_ready": false, "clip_duration_s": null
  },
  "links": {
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

Create an automation with a **Webhook** trigger (webhook id `media-streamer-events`,
*local only* if the server is on your LAN) and use the payload, e.g.:

```yaml
triggers:
  - trigger: webhook
    webhook_id: media-streamer-events
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
