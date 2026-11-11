"""ASGI app: MCP Streamable HTTP, ``/healthz``, and the pull API.

Entrypoints (stable for middleware and process managers):

* factory: ``muse_bridge.app:create_app``
* module app: ``muse_bridge.app:app``
* process: ``python server.py`` or ``python -m muse_bridge``

``/healthz`` and ``/`` stay unauthenticated and return
``{"ok": true, "server": "<name>", "mcp": "/mcp"}``. The default name is
``nori-muse-bridge``, which keeps that body the same as the original server.

The limits hook at the bottom of ``create_app`` is where
``RateLimitMiddleware`` and ``PayloadLimitMiddleware`` from ``muse_bridge.limits``
attach when ``MUSE_BRIDGE_LIMITS_ENABLED`` is set. The import is guarded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from muse_bridge.auth.authenticate import authenticate_bearer, extract_credential, header_map
from muse_bridge.auth.oauth import (
    OAuthBearerValidator,
    OAuthTokenVerifier,
    protected_resource_metadata,
    www_authenticate,
)
from muse_bridge.auth.principal import SCOPE_READ, SCOPE_WRITE, Principal
from muse_bridge.config import Settings, load_settings
from muse_bridge.context import use_request_context
from muse_bridge.domain import delete_my_data, pull_inbox
from muse_bridge.tools import register_tools
from muse_bridge.wiring import inbox_for, key_store_for

logger = logging.getLogger("muse_bridge")

_LEGACY_HINT_LOGGED = False


class _Health:
    """Any method on ``/healthz`` and ``/`` returns the same JSON body, with no auth."""

    def __init__(self, server_name: str):
        self._body = {"ok": True, "server": server_name, "mcp": "/mcp"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        await JSONResponse(self._body)(scope, receive, send)


class _AuthenticatedMCP:
    """Gate the MCP app. ``/healthz`` never reaches this mount."""

    def __init__(self, app, settings: Settings, key_store, store, oauth: OAuthBearerValidator):
        self.app = app
        self.settings = settings
        self.key_store = key_store
        self.store = store
        self.oauth = oauth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = header_map(scope.get("headers"))
        query = scope.get("query_string", b"").decode("latin1")
        token, source = extract_credential(
            headers, query, allow_query_key=self.settings.allow_query_key
        )
        origin = public_origin(self.settings, headers, scope.get("scheme", "http"))
        principal = await authenticate_bearer(
            token,
            settings=self.settings,
            key_store=self.key_store,
            oauth=self.oauth,
            resource=origin + "/mcp",
        )
        if principal is None:
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": www_authenticate(origin + "/.well-known/oauth-protected-resource")},
            )
            await response(scope, receive, send)
            return

        async def send_with_deprecation(message: dict) -> None:
            if source == "query" and message["type"] == "http.response.start":
                extra = list(message.get("headers") or [])
                extra.append((b"deprecation", b"true"))
                message = {**message, "headers": extra}
            await send(message)

        with use_request_context(principal, self.store, self.settings):
            await self.app(scope, receive, send_with_deprecation)


def public_origin(settings: Settings, headers: Mapping[str, str], scheme: str) -> str:
    if settings.public_url:
        return settings.public_url.rstrip("/")
    proto = (headers.get("x-forwarded-proto") or scheme or "http").split(",")[0].strip() or "http"
    host = (headers.get("x-forwarded-host") or headers.get("host") or "localhost").split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _warn_configuration(settings: Settings) -> None:
    global _LEGACY_HINT_LOGGED
    if settings.legacy_single_token and not settings.legacy_token:
        logger.error(
            "MUSE_BRIDGE_LEGACY_SINGLE_TOKEN is on but MUSE_BRIDGE_TOKEN is empty. "
            "Refusing to serve until the token is set."
        )
    elif settings.legacy_token and not settings.legacy_single_token and not _LEGACY_HINT_LOGGED:
        _LEGACY_HINT_LOGGED = True
        logger.warning(
            "MUSE_BRIDGE_TOKEN is set but MUSE_BRIDGE_LEGACY_SINGLE_TOKEN is off, "
            "so the shared token is not accepted. Set MUSE_BRIDGE_LEGACY_SINGLE_TOKEN=1 "
            "to keep the original single-user setup."
        )


def _build_mcp(settings: Settings) -> MCPServer:
    mcp = MCPServer(
        name=settings.server_name,
        title=settings.server_title,
        version=settings.version,
        instructions=settings.instructions,
    )
    register_tools(mcp, settings)
    return mcp


def _install_limits_middleware(app: Starlette, settings: Settings) -> None:
    # LIMITS HOOK: RateLimitMiddleware and PayloadLimitMiddleware from muse_bridge.limits.
    # Enabled with MUSE_BRIDGE_LIMITS_ENABLED=1. The import is guarded so this
    # app runs when muse_bridge.limits does not exist yet.
    #
    # Constructors are called as Middleware(app) with no extra kwargs. Read
    # configuration from the environment or from scope["app"].state.settings
    # inside __call__. PayloadLimitMiddleware is added last so it is the outer
    # user middleware and rejects oversized bodies before rate accounting.
    # /healthz is inside this app; exempt it in the middleware if probes must
    # not count toward the cap.
    if not settings.limits_enabled:
        return
    try:
        from muse_bridge.limits import PayloadLimitMiddleware, RateLimitMiddleware
    except ImportError:
        logger.warning(
            "MUSE_BRIDGE_LIMITS_ENABLED is set but muse_bridge.limits is not installed; "
            "starting without rate or payload middleware"
        )
        return
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(PayloadLimitMiddleware)


def create_app(
    settings: Settings | None = None,
    *,
    oauth_verifier: OAuthTokenVerifier | None = None,
) -> Starlette:
    """Build a new app. Each call has its own inbox, key store, and MCP server.

    Pass ``oauth_verifier`` to validate OAuth access tokens. With no verifier,
    OAuth bearer tokens fail closed and API keys still work. See
    ``muse_bridge.auth.oauth``.
    """
    settings = settings if settings is not None else load_settings()
    _warn_configuration(settings)
    store = inbox_for(settings)
    key_store = key_store_for(settings)
    oauth = OAuthBearerValidator(settings.oauth_issuer, oauth_verifier)
    mcp = _build_mcp(settings)
    mcp_http = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        # Muse connects through a tunnel or a public host. DNS-rebinding
        # protection would reject that Host header, so it stays off.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host="0.0.0.0",
    )
    # Public property on the low-level server. streamable_http_app() sets it.
    # The outer lifespan starts it once. Mounted apps do not run their own
    # lifespan in this Starlette version, so the inner lifespan is not used.
    session_manager = mcp._lowlevel_server.session_manager

    async def _purge_loop() -> None:
        while True:
            await asyncio.sleep(settings.purge_interval_seconds)
            if settings.retention_days <= 0:
                continue
            try:
                removed = await asyncio.to_thread(store.purge_expired, settings.retention_days * 86400)
                if removed:
                    logger.info("retention purge removed %s record(s)", removed)
            except Exception:
                logger.exception("retention purge failed")

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        if settings.legacy_single_token and not settings.legacy_token:
            raise RuntimeError(
                "MUSE_BRIDGE_LEGACY_SINGLE_TOKEN is enabled but MUSE_BRIDGE_TOKEN is empty. "
                "Refusing to run unauthenticated."
            )
        async with session_manager.run():
            task: asyncio.Task[None] | None = None
            if settings.purge_interval_seconds > 0:
                task = asyncio.create_task(_purge_loop())
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    health = _Health(settings.server_name)
    gated = _AuthenticatedMCP(mcp_http, settings, key_store, store, oauth)

    async def oauth_metadata(request: Request) -> Response:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Cache-Control": "public, max-age=3600",
        }
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=headers)
        origin = public_origin(settings, request.headers, request.url.scheme)
        body = protected_resource_metadata(
            resource=origin + "/mcp",
            issuer=settings.oauth_issuer or None,
            server_title=settings.server_title,
        )
        return JSONResponse(body, headers=headers)

    async def inbox(request: Request) -> Response:
        headers_map = {key.lower(): value for key, value in request.headers.items()}
        token, source = extract_credential(
            headers_map,
            request.url.query,
            allow_query_key=settings.allow_query_key,
        )
        origin = public_origin(settings, headers_map, request.url.scheme)
        principal = await authenticate_bearer(
            token,
            settings=settings,
            key_store=key_store,
            oauth=oauth,
            resource=origin + "/mcp",
        )
        extra: dict[str, str] = {}
        if source == "query":
            extra["Deprecation"] = "true"
        if principal is None:
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={
                    "WWW-Authenticate": www_authenticate(origin + "/.well-known/oauth-protected-resource"),
                    **extra,
                },
            )
        if request.method == "GET":
            return _inbox_get(request, principal, store, extra)
        return _inbox_delete(request, principal, store, extra)

    app = Starlette(
        routes=[
            Route("/healthz", health, name="healthz"),
            Route("/", health, name="root"),
            Route(
                "/.well-known/oauth-protected-resource",
                oauth_metadata,
                methods=["GET", "OPTIONS"],
                name="oauth-protected-resource",
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp",
                oauth_metadata,
                methods=["GET", "OPTIONS"],
                name="oauth-protected-resource-mcp",
            ),
            Route("/api/inbox", inbox, methods=["GET", "DELETE"], name="inbox"),
            Mount("/", app=gated),
        ],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.key_store = key_store
    app.state.mcp = mcp
    app.state.oauth = oauth
    _install_limits_middleware(app, settings)
    return app


def _inbox_get(request: Request, principal: Principal, store, extra: dict[str, str]) -> JSONResponse:
    if not principal.has_scope(SCOPE_READ):
        return JSONResponse({"error": "forbidden"}, status_code=403, headers=extra)
    try:
        body = pull_inbox(
            store,
            principal.user_id,
            since=request.query_params.get("since", ""),
            type_filter=request.query_params.get("type", "all"),
            kind=request.query_params.get("kind", ""),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, headers=extra)
    return JSONResponse(body, headers=extra)


def _inbox_delete(request: Request, principal: Principal, store, extra: dict[str, str]) -> JSONResponse:
    if not principal.has_scope(SCOPE_WRITE):
        return JSONResponse({"error": "forbidden"}, status_code=403, headers=extra)
    result = delete_my_data(
        store,
        principal.user_id,
        request.query_params.get("confirm", ""),
        kind=request.query_params.get("kind", ""),
        before=request.query_params.get("before", ""),
        ids=request.query_params.getlist("id"),
    )
    status = 400 if result.get("error") else 200
    return JSONResponse(result, status_code=status, headers=extra)


def main() -> None:
    settings = load_settings()
    if settings.legacy_single_token and not settings.legacy_token:
        raise SystemExit(
            "MUSE_BRIDGE_LEGACY_SINGLE_TOKEN is enabled but MUSE_BRIDGE_TOKEN is empty. "
            "Refusing to run unauthenticated."
        )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


# Module-level app for `uvicorn muse_bridge.app:app`. Built from the environment
# at import. Tests should call create_app(Settings(...)) instead of using this.
app = create_app()
