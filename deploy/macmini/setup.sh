#!/usr/bin/env bash
# Run on the Mac mini from a checkout of this repo. Does not print the token.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
command -v uv >/dev/null || brew install uv
uv venv -q .venv
uv pip install -q --python .venv/bin/python -r requirements.txt
if [[ ! -f .env ]]; then
  umask 077
  printf 'MUSE_BRIDGE_HOST=127.0.0.1\nMUSE_BRIDGE_PORT=8765\nMUSE_BRIDGE_DATA_DIR=%s/data\nMUSE_BRIDGE_LEGACY_SINGLE_TOKEN=0\n' "$ROOT" > .env
  chmod 600 .env
fi
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
DEST="$HOME/Library/LaunchAgents/ai.nori.muse-bridge.plist"
sed -e "s#__MUSE_BRIDGE_ROOT__#${ROOT}#g" -e "s#__HOME__#${HOME}#g" \
  "$ROOT/deploy/macmini/ai.nori.muse-bridge.plist" > "$DEST"
launchctl bootout "gui/$(id -u)/ai.nori.muse-bridge" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST" 2>/dev/null || launchctl load -w "$DEST"
sleep 2
curl -fsS "http://127.0.0.1:${MUSE_BRIDGE_PORT:-8765}/healthz"
echo
echo "Next: brew install cloudflared, then a named tunnel. See docs/DEPLOY.md."
