"""Health tool. Read-only, no inbox access."""

from __future__ import annotations

from muse_bridge.clock import utcnow_iso
from muse_bridge.tools.annotations import hints
from muse_bridge.tools.common import require_auth

DESCRIPTION = "Health check. Returns server name, version, and time. Read-only; no side effects."
TITLE = "Ping"


def annotations(open_world: bool = False):
    del open_world
    return hints(read=True, destructive=False, idempotent=True, open_world=False)


def ping() -> dict:
    _, _, settings = require_auth()
    return {
        "ok": True,
        "server": settings.server_name,
        "version": settings.version,
        "time": utcnow_iso(),
    }
