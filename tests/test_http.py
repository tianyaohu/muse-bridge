"""HTTP and tool behavior through the app factory.

Network stays mocked. These tests do not change ``tests/core``.
"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from muse_bridge.app import create_app
from muse_bridge.config import Settings

HEALTH = {"ok": True, "server": "nori-muse-bridge", "mcp": "/mcp"}


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            legacy_single_token=True,
            legacy_token="legacy-shared-token",
        )
    )
    client = TestClient(app)
    client.__enter__()
    return client, "legacy-shared-token"


def _tool(client: TestClient, token: str, name: str, arguments: dict) -> str:
    response = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    text = body["result"]["content"][0]["text"]
    assert isinstance(text, str)
    return text


def test_healthz_root_and_ping(tmp_path: Path) -> None:
    client, token = _client(tmp_path)
    try:
        assert client.get("/healthz").json() == HEALTH
        assert client.get("/").json() == HEALTH
        assert client.post("/healthz").json() == HEALTH
        text = _tool(client, token, "ping", {})
        assert '"ok": true' in text or '"ok":true' in text
    finally:
        client.__exit__(None, None, None)


def test_job_find_round_trip_hides_the_data_directory(tmp_path: Path) -> None:
    client, token = _client(tmp_path)
    data_dir = str(tmp_path / "data")
    try:
        stored = _tool(
            client,
            token,
            "report_job_finds",
            {
                "finds": [
                    {
                        "title": "Intern",
                        "company": None,
                        "url": "https://example.com/jobs/bridge",
                        "notes": "x" * 5000,
                    }
                ],
                "source": "s" * 500,
            },
        )
        assert "stored" in stored
        assert data_dir not in stored
        listed = _tool(client, token, "list_recent_finds", {"limit": 5, "company": "none"})
        assert "Intern" in listed or "count" in listed
        inbox = client.get("/api/inbox", headers={"Authorization": f"Bearer {token}"})
        assert inbox.status_code == 200
        assert data_dir not in inbox.text
        assert "https://example.com/jobs/bridge" in inbox.text
    finally:
        client.__exit__(None, None, None)
