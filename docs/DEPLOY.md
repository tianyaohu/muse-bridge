# Deploy

Nothing in this document deploys the bridge, buys a host, or signs up for useapi.net. Commands below are what an operator runs. Secrets stay in the host secret store or a mode-600 `.env`, never in the image or in git.

## Docker

The image copies `muse_bridge/` and `server.py`. It does not contain `MUSE_BRIDGE_TOKEN`. The entrypoint chowns `/data` and drops to uid 10001 before `server.py`. Legacy mode is off, so `/healthz` answers before any API key exists.

```bash
docker build -t muse-bridge .
docker run --rm -p 8080:8080 -v muse-bridge-data:/data muse-bridge
curl -fsS http://127.0.0.1:8080/healthz
```

Expected body: `{"ok": true, "server": "nori-muse-bridge", "mcp": "/mcp"}`.

Create a key inside a running container only when you mean to (the token is printed once):

```bash
docker exec -u app <container> python -m muse_bridge.admin create-key --user alice
```

## Fly.io

`deploy/fly.toml` is the app config: region `sea`, port 8080, volume `muse_bridge_data` mounted at `/data`, HTTP check `GET /healthz`, machines stay up (`auto_stop_machines = "off"`, `min_machines_running = 1`). Validate without deploying:

```bash
flyctl config validate --strict -c deploy/fly.toml
```

`flyctl` asks for an access token even for a local schema check. A dummy token checks the file and does not log in or deploy. Do not commit that token.

Operator steps, not run by this change:

```bash
fly launch --copy-config --no-deploy -c deploy/fly.toml
fly volumes create muse_bridge_data --size 1 --region sea
fly secrets set MUSE_BRIDGE_LEGACY_SINGLE_TOKEN=1 MUSE_BRIDGE_TOKEN="$(openssl rand -hex 24)"
fly deploy
curl -fsS https://nori-muse-bridge.fly.dev/healthz
```

Skip the secret when you will create per-user keys instead. Do not put the token in `[env]`.

## Mac mini and Cloudflare

On the mini, from the repo root:

```bash
deploy/macmini/setup.sh
```

That installs dependencies with `uv`, writes `.env` only if it is missing, and bootstraps `ai.nori.muse-bridge`. Logs go to `~/Library/Logs/muse-bridge.log`.

Named tunnel (durable hostname, needs a domain on Cloudflare):

```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create nori-muse-bridge
cloudflared tunnel route dns nori-muse-bridge muse-bridge.YOURDOMAIN.com
```

Edit a copy of `deploy/macmini/cloudflared-config.yml` so `credentials-file` and `hostname` match, save it as `~/.cloudflared/config.yml`, then `sudo cloudflared service install`. Check `curl https://muse-bridge.YOURDOMAIN.com/healthz`.

`./tunnel.sh` is the ephemeral quick tunnel. It looks for `bin/cloudflared` or `cloudflared` on `PATH`. The URL changes every restart, and it does not work from the Grok Bot box.
