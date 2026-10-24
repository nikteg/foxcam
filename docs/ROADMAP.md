# Roadmap

## Planned

- **Authentication** – viewer/admin roles behind a login proxy. Design: [AUTH.md](AUTH.md).
- **Notifications** when a fox event is created (or ends, with the clip):
  - push via [ntfy](https://ntfy.sh) (self-hosted or ntfy.sh), Home Assistant webhook, or
    Telegram bot;
  - per-camera quiet hours and a minimum confidence;
  - payload: label, confidence, camera, time, thumbnail, link to the event in the UI.
  - Implementation sketch: a `notifications` section in `config.yaml` and a hook called from
    `pipeline/events.py` when an event's clip is built for the first time.
- **Fine-tuning workflow** – turn ✅/❌ feedback into a YOLO/RF-DETR dataset (crops + labels),
  a training script for the GPU box, and side-by-side model comparison on `samples/`.
- **Remote recorder at the cabin** – documented Raspberry Pi + Tailscale setup that pulls RTSP
  locally and records on site, with the server reading over the VPN, as a robust alternative to
  the Tapo cloud relay.
- **Faster SpeciesNet worker** – keep the model loaded between jobs (Python API instead of
  the CLI); today every job pays ~25 s of model loading.

## Ideas

- Motion pre-filter (skip segments without motion before queueing) to save GPU time.
- Night-only schedule per camera (don't queue daylight segments).
- Event timeline view (per camera, per day) and a "best of" gallery.
- Editable `config.yaml` settings from the UI (segment length, retention).
- Swedish translations for server-side error messages.
- Per-camera privacy schedules (e.g. private while people are at the cabin).
