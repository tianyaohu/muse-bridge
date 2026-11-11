"""Shared helpers for tool handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from muse_bridge.auth.principal import Principal
from muse_bridge.config import Settings
from muse_bridge.context import current_principal, current_settings, current_store
from muse_bridge.storage.base import InboxStore
from muse_bridge.webhook import schedule_webhook


def require_auth() -> tuple[Principal, InboxStore, Settings]:
    principal = current_principal.get()
    store = current_store.get()
    settings = current_settings.get()
    if principal is None or store is None or settings is None:
        raise ToolError("not authenticated")
    return principal, store, settings


def require_scope(scope: str) -> tuple[Principal, InboxStore, Settings]:
    principal, store, settings = require_auth()
    if not principal.has_scope(scope):
        raise ToolError(f"missing scope: {scope}")
    return principal, store, settings


def fanout(settings: Settings) -> Callable[[dict[str, Any]], None]:
    def deliver(record: dict[str, Any]) -> None:
        schedule_webhook(settings, record)

    return deliver
