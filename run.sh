#!/usr/bin/env bash
# Start the bridge on 127.0.0.1:8765 unless .env overrides the bind.
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
legacy="$(printf '%s' "${MUSE_BRIDGE_LEGACY_SINGLE_TOKEN:-0}" | tr '[:upper:]' '[:lower:]')"
case "$legacy" in
  1 | true | yes | on)
    if [[ -z "${MUSE_BRIDGE_TOKEN:-}" ]]; then
      echo "MUSE_BRIDGE_LEGACY_SINGLE_TOKEN is on but MUSE_BRIDGE_TOKEN is empty" >&2
      exit 1
    fi
    ;;
esac
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python server.py
fi
exec python3 server.py
