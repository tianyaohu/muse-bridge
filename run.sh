#!/usr/bin/env bash
# Starts the bridge server (port 8765) on localhost.
cd "$(dirname "$0")"; set -a; . ./.env; set +a
exec .venv/bin/python server.py
