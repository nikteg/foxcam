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

Requirements: [mise](https://mise.jdx.dev), `uv`, `pnpm` and `ffmpeg`. Everything else is a
mise task (`mise tasks` lists them all):

```sh
mise run setup      # Python + web deps, config.yaml, MediaMTX binary, fox sample clips
mise run dev        # whole stack with a dummy worker (Ctrl+C stops it)
mise run seed       # with the stack running: a fake camera per sample clip
```

Workers always need an access token. The dev stack and the `worker*` tasks create a local
`dev` token for themselves (kept in `data/dev-worker.token`); other workers get one from
**Worker queue → Add worker** or `mise run worker:token -- <name>`.

| Task | What it does |
|---|---|
| `dev`, `dev:no-worker`, `dev:yolo` | whole stack, with a dummy / no / YOLO-World worker |
| `server`, `ingest`, `mediamtx`, `web`, `worker`, `worker:yolo`, `worker:speciesnet` | one part at a time |
| `seed` (`-- --profiles`) | test cameras from `samples/` (and an example vehicles profile) |
| `check`, `test`, `lint`, `fmt`, `test:detectors` | quality checks |
| `s3:local` / `s3:stop` | local S3 (SeaweedFS) to try `storage.s3` |
| `docker:up`, `docker:test`, … | Docker stacks (the test stack runs next to dev on :18000) |
| `stop` | kill leftovers on the dev ports |
| `clean` / `reset` / `clean:all` | caches – all data (asks) – back to a fresh checkout (asks) |
| `reset:recordings`, `reset:events`, `reset:detections`, `reset:queue`, `reset:workers`, `reset:cameras`, `reset:profiles`, `reset:tapo` | delete one kind of data and keep the rest (asks; stop the server first) |

Without mise, the same steps by hand:

```sh
scripts/get_mediamtx.sh             # MediaMTX -> .tools/
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

On the GPU machine, install only the worker and point it at this server. SpeciesNet (the
most accurate option, see [docs/MODELS.md](docs/MODELS.md)) needs Python 3.12 and cannot be
installed next to Ultralytics:

```sh
uv tool install --python 3.12 --torch-backend auto "media-worker[speciesnet] @ ./worker"
media-worker doctor --server http://<this-host>:8000 --token msw_...   # checks GPU, ffmpeg, token
media-worker --server http://<this-host>:8000 --token msw_... --detector speciesnet
```

Different workers can look for different things on different cameras with **detection
profiles** (e.g. foxes with SpeciesNet in the garden, cars with YOLO-World on the driveway):
see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#detection-profiles).

Windows (WSL 2 or native), Docker with GPU passthrough, autostart and troubleshooting:
[docs/WORKER_SETUP.md](docs/WORKER_SETUP.md). **Worker queue → Add worker** in the web UI
creates the token and shows the commands for each platform.

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
- [docs/WORKER_SETUP.md](docs/WORKER_SETUP.md) – GPU worker on Windows/WSL, Docker, Linux
- [docs/WORKER_PROTOCOL.md](docs/WORKER_PROTOCOL.md) – HTTP API for workers / SaaS adapters
- [docs/MODELS.md](docs/MODELS.md) – detection models for foxes, with measured results
- [docs/AUTH.md](docs/AUTH.md) – authentication design (not implemented yet)
- [docs/ROADMAP.md](docs/ROADMAP.md) – notifications and other planned work
