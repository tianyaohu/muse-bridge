"""Per-user authentication: API keys, optional legacy token, OAuth seam."""

from muse_bridge.auth.keys import IssuedKey, KeyInfo, KeyStore
from muse_bridge.auth.oauth import OAuthBearerValidator, OAuthTokenVerifier
from muse_bridge.auth.principal import FULL_SCOPES, SCOPE_READ, SCOPE_WRITE, Principal

__all__ = [
    "FULL_SCOPES",
    "IssuedKey",
    "KeyInfo",
    "KeyStore",
    "OAuthBearerValidator",
    "OAuthTokenVerifier",
    "Principal",
    "SCOPE_READ",
    "SCOPE_WRITE",
]
