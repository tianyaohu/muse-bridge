"""API keys, legacy token, query-key deprecation, and /healthz."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from muse_bridge.app import create_app
from muse_bridge.auth.keys import parse_api_key, secrets_equal
from muse_bridge.config import Settings


def test_healthz_is_open_and_unchanged(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "server": "nori-muse-bridge", "mcp": "/mcp"}
    posted = client.post("/healthz")
    assert posted.status_code == 200
    assert posted.json() == response.json()
    assert client.get("/").json() == response.json()


def test_inbox_requires_auth(client):
    response = client.get("/api/inbox")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert "resource_metadata" in response.headers["www-authenticate"]


def test_create_key_stores_hash_only_and_verifies(bridge):
    issued = bridge.state.key_store.create_key("alice", label="laptop")
    raw = (bridge.state.settings.data_dir / "keys.json").read_text(encoding="utf-8")
    assert issued.token not in raw
    assert issued.token.startswith(f"mb_{issued.prefix}_")
    assert parse_api_key(issued.token) == issued.prefix
    mode = stat.S_IMODE((bridge.state.settings.data_dir / "keys.json").stat().st_mode)
    assert mode == 0o600

    principal = bridge.state.key_store.verify(issued.token)
    assert principal is not None
    assert principal.user_id == "alice"
    assert principal.auth_method == "api_key"
    assert principal.key_id == issued.key_id
    assert "inbox:read" in principal.scopes
    assert "inbox:write" in principal.scopes

    flipped = issued.token[:-4] + ("abcd" if not issued.token.endswith("abcd") else "wxyz")
    assert bridge.state.key_store.verify(flipped) is None
    assert bridge.state.key_store.verify("mb_deadbeef_" + "x" * 32) is None

    infos = bridge.state.key_store.list_keys()
    assert infos[0].last_used_at
    assert infos[0].prefix == issued.prefix
    assert "secret_hash" not in repr(infos[0]) or issued.token not in repr(infos[0])


def test_revoked_key_is_rejected(bridge, client):
    issued = bridge.state.key_store.create_key("alice")
    assert client.get("/api/inbox", headers={"Authorization": f"Bearer {issued.token}"}).status_code == 200
    revoked = bridge.state.key_store.revoke(prefix=issued.prefix)
    assert revoked is not None and revoked.revoked_at
    again = bridge.state.key_store.revoke(key_id=issued.key_id)
    assert again is not None and again.revoked_at
    assert client.get("/api/inbox", headers={"Authorization": f"Bearer {issued.token}"}).status_code == 401
    assert client.get("/api/inbox", headers={"X-API-Key": issued.token}).status_code == 401


def test_query_key_is_ignored_unless_enabled(tmp_path):
    issued_app = create_app(Settings(data_dir=tmp_path / "off"))
    issued = issued_app.state.key_store.create_key("alice")
    with __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(issued_app) as http:
        denied = http.get("/api/inbox", params={"key": issued.token})
        assert denied.status_code == 401
        assert issued.token not in denied.text

    allowed_app = create_app(Settings(data_dir=tmp_path / "on", allow_query_key=True))
    allowed = allowed_app.state.key_store.create_key("alice")
    with __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(allowed_app) as http:
        ok = http.get("/api/inbox", params={"key": allowed.token})
        assert ok.status_code == 200
        assert ok.headers.get("deprecation") == "true"


def test_legacy_token_maps_to_one_user(tmp_path):
    app = create_app(
        Settings(
            data_dir=tmp_path / "legacy",
            legacy_single_token=True,
            legacy_token="shared-secret",
            legacy_user="frank",
        )
    )
    with __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(app) as http:
        missing = http.get("/api/inbox", headers={"Authorization": "Bearer shared-secret"})
        assert missing.status_code == 200
        assert http.get("/api/inbox", headers={"Authorization": "Bearer no"}).status_code == 401
    from muse_bridge.context import use_request_context
    from muse_bridge.auth.principal import FULL_SCOPES, Principal
    import asyncio

    async def write() -> None:
        principal = Principal(user_id="frank", auth_method="legacy_token", scopes=FULL_SCOPES)
        with use_request_context(principal, app.state.store, app.state.settings):
            await app.state.mcp.call_tool(
                "submit_item",
                {"kind": "note", "payload": {"text": "frank-only"}},
            )

    asyncio.run(write())
    assert "frank-only" in (tmp_path / "legacy" / "users" / "frank" / "records.jsonl").read_text()


def test_secrets_equal_accepts_unequal_lengths():
    assert secrets_equal("same-token", "same-token") is True
    assert secrets_equal("short", "a-much-longer-token") is False
    assert secrets_equal("present", "") is False


def test_rejects_path_like_user_ids(bridge):
    with __import__("pytest").raises(ValueError):
        bridge.state.key_store.create_key("../alice")
    with __import__("pytest").raises(ValueError):
        bridge.state.key_store.create_key("alice/bob")


def test_oauth_metadata_and_verifier(tmp_path):
    from muse_bridge.auth.principal import SCOPE_READ, Principal

    async def verifier(token: str, *, resource: str | None, issuer: str) -> Principal | None:
        assert resource.endswith("/mcp")
        assert issuer == "https://auth.example.com"
        if token == "read-only":
            return Principal(user_id="alice", auth_method="oauth", scopes=frozenset({SCOPE_READ}), issuer=issuer)
        return None

    app = create_app(
        Settings(
            data_dir=tmp_path / "oauth",
            public_url="https://bridge.example.com",
            oauth_issuer="https://auth.example.com",
        ),
        oauth_verifier=verifier,
    )
    with __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(app) as http:
        meta = http.get("/.well-known/oauth-protected-resource")
        assert meta.status_code == 200
        body = meta.json()
        assert body["resource"] == "https://bridge.example.com/mcp"
        assert body["authorization_servers"] == ["https://auth.example.com"]
        assert body["bearer_methods_supported"] == ["header"]
        assert http.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == body["resource"]
        unconfigured = create_app(Settings(data_dir=tmp_path / "plain", public_url="https://bridge.example.com"))
        with __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(unconfigured) as plain:
            assert plain.get("/.well-known/oauth-protected-resource").json()["authorization_servers"] == []
        read = http.get("/api/inbox", headers={"Authorization": "Bearer read-only"})
        assert read.status_code == 200
        denied = http.delete("/api/inbox", params={"confirm": "delete"}, headers={"Authorization": "Bearer read-only"})
        assert denied.status_code == 403
        assert http.get("/api/inbox", headers={"Authorization": "Bearer other"}).status_code == 401


def test_limits_hook_imports_are_guarded(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "limits", limits_enabled=True))
    assert app.state.settings.limits_enabled is True


def test_admin_create_key_cli(tmp_path):
    env = os.environ.copy()
    env["MUSE_BRIDGE_DATA_DIR"] = str(tmp_path / "data")
    env["MUSE_BRIDGE_LEGACY_SINGLE_TOKEN"] = "0"
    env["MUSE_BRIDGE_PORT"] = "8765"
    result = subprocess.run(
        [sys.executable, "-m", "muse_bridge.admin", "create-key", "--user", "alice"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    token_line = next(line for line in result.stdout.splitlines() if line.startswith("token: "))
    token = token_line.split(": ", 1)[1]
    assert token.startswith("mb_")
    assert token not in (tmp_path / "data" / "keys.json").read_text(encoding="utf-8")
    listed = subprocess.run(
        [sys.executable, "-m", "muse_bridge.admin", "--data-dir", str(tmp_path / "data"), "list-keys"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0
    assert "alice" in listed.stdout
    assert token not in listed.stdout
