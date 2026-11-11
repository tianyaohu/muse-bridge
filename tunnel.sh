#!/usr/bin/env bash
# Ephemeral Cloudflare quick tunnel. The printed URL changes on every restart.
# This does not work from the Grok Bot box (port 7844 is blocked).
set -euo pipefail
cd "$(dirname "$0")"
port="${MUSE_BRIDGE_PORT:-8765}"
if [[ -x bin/cloudflared ]]; then
  cloudflared=(bin/cloudflared)
elif command -v cloudflared >/dev/null 2>&1; then
  cloudflared=(cloudflared)
else
  echo "cloudflared not found (expected bin/cloudflared or on PATH)" >&2
  exit 1
fi
exec "${cloudflared[@]}" tunnel --no-autoupdate --protocol http2 --url "http://127.0.0.1:${port}"
