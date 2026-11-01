#!/usr/bin/env bash
# Quick (ephemeral) Cloudflare tunnel -> prints https://<random>.trycloudflare.com
cd "$(dirname "$0")"
exec bin/cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:${MUSE_BRIDGE_PORT:-8765} 2>&1 | tee tunnel.log
