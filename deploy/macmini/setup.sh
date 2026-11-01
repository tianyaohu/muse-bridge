#!/usr/bin/env bash
# Run ON THE MAC MINI after copying /workspace/muse-bridge to ~/muse-bridge.
set -euo pipefail
cd ~/muse-bridge
command -v uv >/dev/null || brew install uv
uv venv -q .venv && uv pip install -q --python .venv/bin/python -r requirements.txt
[ -f .env ] || (umask 077; echo "MUSE_BRIDGE_TOKEN=$(openssl rand -hex 24)" > .env)
cp deploy/macmini/ai.nori.muse-bridge.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/ai.nori.muse-bridge.plist 2>/dev/null || true
launchctl load -w ~/Library/LaunchAgents/ai.nori.muse-bridge.plist
sleep 3; curl -s http://127.0.0.1:8765/healthz; echo
echo "Next: brew install cloudflared; either 'cloudflared tunnel --url http://127.0.0.1:8765' (temp URL)"
echo "or a named tunnel (see README 'Durable URL')."
