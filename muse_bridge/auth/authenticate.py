"""Resolve a presented bearer credential to a Principal.

Order:

1. Tokens shaped like ``mb_<prefix>_<secret>`` are API keys only. A miss or a
   revoked key does not fall through to the legacy token or OAuth.
2. Otherwise, if legacy single-token mode is on, compare the shared token.
3. Otherwise, if an OAuth verifier is configured, ask it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from urllib.parse import parse_qs

from muse_bridge.auth.keys import KeyStore, parse_api_key, secrets_equal
from muse_bridge.auth.oauth import OAuthBearerValidator
from muse_bridge.auth.principal import FULL_SCOPES, Principal
from muse_bridge.config import Settings
from muse_bridge.ids import validate_user_id

logger = logging.getLogger("muse_bridge.auth")

_QUERY_IGNORED = False
_QUERY_ACCEPTED = False


def header_map(scope_headers: list[tuple[bytes, bytes]] | None) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for key, value in scope_headers or []:
        mapped[key.decode("latin1").lower()] = value.decode("latin1")
    return mapped


def extract_credential(
    headers: Mapping[str, str],
    query_string: str,
    *,
    allow_query_key: bool,
) -> tuple[str, str]:
    """Return ``(token, source)``.

    ``source`` is ``bearer``, ``x-api-key``, ``query``, or ``""``.
    ``?key=`` is deprecated. It is ignored unless ``allow_query_key`` is set,
    and it is never written to the log.
    """
    global _QUERY_IGNORED, _QUERY_ACCEPTED
    token = ""
    source = ""
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            source = "bearer"
    if not token:
        api_key = headers.get("x-api-key", "").strip()
        if api_key:
            token = api_key
            source = "x-api-key"
    query_values = parse_qs(query_string, keep_blank_values=False).get("key") or []
    query_token = query_values[0] if query_values else ""
    if not token and query_token:
        if allow_query_key:
            token = query_token
            source = "query"
            if not _QUERY_ACCEPTED:
                _QUERY_ACCEPTED = True
                logger.warning(
                    "accepted a deprecated ?key= credential; send Authorization: Bearer or X-API-Key"
                )
        elif not _QUERY_IGNORED:
            _QUERY_IGNORED = True
            logger.warning(
                "ignored a deprecated ?key= credential; send Authorization: Bearer or X-API-Key"
            )
    return token, source


async def authenticate_bearer(
    token: str,
    *,
    settings: Settings,
    key_store: KeyStore,
    oauth: OAuthBearerValidator | None,
    resource: str | None = None,
) -> Principal | None:
    if not token or len(token) > 8192:
        return None
    if parse_api_key(token):
        return key_store.verify(token)
    if settings.legacy_single_token and secrets_equal(token, settings.legacy_token):
        return Principal(
            user_id=validate_user_id(settings.legacy_user),
            auth_method="legacy_token",
            scopes=FULL_SCOPES,
            key_id=None,
        )
    if oauth is not None:
        return await oauth.validate(token, resource=resource)
    return None
