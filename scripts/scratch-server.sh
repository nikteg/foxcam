#!/usr/bin/env bash
# A throwaway Foxcam server for looking at UI changes: a copy of the local database (and
# the newest event clips) in a temporary directory, an admin account to sign in with, and
# the built web UI (mise run build first). The real data/ is never written to.
#
#   scripts/scratch-server.sh            # http://127.0.0.1:8765
#   PORT=8766 scripts/scratch-server.sh
#
# Sign in as scratch@example.com / scratchpass1. Ctrl+C stops it; the copy is removed.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
port="${PORT:-8765}"
clips="${CLIPS:-10}"  # how many of the newest event clips to copy
dir="${TMPDIR:-/tmp}/foxcam-scratch-$port"
src="$repo/data"

rm -rf "$dir"
mkdir -p "$dir"
trap 'rm -rf "$dir"' EXIT
if [[ -f "$src/foxcam.db" ]]; then
  sqlite3 -readonly "$src/foxcam.db" ".backup '$dir/foxcam.db'"
  sqlite3 -readonly "$src/foxcam.db" \
    "SELECT clip_path FROM event WHERE clip_path IS NOT NULL ORDER BY id DESC LIMIT $clips" |
    while read -r rel; do
      for f in "$src/$rel" "$src/${rel%.mp4}.jpg"; do
        [[ -f "$f" ]] && mkdir -p "$dir/$(dirname "${f#"$src"/}")" && cp "$f" "$dir/${f#"$src"/}"
      done
    done
fi

cd "$repo"
export MEDIA_DATA_DIR="$dir"
printf 'scratchpass1\nscratchpass1\n' | uv run foxcam user add scratch@example.com >/dev/null
uv run foxcam user admin scratch@example.com >/dev/null
echo "scratch server: http://127.0.0.1:$port (scratch@example.com / scratchpass1), data in $dir"
uv run foxcam serve --host 127.0.0.1 --port "$port"
