"""Request rate and payload limits.

``muse_bridge.app`` installs these when ``MUSE_BRIDGE_LIMITS_ENABLED`` is on.
Both classes are constructed as ``Middleware(app)`` with no extra arguments.
Configuration is read from ``scope["app"].state.settings`` and the environment
on each request.

``PayloadLimitMiddleware`` is the outer middleware. An oversized body is
rejected before the rate limiter counts it. ``GET`` and ``HEAD`` on ``/healthz``
and ``/`` are not counted. Every other request, including failed auth, counts
against the presented credential. Callers with no credential share one bucket.
The client address is not a key: Muse, Cloudflare, and Fly share egress, and
``X-Forwarded-For`` is spoofable.

The outer middleware also turns an unhandled exception into a JSON error that
does not include the exception text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from muse_bridge.auth.authenticate import extract_credential, header_map

logger = logging.getLogger("muse_bridge.limits")

DEFAULT_RATE_LIMIT = 60
DEFAULT_RATE_WINDOW_SECONDS = 60
DEFAULT_MAX_BODY_BYTES = 1_048_576
_MAX_BUCKETS = 10_000
_EXEMPT_PATHS = frozenset({"/", "/healthz"})

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _now() -> float:
    return time.monotonic()


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(name) from exc
    if value < 1:
        raise ValueError(name)
    return value


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _settings(scope: Scope) -> Any:
    app = scope.get("app")
    return getattr(getattr(app, "state", None), "settings", None)


def _allow_query_key(scope: Scope) -> bool:
    settings = _settings(scope)
    if settings is not None:
        return bool(getattr(settings, "allow_query_key", False))
    return _flag("MUSE_BRIDGE_ALLOW_QUERY_KEY")


def _path(scope: Scope) -> str:
    path = scope.get("path") or "/"
    if not isinstance(path, str) or not path.startswith("/"):
        path = "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path


def _rate_exempt(scope: Scope) -> bool:
    method = str(scope.get("method", "GET")).upper()
    return method in {"GET", "HEAD"} and _path(scope) in _EXEMPT_PATHS


def _headers(scope: Scope) -> Mapping[str, str]:
    raw = scope.get("headers") or []
    try:
        return header_map(raw)
    except (UnicodeDecodeError, TypeError, ValueError):
        return {}


def _credential(scope: Scope) -> str:
    """Return the same credential auth would accept, without storing it."""
    raw_query = scope.get("query_string") or b""
    if isinstance(raw_query, bytes):
        query = raw_query.decode("latin-1")
    else:
        query = str(raw_query)
    try:
        token, _source = extract_credential(
            _headers(scope),
            query,
            allow_query_key=_allow_query_key(scope),
        )
    except Exception:
        logger.warning("could not read the request credential for rate limiting")
        return ""
    return token


def _bucket(scope: Scope) -> str:
    credential = _credential(scope)
    if not credential:
        return "anon"
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    return f"cred:{digest}"


def _content_length(scope: Scope) -> int | None:
    for key, value in _headers(scope).items():
        if key == "content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


async def _send_json(
    send: Send,
    status: int,
    payload: Mapping[str, Any],
    extra: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = _json_bytes(payload)
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if extra:
        headers.extend(extra)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _mcp_error(code: int, message: str, data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": code, "message": message, "data": dict(data)},
    }


def _is_mcp(scope: Scope) -> bool:
    return _path(scope) == "/mcp"


class RateLimitMiddleware:
    """Fixed window per credential. Anonymous callers share one bucket."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _rate_exempt(scope):
            await self.app(scope, receive, send)
            return
        try:
            limit = _positive_int("MUSE_BRIDGE_RATE_LIMIT", DEFAULT_RATE_LIMIT)
            window = _positive_int("MUSE_BRIDGE_RATE_WINDOW_SECONDS", DEFAULT_RATE_WINDOW_SECONDS)
        except ValueError as exc:
            await _send_json(send, 500, {"error": "invalid_configuration", "setting": str(exc)})
            return
        allowed, retry_after = self._allow(_bucket(scope), limit, window, _now())
        if allowed:
            await self.app(scope, receive, send)
            return
        retry = max(1, math.ceil(retry_after))
        data = {"limit": limit, "window_seconds": window, "retry_after_seconds": retry}
        if _is_mcp(scope):
            payload: Mapping[str, Any] = _mcp_error(-32029, "Rate limit exceeded", data)
        else:
            payload = {"error": "rate_limited", **data}
        await _send_json(send, 429, payload, [(b"retry-after", str(retry).encode("ascii"))])

    def _allow(self, bucket: str, limit: int, window: int, now: float) -> tuple[bool, float]:
        with self._lock:
            hits = [stamp for stamp in self._hits.get(bucket, []) if now - stamp < window]
            if len(hits) >= limit:
                self._hits[bucket] = hits
                return False, window - (now - hits[0])
            hits.append(now)
            if bucket not in self._hits and len(self._hits) >= _MAX_BUCKETS:
                self._evict(now, window)
            self._hits[bucket] = hits
            return True, 0.0

    def _evict(self, now: float, window: int) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] >= window]
        for key in stale:
            del self._hits[key]
        if len(self._hits) < _MAX_BUCKETS:
            return
        oldest = min(self._hits, key=lambda key: self._hits[key][-1])
        del self._hits[oldest]


class _TooLarge(Exception):
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes


class PayloadLimitMiddleware:
    """Reject bodies over the cap, then hide unexpected errors from clients."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self._guarded(scope, receive, tracking_send)
        except _TooLarge as exc:
            if started:
                raise
            await self._too_large(scope, send, exc.max_bytes)
        except Exception:
            if started:
                raise
            logger.exception("request failed")
            if _is_mcp(scope):
                payload: Mapping[str, Any] = _mcp_error(-32603, "Internal error", {})
            else:
                payload = {"error": "internal_error"}
            await _send_json(send, 500, payload)

    async def _guarded(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            max_bytes = _positive_int("MUSE_BRIDGE_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)
        except ValueError as exc:
            await _send_json(send, 500, {"error": "invalid_configuration", "setting": str(exc)})
            return
        declared = _content_length(scope)
        if declared is not None and declared > max_bytes:
            await self._too_large(scope, send, max_bytes)
            return
        # Buffer before the inner app runs. The rate limiter is inside this
        # middleware, so it must not see a body that is about to be rejected.
        chunks: list[bytes] = []
        seen = 0
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                break
            body = message.get("body") or b""
            if not isinstance(body, (bytes, bytearray)):
                body = bytes(body)
            seen += len(body)
            if seen > max_bytes:
                raise _TooLarge(max_bytes)
            chunks.append(bytes(body))
            if not message.get("more_body", False):
                break
        pending = b"".join(chunks)
        delivered = False

        async def replay() -> Message:
            nonlocal delivered, pending
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            body, pending = pending, b""
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Scope, send: Send, max_bytes: int) -> None:
        data = {"max_bytes": max_bytes}
        if _is_mcp(scope):
            payload: Mapping[str, Any] = _mcp_error(-32013, "Payload too large", data)
        else:
            payload = {"error": "payload_too_large", **data}
        await _send_json(send, 413, payload)
