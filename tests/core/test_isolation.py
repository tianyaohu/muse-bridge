"""One user cannot read, list, or delete another user's records."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from muse_bridge.auth.principal import FULL_SCOPES, Principal
from muse_bridge.clock import utcnow, utcnow_iso
from muse_bridge.context import use_request_context


def _principal(user_id: str) -> Principal:
    return Principal(user_id=user_id, auth_method="api_key", scopes=FULL_SCOPES, key_id="test")


def test_two_users_cannot_see_each_other(bridge, client):
    alice = bridge.state.key_store.create_key("alice")
    bob = bridge.state.key_store.create_key("bob")

    async def write(user_id: str, text: str, url: str) -> None:
        with use_request_context(_principal(user_id), bridge.state.store, bridge.state.settings):
            await bridge.state.mcp.call_tool(
                "submit_item",
                {"kind": "note", "payload": {"text": text}},
            )
            await bridge.state.mcp.call_tool(
                "report_job_finds",
                {"finds": [{"title": "Role", "company": "Co", "url": url, "notes": text}]},
            )
            await bridge.state.mcp.call_tool("send_message", {"text": text, "subject": user_id})

    asyncio.run(write("alice", "alice-secret", "https://example.com/jobs/same"))
    asyncio.run(write("bob", "bob-secret", "https://example.com/jobs/same"))

    alice_http = client.get("/api/inbox", headers={"Authorization": f"Bearer {alice.token}"})
    bob_http = client.get("/api/inbox", headers={"X-API-Key": bob.token})
    assert alice_http.status_code == 200
    assert bob_http.status_code == 200
    assert "alice-secret" in alice_http.text
    assert "bob-secret" not in alice_http.text
    assert "bob-secret" in bob_http.text
    assert "alice-secret" not in bob_http.text

    alice_file = (bridge.state.settings.data_dir / "users" / "alice" / "records.jsonl").read_text(encoding="utf-8")
    bob_file = (bridge.state.settings.data_dir / "users" / "bob" / "records.jsonl").read_text(encoding="utf-8")
    assert "bob-secret" not in alice_file
    assert "alice-secret" not in bob_file
    assert '"user_id": "alice"' in alice_file
    assert '"user_id": "bob"' in bob_file

    # The same URL is a duplicate only inside one user's inbox.
    assert alice_file.count("https://example.com/jobs/same") == 1
    assert bob_file.count("https://example.com/jobs/same") == 1

    async def wipe_alice() -> None:
        with use_request_context(_principal("alice"), bridge.state.store, bridge.state.settings):
            await bridge.state.mcp.call_tool("delete_my_data", {"confirm": "nope"})
            assert bridge.state.store.list_records("alice")
            await bridge.state.mcp.call_tool("delete_my_data", {"confirm": "delete"})

    asyncio.run(wipe_alice())
    assert bridge.state.store.list_records("alice") == []
    assert bridge.state.store.list_records("bob")
    assert "bob-secret" in bob_file


def test_pull_api_filters_do_not_cross_users(bridge, client):
    alice = bridge.state.key_store.create_key("alice")
    bob = bridge.state.key_store.create_key("bob")

    async def write() -> None:
        with use_request_context(_principal("alice"), bridge.state.store, bridge.state.settings):
            await bridge.state.mcp.call_tool(
                "send_message_to_nori",
                {"text": "alice-msg", "subject": "hi"},
            )
        with use_request_context(_principal("bob"), bridge.state.store, bridge.state.settings):
            await bridge.state.mcp.call_tool(
                "report_job_finds",
                {"finds": [{"title": "T", "company": "C", "url": "https://example.com/b"}]},
            )

    asyncio.run(write())
    finds = client.get(
        "/api/inbox",
        params={"type": "finds"},
        headers={"Authorization": f"Bearer {alice.token}"},
    )
    assert finds.json()["count"] == 0
    messages = client.get(
        "/api/inbox",
        params={"type": "messages"},
        headers={"Authorization": f"Bearer {alice.token}"},
    )
    assert messages.json()["count"] == 1
    assert messages.json()["records"][0]["text"] == "alice-msg"
    bob_finds = client.get(
        "/api/inbox",
        params={"type": "finds"},
        headers={"Authorization": f"Bearer {bob.token}"},
    )
    assert bob_finds.json()["count"] == 1
    assert "alice-msg" not in bob_finds.text


def test_retention_purge_is_per_record_and_user_scoped(bridge):
    bridge.state.store.append(
        "alice",
        {
            "id": "old",
            "kind": "note",
            "type": "note",
            "received_at": "2020-01-01T00:00:00+00:00",
            "status": "new",
            "payload": {"text": "stale"},
        },
    )
    bridge.state.store.append(
        "alice",
        {
            "id": "fresh",
            "kind": "note",
            "type": "note",
            "received_at": utcnow_iso(),
            "status": "new",
            "payload": {"text": "keep-alice"},
        },
    )
    bridge.state.store.append(
        "bob",
        {
            "id": "bob-fresh",
            "kind": "note",
            "type": "note",
            "received_at": utcnow_iso(),
            "status": "new",
            "payload": {"text": "keep-bob"},
        },
    )
    removed = bridge.state.store.purge_expired(30 * 86400, now=utcnow() + timedelta(seconds=5))
    assert removed == 1
    alice_ids = [row["id"] for row in bridge.state.store.list_records("alice")]
    bob_ids = [row["id"] for row in bridge.state.store.list_records("bob")]
    assert alice_ids == ["fresh"]
    assert bob_ids == ["bob-fresh"]


def test_admin_delete_user_leaves_the_other_user(bridge, tmp_path):
    bridge.state.key_store.create_key("alice")
    bridge.state.key_store.create_key("bob")
    bridge.state.store.append(
        "alice",
        {"id": "a", "kind": "note", "type": "note", "received_at": utcnow_iso(), "payload": {"text": "a"}},
    )
    bridge.state.store.append(
        "bob",
        {"id": "b", "kind": "note", "type": "note", "received_at": utcnow_iso(), "payload": {"text": "b"}},
    )
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["MUSE_BRIDGE_PORT"] = "8765"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "muse_bridge.admin",
            "--data-dir",
            str(bridge.state.settings.data_dir),
            "delete-user",
            "--user",
            "alice",
            "--yes",
        ],
        cwd="/workspace",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert bridge.state.key_store.list_keys(user_id="alice") == []
    assert bridge.state.store.list_records("alice") == []
    assert bridge.state.store.list_records("bob")[0]["id"] == "b"
    assert bridge.state.key_store.list_keys(user_id="bob")
