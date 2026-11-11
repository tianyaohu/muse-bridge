"""Rate and payload limits, including both auth modes.

The middleware is constructed the way ``muse_bridge.app`` constructs it:
``Middleware(app)`` with no keyword arguments. Limits come from the environment
and from ``scope["app"].state.settings``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from muse_bridge.app import create_app
from muse_bridge.config import Settings
from muse_bridge.limits import PayloadLimitMiddleware, RateLimitMiddleware

ROOT = Path(__file__).resolve().parents[1]
HEALTH = {"ok": True, "server": "nori-muse-bridge", "mcp": "/mcp"}


@pytest.fixture
def limit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "2")
    monkeypatch.setenv("MUSE_BRIDGE_RATE_WINDOW_SECONDS", "60")
    monkeypatch.setenv("MUSE_BRIDGE_MAX_BODY_BYTES", "1024")


def _app(tmp_path: Path, **kwargs: object) -> Starlette:
    settings = Settings(data_dir=tmp_path / "data", limits_enabled=True, **kwargs)  # type: ignore[arg-type]
    return create_app(settings)


def _issue(app: Starlette, user: str) -> str:
    return app.state.key_store.create_key(user).token


def _auth(token: str, scheme: str = "bearer") -> dict[str, str]:
    if scheme == "x-api-key":
        return {"X-API-Key": token}
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public_and_not_rate_limited(
    tmp_path: Path, limit_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    app = _app(tmp_path)
    with TestClient(app) as client:
        for _ in range(5):
            health = client.get("/healthz")
            assert health.status_code == 200
            assert health.json() == HEALTH
            root = client.head("/")
            assert root.status_code == 200
        # The anonymous bucket is full. Probes still do not count.
        denied = client.get("/api/inbox")
        assert denied.status_code == 401
        again = client.get("/api/inbox")
        assert again.status_code == 429
        assert client.get("/healthz").json() == HEALTH
        assert client.head("/healthz").status_code == 200


def test_limits_stay_off_unless_the_flag_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    app = create_app(Settings(data_dir=tmp_path / "data", limits_enabled=False))
    with TestClient(app) as client:
        first = client.get("/api/inbox")
        second = client.get("/api/inbox")
    assert first.status_code == 401
    assert second.status_code == 401


def test_per_key_and_legacy_buckets_are_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    app = _app(
        tmp_path,
        legacy_single_token=True,
        legacy_token="legacy-shared-token",
        legacy_user="legacy",
    )
    alice = _issue(app, "alice")
    bob = _issue(app, "bob")
    with TestClient(app) as client:
        assert client.get("/api/inbox", headers=_auth(alice)).status_code == 200
        limited = client.get("/api/inbox", headers=_auth(alice, "x-api-key"))
        assert limited.status_code == 429
        body = limited.json()
        assert body["error"] == "rate_limited"
        assert alice not in limited.text
        assert "legacy-shared-token" not in limited.text
        assert limited.headers["retry-after"] == "60"
        assert client.get("/api/inbox", headers=_auth(bob)).status_code == 200
        assert client.get("/api/inbox", headers=_auth("legacy-shared-token")).status_code == 200
        assert client.get("/api/inbox").status_code == 401
        assert client.get("/api/inbox").status_code == 429


def test_mcp_rate_limit_is_jsonrpc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    app = _app(tmp_path)
    token = _issue(app, "alice")
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "ping", "arguments": {}},
    }
    with TestClient(app) as client:
        first = client.post("/mcp", headers=_auth(token), json=payload)
        second = client.post("/mcp", headers=_auth(token), json=payload)
    assert first.status_code != 429
    assert second.status_code == 429
    error = second.json()["error"]
    assert error["code"] == -32029
    assert error["message"] == "Rate limit exceeded"
    assert error["data"]["limit"] == 1
    assert token not in second.text


def test_query_key_uses_the_credential_bucket_only_when_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    blocked = _app(tmp_path, allow_query_key=False)
    token = _issue(blocked, "alice")
    with TestClient(blocked) as client:
        ignored = client.get("/api/inbox", params={"key": token})
        assert ignored.status_code == 401
        assert client.get("/api/inbox", headers=_auth(token)).status_code == 200

    allowed = _app(tmp_path, allow_query_key=True)
    other = _issue(allowed, "alice")
    with TestClient(allowed) as client:
        assert client.get("/api/inbox", params={"key": other}).status_code == 200
        again = client.get("/api/inbox", headers=_auth(other))
        assert again.status_code == 429
        assert other not in again.text


def test_oversized_body_does_not_consume_a_rate_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    monkeypatch.setenv("MUSE_BRIDGE_MAX_BODY_BYTES", "64")
    app = _app(tmp_path)
    token = _issue(app, "alice")
    with TestClient(app) as client:
        huge = client.post(
            "/mcp",
            headers={**_auth(token), "content-type": "application/json"},
            content=b'{"jsonrpc":"2.0","id":1,"method":"ping"}' + b" " * 200,
        )
        assert huge.status_code == 413
        assert huge.json()["error"]["code"] == -32013
        assert huge.json()["error"]["data"]["max_bytes"] == 64
        assert token not in huge.text
        assert client.get("/api/inbox", headers=_auth(token)).status_code == 200
        health = client.post("/healthz", content=b"x" * 80)
        assert health.status_code == 413
        assert health.json()["error"] == "payload_too_large"


def test_window_resets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    monkeypatch.setenv("MUSE_BRIDGE_RATE_WINDOW_SECONDS", "10")
    clock = {"now": 1_000.0}

    def _now() -> float:
        return clock["now"]

    monkeypatch.setattr("muse_bridge.limits._now", _now)
    app = _app(tmp_path)
    token = _issue(app, "alice")
    with TestClient(app) as client:
        assert client.get("/api/inbox", headers=_auth(token)).status_code == 200
        assert client.get("/api/inbox", headers=_auth(token)).status_code == 429
        clock["now"] += 11
        assert client.get("/api/inbox", headers=_auth(token)).status_code == 200


def test_invalid_limit_fails_closed_without_echoing_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "not-a-number")
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/inbox")
        assert client.get("/healthz").status_code == 200
    assert response.status_code == 500
    assert response.json()["error"] == "invalid_configuration"
    assert response.json()["setting"] == "MUSE_BRIDGE_RATE_LIMIT"
    assert "not-a-number" not in response.text


def test_admin_create_key_is_rate_limited_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    env = os.environ.copy()
    env["MUSE_BRIDGE_DATA_DIR"] = str(data)
    env["MUSE_BRIDGE_LEGACY_SINGLE_TOKEN"] = "0"
    env.pop("MUSE_BRIDGE_TOKEN", None)
    env["MUSE_BRIDGE_RATE_LIMIT"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "muse_bridge.admin", "create-key", "--user", "carol"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    token = ""
    for line in completed.stdout.splitlines():
        if line.startswith("token:"):
            token = line.split(":", 1)[1].strip()
    assert token.startswith("mb_")
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    app = create_app(Settings(data_dir=data, limits_enabled=True))
    with TestClient(app) as client:
        assert client.get("/api/inbox", headers=_auth(token)).status_code == 200
        limited = client.get("/api/inbox", headers=_auth(token))
    assert limited.status_code == 429
    assert token not in limited.text


def test_payload_middleware_hides_unexpected_errors() -> None:
    async def explode(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        raise RuntimeError("mb_alice_secretvalue leaked from /data/private/inbox")

    app = PayloadLimitMiddleware(explode)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/mcp")
    assert response.status_code == 500
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32603, "message": "Internal error", "data": {}},
    }
    assert "secretvalue" not in response.text
    assert "Traceback" not in response.text
    assert "/data/private" not in response.text


def test_chunked_body_over_the_cap_is_rejected_before_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_MAX_BODY_BYTES", "8")
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    calls = {"n": 0}

    async def inner(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            return
        while True:
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                break
        calls["n"] += 1
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    app = PayloadLimitMiddleware(RateLimitMiddleware(inner))

    async def once(chunks: list[bytes]) -> tuple[int, bytes]:
        remaining = list(chunks)

        async def receive():  # type: ignore[no-untyped-def]
            if not remaining:
                return {"type": "http.request", "body": b"", "more_body": False}
            chunk = remaining.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(remaining)}

        messages: list[dict] = []

        async def send(message):  # type: ignore[no-untyped-def]
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        }
        await app(scope, receive, send)
        status = next(
            message["status"] for message in messages if message["type"] == "http.response.start"
        )
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return status, body

    status, body = asyncio.run(once([b"12345", b"67890"]))
    assert status == 413
    assert calls["n"] == 0
    assert b"Payload too large" in body
    status, body = asyncio.run(once([b"tiny"]))
    assert status == 401
    assert calls["n"] == 1


def test_bad_authorization_bytes_are_unauthorized(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))

    async def call() -> tuple[int, bytes]:
        messages: list[dict] = []

        async def receive():  # type: ignore[no-untyped-def]
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):  # type: ignore[no-untyped-def]
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/inbox",
            "raw_path": b"/api/inbox",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer \xff\xfe")],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        }
        await app(scope, receive, send)
        status = next(
            message["status"] for message in messages if message["type"] == "http.response.start"
        )
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return status, body

    status, body = asyncio.run(call())
    assert status == 401
    assert b"unauthorized" in body
    assert b"Traceback" not in body


def test_route_exception_text_is_not_returned(tmp_path: Path) -> None:
    async def boom(request):  # type: ignore[no-untyped-def]
        raise RuntimeError("mb_alice_secretvalue /data/private/inbox")

    app = Starlette(routes=[Route("/boom", boom)])
    app.add_middleware(PayloadLimitMiddleware)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert "secretvalue" not in response.text
    assert "Traceback" not in response.text


def test_middleware_order_matches_the_hook(tmp_path: Path, limit_env: None) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == HEALTH
    names: list[str] = []
    node = app.middleware_stack
    while node is not None:
        names.append(type(node).__name__)
        node = getattr(node, "app", None)
    assert names.index("PayloadLimitMiddleware") < names.index("RateLimitMiddleware")


def test_non_mcp_rate_limit_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSE_BRIDGE_RATE_LIMIT", "1")
    app = _app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/healthz").status_code == 200
        limited = client.post("/healthz")
    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"
    assert "jsonrpc" not in limited.text
