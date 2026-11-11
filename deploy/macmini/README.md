# Mac mini

`setup.sh` creates the venv, writes a mode-600 `.env` only when one is missing, and installs the launchd agent. Placeholders in the plist (`__MUSE_BRIDGE_ROOT__`, `__HOME__`) are filled in on the machine. The committed plist has no home-directory path and no token.

Legacy single-token mode stays off, so the process can start before any key exists. Create a key with `python -m muse_bridge.admin create-key --user <id>` and give that token to Muse. To keep the original shared token, set `MUSE_BRIDGE_LEGACY_SINGLE_TOKEN=1` and `MUSE_BRIDGE_TOKEN` in `.env` (mode 600) and restart the agent.

`cloudflared-config.yml` is a named-tunnel example. Replace `REPLACE_ME`, `TUNNEL_UUID`, and `YOURDOMAIN`. Do not commit the credentials file cloudflared writes under `~/.cloudflared/`.

A quick tunnel (`./tunnel.sh`) prints a temporary `trycloudflare.com` URL and does not work from the Grok Bot box. Full steps are in `docs/DEPLOY.md`.
