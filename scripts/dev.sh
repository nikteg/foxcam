#!/usr/bin/env bash
# Start the whole stack locally: MediaMTX, server (API + pipeline), ingest, web dev server
# and a worker. Ctrl+C stops everything.
#
#   scripts/dev.sh                                   # dummy worker that "sees" foxes
#   WORKER_ARGS="--detector ultralytics --model yolov8s-worldv2.pt --device mps" scripts/dev.sh
#   WORKER_ARGS=none scripts/dev.sh                  # no worker (run your own elsewhere)
#   RELOAD=0 scripts/dev.sh                          # don't restart on code changes
#   RELOAD_INGEST=0 scripts/dev.sh                   # never restart ingest (camera streams)
#
# Server, ingest and worker restart when their Python code changes (web: Vite HMR).
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

# Recording file names include the UTC offset; TZ=UTC just keeps them in UTC. Started
# through foxcam, which gives it the login the server and ingest use.
start mediamtx env TZ=UTC uv run foxcam mediamtx --binary "$MEDIAMTX" mediamtx/mediamtx.yml
sleep 1
# This machine's LAN address, so other devices can use the UI (and notifications link to it).
lan_ip() {
  if command -v ipconfig >/dev/null 2>&1; then # macOS: the interface of the default route
    local iface
    iface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')"
    ipconfig getifaddr "${iface:-en0}" 2>/dev/null || true
  else
    hostname -I 2>/dev/null | awk '{print $1}'
  fi
}
LAN_IP="$(lan_ip)"
if [ -n "$LAN_IP" ]; then
  export FOXCAM_PUBLIC_URL="${FOXCAM_PUBLIC_URL:-http://$LAN_IP:5173}"
fi

# API docs (/docs) are off unless asked for: they are outside the login.
export FOXCAM_API_DOCS="${FOXCAM_API_DOCS:-1}"

RELOAD="${RELOAD:-1}"
# Restart a command when Python files under the given directory change.
watch() { # dir, command...
  local dir=$1
  shift
  if [ "$RELOAD" = 1 ]; then
    uv run watchfiles --filter python --sigint-timeout 10 "$*" "$dir"
  else
    uv run "$@"
  fi
}
if [ "$RELOAD" = 1 ]; then
  start server uv run foxcam serve --host 0.0.0.0 --port 8000 --reload
else
  start server uv run foxcam serve --host 0.0.0.0 --port 8000
fi
# Ingest only restarts for changes to its own code: a restart drops every camera stream
# (Tapo cloud sessions take ~20-30 s to come back), so server/web edits leave it alone.
if [ "${RELOAD_INGEST:-$RELOAD}" = 1 ]; then
  start ingest watch server/src/foxcam/ingest foxcam ingest
else
  start ingest uv run foxcam ingest
fi
# Joins finished hours into one file each (and re-encodes per recording profile).
if [ "$RELOAD" = 1 ]; then
  start compact watch server/src/foxcam/pipeline foxcam compact
else
  start compact uv run foxcam compact
fi
start web pnpm --dir web dev --host
if [ "$WORKER_ARGS" != none ]; then
  sleep 3
  # Workers need a token; a local "dev" token is created once and kept in data/.
  token="${FOXCAM_WORKER_TOKEN:-$(uv run foxcam worker-token dev --save data/dev-worker.token)}"
  # shellcheck disable=SC2086
  export FOXCAM_WORKER_TOKEN="$token"
  start worker watch worker/src foxcam-worker --server http://localhost:8000 $WORKER_ARGS
fi

echo
echo "  UI (dev, hot reload):  http://localhost:5173"
echo "  UI (built) + API:      http://localhost:8000   (API docs: /docs)"
if [ -n "$LAN_IP" ]; then
  echo "  Other devices:         http://$LAN_IP:5173   (live view and notification links use this host)"
fi
if [ -f data/setup-code ]; then
  echo "  First account:         setup code $(cat data/setup-code) (also in data/setup-code)"
fi
echo "  Ctrl+C to stop"
echo
wait
