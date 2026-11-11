"""Per-request state for MCP tool handlers.

Auth middleware sets these context vars before the Streamable HTTP app runs.
anyio copies the context into worker threads, so synchronous tools see them.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from muse_bridge.auth.principal import Principal
from muse_bridge.config import Settings
from muse_bridge.storage.base import InboxStore

current_principal: ContextVar[Principal | None] = ContextVar("muse_bridge_principal", default=None)
current_store: ContextVar[InboxStore | None] = ContextVar("muse_bridge_store", default=None)
current_settings: ContextVar[Settings | None] = ContextVar("muse_bridge_settings", default=None)


@contextmanager
def use_request_context(
    principal: Principal, store: InboxStore, settings: Settings
) -> Iterator[None]:
    principal_token = current_principal.set(principal)
    store_token = current_store.set(store)
    settings_token = current_settings.set(settings)
    try:
        yield
    finally:
        current_principal.reset(principal_token)
        current_store.reset(store_token)
        current_settings.reset(settings_token)
