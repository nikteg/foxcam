#!/usr/bin/env bash
# Calls a Foxcam server's API with an API key, without the key showing up in commands or
# output. Reads FOXCAM_PROD_URL and FOXCAM_PROD_TOKEN from the environment, else from
# mise.local.toml (gitignored) through `mise env`.
#
#   scripts/prod-api.sh GET /api/events?limit=30
#   scripts/prod-api.sh GET /api/events/21/clip -o clip.mp4
#   scripts/prod-api.sh POST /api/segments/1492/reanalyse
#   scripts/prod-api.sh PUT /api/settings/runtime '{"processing.sample_fps": 2}'
#
# Extra arguments after the path (and the JSON body, for POST/PUT/PATCH) go to curl.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "${FOXCAM_PROD_TOKEN:-}" || -z "${FOXCAM_PROD_URL:-}" ]]; then
  eval "$(cd "$repo" && mise env -s bash 2>/dev/null | grep -E '^export FOXCAM_PROD_(URL|TOKEN)=')" || true
fi
if [[ -z "${FOXCAM_PROD_TOKEN:-}" || -z "${FOXCAM_PROD_URL:-}" ]]; then
  echo "FOXCAM_PROD_URL / FOXCAM_PROD_TOKEN missing: put them in $repo/mise.local.toml" >&2
  exit 1
fi

method="$1" path="$2"
shift 2
body=()
if [[ "$method" =~ ^(POST|PUT|PATCH)$ && $# -gt 0 && "$1" != -* ]]; then
  body=(-H "Content-Type: application/json" --data "$1")
  shift
fi

# The key goes to curl on stdin, not on the command line (ps, logs).
printf 'Authorization: Bearer %s\n' "$FOXCAM_PROD_TOKEN" |
  curl -sS --fail-with-body -X "$method" -H @- ${body[@]+"${body[@]}"} "$@" \
    "${FOXCAM_PROD_URL%/}$path"
