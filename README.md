# Media Streamer 🦊

Records Tapo camera streams in configurable segments, hands censored copies to external
detection workers over HTTP, and keeps a clip whenever a fox shows up.

```
cameras ─► MediaMTX ─► segments on disk ─► server (censor, queue, events, retention, API)
                │                               ▲  HTTP lease/result
                └─► live view (WebRTC/HLS)      └── workers (GPU box, SaaS adapter, …)
```

| Part | Stack |
|---|---|
| `server/` | Python, FastAPI, SQLite, ffmpeg |
| `web/` | React 19, Fluent UI v9, Vite 8, React Compiler (oxc), TanStack Query, oxlint, oxfmt |
| `worker/` | Python, pluggable detectors (dummy, Ultralytics/YOLO-World, SpeciesNet) |
| `mediamtx/` | MediaMTX config (RTSP ingest, recording, live view) |

## Running locally

Requirements: `uv`, `pnpm`, `ffmpeg`, and the MediaMTX binary in `.tools/`:

```sh
mkdir -p .tools && cd .tools
TAG=$(curl -sL https://api.github.com/repos/bluenviron/mediamtx/releases/latest | python3 -c "import json,sys;print(json.load(sys.stdin)['tag_name'])")
curl -L -o mediamtx.tar.gz "https://github.com/bluenviron/mediamtx/releases/download/${TAG}/mediamtx_${TAG}_darwin_arm64.tar.gz"
tar xzf mediamtx.tar.gz mediamtx && rm mediamtx.tar.gz && cd ..
```

First time:

```sh
uv sync --all-packages              # Python: server + worker
pnpm --dir web install              # web UI
python3 scripts/fetch_samples.py    # fox clips from Wikimedia Commons -> samples/
```

Run everything (MediaMTX, server, ingest, web dev server and a worker):

```sh
scripts/dev.sh
```

Open http://localhost:5173 (hot reload) or http://localhost:8000 (built UI, run
`pnpm --dir web build` first). API docs: http://localhost:8000/docs. Ctrl+C stops all.

Without a `config.yaml`, the script copies `config.example.yaml` (a test-pattern camera).
To use the downloaded fox clips as fake cameras:

```yaml
cameras:
  - id: garden
    source: { type: file, path: ./samples/fox-garden.mp4 }
  - id: night
    source: { type: file, path: ./samples/fox-night-urban.mp4 }
```

The default worker is a dummy that reports a fox in every third segment, which is enough
to exercise the whole pipeline. For real detection:

```sh
uv sync --all-packages --extra ultralytics
WORKER_ARGS="--detector ultralytics --model yolov8s-worldv2.pt --classes fox --device mps" scripts/dev.sh
```

On the GPU machine, run only the worker and point it at this server. SpeciesNet (the most
accurate option, see [docs/MODELS.md](docs/MODELS.md)) needs Python 3.12 and its own
environment because its dependencies conflict with Ultralytics:

```sh
uv venv --python 3.12 .venv-speciesnet
VIRTUAL_ENV=.venv-speciesnet uv pip install -e "./worker[speciesnet]"
.venv-speciesnet/bin/media-worker --server http://<this-host>:8000 --detector speciesnet
```

Web checks: `pnpm --dir web check` (typecheck, oxlint and an oxfmt check).

## Connecting Tapo cameras

Open **Cameras → Add camera** in the web UI. Cameras added there start recording within a
few seconds; no restart or `config.yaml` edit is needed.

- **Tapo camera (cloud)**: works from anywhere, like the Tapo app (the summer-house case).
  Sign in with your Tapo account; if two-step verification is on, enter the code Tapo
  sends. Pick cameras from the list. Requires `uv sync --all-packages --extra tapo-cloud`.
  This uses an unofficial, reverse-engineered library ([ontapo](https://pypi.org/project/ontapo/)),
  so it can break if TP-Link changes their cloud, and it uses upload bandwidth at the
  camera's site continuously (HD ≈ 1–2 Mbit/s per camera).
- **RTSP camera**: the robust option when the server can reach the camera, on the same
  network or over a VPN (e.g. a Raspberry Pi with Tailscale at the cabin). In the Tapo
  app create a *Camera Account* (camera → Settings → Advanced settings → Camera account);
  the dialog builds `rtsp://user:pass@<ip>:554/stream1` and can test the connection.

### Testing the Tapo cloud stream without sharing credentials

Your Tapo password is only ever typed into a local prompt (or the local web UI) and sent
straight to TP-Link. What is stored is a session token in `data/tapo-session.json`
(file mode 0600, git-ignored), which is as sensitive as the password: don't share it.

```sh
uv sync --all-packages --extra tapo-cloud
uv run media-streamer tapo-login      # e-mail, password (hidden), then the MFA code
uv run media-streamer tapo-check      # lists cameras, records 10 s, prints codec/size only
uv run media-streamer tapo-check --camera "Garden" --seconds 30 --resolution VGA
uv run media-streamer tapo-logout     # delete the saved session
```

`tapo-check` output contains no secrets and is safe to share when asking for help. Changing
your Tapo password invalidates the stored session.

## Docker

```sh
docker compose up --build -d    # mediamtx + server (with built UI) + ingest
```

Compose reads `config.yaml` and stores data in `./data`. To run it next to a dev stack, or
with another config, override via environment (or a `.env` file):

```sh
MEDIA_CONFIG=./other-config.yaml MEDIA_DATA_PATH=./other-data \
HTTP_PORT=18000 RTSP_PORT=18554 HLS_PORT=18888 WEBRTC_PORT=18889 WEBRTC_UDP_PORT=18189 \
  docker compose -p media-streamer-test up --build -d
```

Set `PUBLIC_HOST_URL` (e.g. `http://192.168.1.10`) so browsers on other devices can reach
the live view, and add that IP to `webrtcAdditionalHosts` in `mediamtx/mediamtx.yml`.

## S3-compatible storage

Clips (and optionally all recordings) can be copied to an S3-compatible bucket: AWS S3,
Cloudflare R2, Backblaze B2, MinIO, Hetzner, Wasabi… Fill in `storage.s3` in `config.yaml`
(see `config.example.yaml`), install the extra and restart:

```sh
uv sync --all-packages --extra s3     # included in the Docker image
```

Credentials can be left out of the file and passed as `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY`. Settings → Storage shows upload progress and has a connection test.
Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#object-storage-s3).

## Tests

```sh
uv run pytest server/tests worker/tests      # server + worker (needs ffmpeg)
pnpm --dir web check                          # typecheck, oxlint, oxfmt
```

Detector accuracy on real fox footage (opt-in, needs a detector installed):

```sh
MEDIA_WORKER_TEST_DETECTOR=ultralytics MEDIA_WORKER_TEST_CLASSES=fox uv run pytest worker/tests
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – components, pipeline, data model, retention, privacy
- [docs/WORKER_PROTOCOL.md](docs/WORKER_PROTOCOL.md) – HTTP API for workers / SaaS adapters
- [docs/MODELS.md](docs/MODELS.md) – detection models for foxes, with measured results
- [docs/AUTH.md](docs/AUTH.md) – authentication design (not implemented yet)
- [docs/ROADMAP.md](docs/ROADMAP.md) – notifications and other planned work
