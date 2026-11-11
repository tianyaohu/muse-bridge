"""Authenticated caller. Scopes are hints the HTTP API and tools enforce."""

from __future__ import annotations

from dataclasses import dataclass

# Full access for API keys and the legacy single-user token. OAuth verifiers
# can return a narrower set (inbox:read without inbox:write).
SCOPE_READ = "inbox:read"
SCOPE_WRITE = "inbox:write"
FULL_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE})


@dataclass(frozen=True)
class Principal:
    user_id: str
    auth_method: str
    scopes: frozenset[str]
    key_id: str | None = None
    issuer: str | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
