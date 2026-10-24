#!/usr/bin/env bash
# Download the MediaMTX binary for this OS/CPU into .tools/mediamtx.
#
#   scripts/get_mediamtx.sh              # latest release (skips if already present)
#   MEDIAMTX_VERSION=v1.21.2 FORCE=1 scripts/get_mediamtx.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=.tools/mediamtx
if [ -x "$DEST" ] && [ -z "${FORCE:-}" ]; then
  echo "MediaMTX already installed: $("$DEST" --version 2>/dev/null | head -1) (FORCE=1 to replace)"
  exit 0
fi

case "$(uname -s)" in
  Darwin) os=darwin ;;
  Linux) os=linux ;;
  *) echo "unsupported OS $(uname -s); download MediaMTX manually" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) arch=amd64 ;;
  arm64 | aarch64) arch=arm64 ;;
  *) echo "unsupported CPU $(uname -m); download MediaMTX manually" >&2; exit 1 ;;
esac

tag="${MEDIAMTX_VERSION:-$(curl -fsSL https://api.github.com/repos/bluenviron/mediamtx/releases/latest |
  python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
base="https://github.com/bluenviron/mediamtx/releases/download/${tag}"
# Linux ARM builds have been published as both arm64 and arm64v8.
for name in "${os}_${arch}" "${os}_${arch}v8"; do
  if curl -fsSL -o "$tmp/m.tar.gz" "$base/mediamtx_${tag}_${name}.tar.gz"; then
    mkdir -p .tools
    tar -xzf "$tmp/m.tar.gz" -C "$tmp" mediamtx
    mv "$tmp/mediamtx" "$DEST"
    chmod +x "$DEST"
    echo "installed MediaMTX ${tag} (${name}) to $DEST"
    exit 0
  fi
done
echo "no MediaMTX ${tag} build found for ${os}/${arch}" >&2
exit 1
