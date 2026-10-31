# Roadmap

## Planned

- **Fine-tuning workflow** – turn the training-set export (detections marked ✅/❌, as JSON
  from Events → Detections) into a YOLO/RF-DETR dataset (crops + labels), a training script
  for the GPU box, and side-by-side model comparison on `samples/`.
- **Remote recorder at the cabin** – documented Raspberry Pi + Tailscale setup that pulls RTSP
  locally and records on site, with the server reading over the VPN, as a robust alternative to
  the Tapo cloud relay.
- **Faster SpeciesNet worker** – keep the model loaded between jobs (Python API instead of
  the CLI); today every job pays ~25 s of model loading.
- **Events keep only their clip** – today an event pins the recordings it happened in, and
  after compaction that keeps the whole hourly file (retention only deletes whole files), up
  to 12 times the footage needed. Stop pinning: keep the clip (perhaps with a longer pre/post
  roll), let the recordings go with ordinary retention, and have the event drawer play the
  clip only and hide re-analyse once its recordings are gone. Starred detections still keep
  their recording. The alternative was trimming hourly files to the pinned stretches
  (stream copy), which keeps the footage around events but is more work.

## Ideas

- Notification templates (custom title/body per channel) and digest mode (one summary per
  night instead of one message per event).
- Hardware decoding for worker inputs on NVIDIA and Apple. Today the live transcodes and
  compaction use the GPU, worker inputs are decoded and scaled with VAAPI where a render
  node exists, and clips are cut without re-encoding ([HARDWARE.md](HARDWARE.md)).
- Recording profiles editable in the UI (today: built-in ones plus `config.yaml`; the UI
  only picks the default and each camera's).
- Motion-only events (movement the models cannot name) and object tracking across frames.
- Night-only schedule per camera (don't queue daylight segments).
- Event timeline view (per camera, per day).
- Swedish translations for server-side error messages.
- Per-camera privacy schedules (e.g. private while people are at the cabin).
- Personal channels besides web push: each account its own ntfy, Telegram or webhook for the
  cameras it sees. Today web push is per account, the other channels are shared and set up
  by an admin.
- Passkeys / TOTP as a second factor for password logins; password reset by e-mail.
