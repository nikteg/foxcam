#!/usr/bin/env bash
# Start the whole stack locally: MediaMTX, server (API + pipeline), ingest, web dev server
# and a worker. Ctrl+C stops everything.
#
#   scripts/dev.sh                                   # dummy worker that "sees" foxes
#   WORKER_ARGS="--detector ultralytics --model yolov8s-worldv2.pt --classes fox --device mps" scripts/dev.sh
#   WORKER_ARGS=none scripts/dev.sh                  # no worker (run your own elsewhere)
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f config.yaml ] || { cp config.example.yaml config.yaml; echo "created config.yaml"; }
MEDIAMTX="${MEDIAMTX:-.tools/mediamtx}"
command -v "$MEDIAMTX" >/dev/null || [ -x "$MEDIAMTX" ] || {
  echo "MediaMTX not found at $MEDIAMTX (see README: download it to .tools/mediamtx)" >&2
  exit 1
}
WORKER_ARGS="${WORKER_ARGS:---detector dummy --dummy-label fox}"

cleanup() {
  trap - EXIT INT TERM
  echo "stopping…"
  # Everything started here shares this script's process group.
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

start() { # name, command...  (output prefixed with [name])
  local name=$1
  shift
  "$@" 2>&1 | sed -u "s/^/[$name] /" &
}

# Recording file names include the UTC offset; TZ=UTC just keeps them in UTC.
start mediamtx env TZ=UTC "$MEDIAMTX" mediamtx/mediamtx.yml
sleep 1
start server uv run media-streamer serve --host 0.0.0.0 --port 8000
start ingest uv run media-streamer ingest
start web pnpm --dir web dev --host
if [ "$WORKER_ARGS" != none ]; then
  sleep 3
  # shellcheck disable=SC2086
  start worker uv run media-worker --server http://localhost:8000 $WORKER_ARGS
fi

echo
echo "  UI (dev, hot reload):  http://localhost:5173"
echo "  UI (built) + API:      http://localhost:8000   (API docs: /docs)"
echo "  Ctrl+C to stop"
echo
wait
