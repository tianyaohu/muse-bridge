"""OAuth 2.1 resource-server seam (MCP authorization + RFC 9728).

This process is a resource server. It is not an authorization server: there is
no ``/authorize``, token endpoint, or dynamic client registration. What ships
here:

* ``GET /.well-known/oauth-protected-resource`` (and the ``/mcp`` path variant)
  returns protected-resource metadata.
* ``OAuthBearerValidator`` is the bearer-validation interface. Plug in a
  verifier (JWT/JWKS or RFC 7662 introspection) that returns a ``Principal``.
  With no verifier, OAuth bearer tokens fail closed and API keys still work.
* 401 responses advertise the metadata URL in ``WWW-Authenticate``.

A follow-up can supply a real verifier once an issuer is chosen. Set
``MUSE_BRIDGE_OAUTH_ISSUER`` to advertise that issuer in the metadata.
"""

from __future__ import annotations

from typing import Protocol

from muse_bridge.auth.principal import Principal
from muse_bridge.ids import validate_user_id

SCOPES = ("inbox:read", "inbox:write")


class OAuthTokenVerifier(Protocol):
    async def __call__(
        self, token: str, *, resource: str | None, issuer: str
    ) -> Principal | None:
        """Validate an access token and return the resource owner, or None.

        ``Principal.user_id`` must be a bridge user id. ``Principal.scopes``
        should be a subset of ``inbox:read`` and ``inbox:write``. Set
        ``auth_method`` to ``"oauth"``.
        """


class OAuthBearerValidator:
    """Resource-server check for OAuth access tokens.

    ``verifier`` does the cryptographic or introspection work. This class
    refuses to guess: a missing verifier accepts nothing.
    """

    def __init__(self, issuer: str, verifier: OAuthTokenVerifier | None = None):
        self.issuer = issuer
        self.verifier = verifier

    async def validate(self, token: str, *, resource: str | None = None) -> Principal | None:
        if self.verifier is None or not token:
            return None
        principal = await self.verifier(token, resource=resource, issuer=self.issuer)
        if principal is None:
            return None
        try:
            user_id = validate_user_id(principal.user_id)
        except ValueError:
            return None
        return Principal(
            user_id=user_id,
            auth_method="oauth",
            scopes=frozenset(principal.scopes),
            key_id=principal.key_id,
            issuer=principal.issuer or self.issuer or None,
        )


def protected_resource_metadata(*, resource: str, issuer: str | None, server_title: str) -> dict:
    """RFC 9728 metadata, plus a ``muse_bridge`` object describing this build.

    ``authorization_servers`` is empty until ``MUSE_BRIDGE_OAUTH_ISSUER`` is
    set. Empty means OAuth is not configured; API keys remain the working
    bearer credential. Clients that require a non-empty list should wait for
    the issuer setting.
    """
    configured = bool(issuer)
    body: dict = {
        "resource": resource,
        "authorization_servers": [issuer] if configured else [],
        "bearer_methods_supported": ["header"],
        "scopes_supported": list(SCOPES),
        "resource_name": server_title,
        "muse_bridge": {
            "api_keys": True,
            "api_key_format": "mb_<prefix>_<secret>",
            "oauth": "advertised" if configured else "not_configured",
        },
    }
    if not configured:
        body["muse_bridge"]["note"] = (
            "Set MUSE_BRIDGE_OAUTH_ISSUER to advertise an authorization server. "
            "API keys via Authorization: Bearer or X-API-Key are validated locally. "
            "OAuthBearerValidator accepts a future access-token verifier."
        )
    return body


def www_authenticate(metadata_url: str) -> str:
    return (
        'Bearer realm="muse-bridge", '
        f'resource_metadata="{metadata_url}", '
        'error="invalid_token"'
    )
