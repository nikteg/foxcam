# Roadmap

## Planned

- **Fine-tuning workflow** – turn ✅/❌ feedback into a YOLO/RF-DETR dataset (crops + labels),
  a training script for the GPU box, and side-by-side model comparison on `samples/`.
- **Remote recorder at the cabin** – documented Raspberry Pi + Tailscale setup that pulls RTSP
  locally and records on site, with the server reading over the VPN, as a robust alternative to
  the Tapo cloud relay.
- **Faster SpeciesNet worker** – keep the model loaded between jobs (Python API instead of
  the CLI); today every job pays ~25 s of model loading.

## Ideas

- Notification templates (custom title/body per channel) and digest mode (one summary per
  night instead of one message per event).
- Hardware decoding for the batch work too (worker inputs, clips, thumbnails); today only
  the live transcodes use the GPU ([HARDWARE.md](HARDWARE.md)).
- Recording profiles editable in the UI (today: built-in ones plus `config.yaml`).
- Motion pre-filter (skip segments without motion before queueing) to save GPU time.
- Night-only schedule per camera (don't queue daylight segments).
- Event timeline view (per camera, per day) and a "best of" gallery.
- Editable `config.yaml` settings from the UI (segment length, retention).
- Swedish translations for server-side error messages.
- Per-camera privacy schedules (e.g. private while people are at the cabin).
- Per-user notifications (each account picks its own channels for the cameras it sees).
- Passkeys / TOTP as a second factor for password logins; password reset by e-mail.
