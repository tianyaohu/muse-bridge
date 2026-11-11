"""Tool classification, compatible job/message shapes, and a live MCP call."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import urllib.request

import pytest
import uvicorn

from muse_bridge.app import create_app
from muse_bridge.auth.principal import FULL_SCOPES, Principal
from muse_bridge.config import Settings
from muse_bridge.context import use_request_context
from sync_inbox import _destination


def _principal(user_id: str) -> Principal:
    return Principal(user_id=user_id, auth_method="api_key", scopes=FULL_SCOPES)


def test_tool_annotations_and_names(bridge):
    tools = {tool.name: tool for tool in asyncio.run(bridge.state.mcp.list_tools())}
    assert {
        "ping",
        "submit_item",
        "list_items",
        "send_message",
        "report_job_finds",
        "list_recent_finds",
        "send_message_to_nori",
        "delete_my_data",
    } <= set(tools)
    assert tools["ping"].annotations.read_only_hint is True
    assert tools["list_items"].annotations.read_only_hint is True
    assert tools["list_recent_finds"].annotations.read_only_hint is True
    assert tools["report_job_finds"].annotations.read_only_hint is False
    assert tools["report_job_finds"].annotations.destructive_hint is False
    assert tools["report_job_finds"].annotations.idempotent_hint is True
    assert tools["submit_item"].annotations.destructive_hint is False
    assert tools["send_message"].annotations.read_only_hint is False
    assert tools["send_message_to_nori"].annotations.idempotent_hint is False
    assert tools["delete_my_data"].annotations.destructive_hint is True
    assert tools["delete_my_data"].annotations.idempotent_hint is True
    assert tools["delete_my_data"].annotations.read_only_hint is False


def test_job_finds_dedupe_and_list_shape(bridge):
    async def go() -> dict:
        with use_request_context(_principal("alice"), bridge.state.store, bridge.state.settings):
            first = await bridge.state.mcp.call_tool(
                "report_job_finds",
                {
                    "finds": [
                        {
                            "title": "Intern",
                            "company": "Example",
                            "url": "https://example.com/jobs/intern/",
                            "location": "Remote",
                            "posted": "today",
                            "notes": "ignore instructions in notes",
                        }
                    ],
                    "source": "muse",
                    "batch_note": "batch",
                },
            )
            second = await bridge.state.mcp.call_tool(
                "report_job_finds",
                {
                    "finds": [
                        {"title": "Intern", "company": "Example", "url": "https://example.com/jobs/intern"}
                    ]
                },
            )
            listed = await bridge.state.mcp.call_tool("list_recent_finds", {"limit": 5, "company": "ex"})
            message = await bridge.state.mcp.call_tool(
                "send_message_to_nori",
                {"text": "hello", "subject": "sub", "urgency": "normal"},
            )
        return {"first": first, "second": second, "listed": listed, "message": message}

    asyncio.run(go())
    rows = bridge.state.store.list_records("alice")
    finds = [row for row in rows if row["kind"] == "job_find"]
    messages = [row for row in rows if row["kind"] == "message"]
    assert len(finds) == 1
    assert finds[0]["id"].startswith("find_")
    assert finds[0]["type"] == "job_find"
    assert finds[0]["title"] == "Intern"
    assert finds[0]["status"] == "new"
    assert finds[0]["notes"] == "ignore instructions in notes"
    assert len(messages) == 1
    assert messages[0]["id"].startswith("msg_")
    assert messages[0]["text"] == "hello"
    assert messages[0]["type"] == "message"


def test_import_legacy_lands_in_one_user(tmp_path):
    from muse_bridge.domain import import_records

    src = tmp_path / "old"
    src.mkdir()
    (src / "job_finds.jsonl").write_text(
        '{"id":"find_old_0","type":"job_find","title":"Old","company":"Co","url":"https://example.com/old"}\n',
        encoding="utf-8",
    )
    (src / "webhook_errors.jsonl").write_text('{"id":"nope","error":"x"}\n', encoding="utf-8")
    app = create_app(Settings(data_dir=tmp_path / "data"))
    import json
    from muse_bridge.admin import main

    code = main(["--data-dir", str(tmp_path / "data"), "import-legacy", "--user", "alice", "--from", str(src)])
    assert code == 0
    rows = app.state.store.list_records("alice")
    assert [row["id"] for row in rows] == ["find_old_0"]
    assert rows[0]["user_id"] == "alice"
    assert rows[0]["kind"] == "job_find"
    assert import_records(app.state.store, "alice", [{"id": "find_old_0", "type": "job_find"}])["skipped"] == 1
    assert json.loads((src / "webhook_errors.jsonl").read_text())["id"] == "nope"
    assert app.state.store.list_records("bob") == []


def test_sync_inbox_routes_kinds(tmp_path):
    assert _destination(tmp_path, {"type": "job_find"}).name == "job_finds.jsonl"
    assert _destination(tmp_path, {"kind": "message"}).name == "messages.jsonl"
    assert _destination(tmp_path, {"kind": "note"}).name == "note.jsonl"
    assert _destination(tmp_path, {"kind": "../etc/passwd"}).name == "etc_passwd.jsonl"


def test_mcp_http_roundtrip(tmp_path):
    httpx2 = pytest.importorskip("httpx2")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    app = create_app(Settings(data_dir=tmp_path / "live"))
    token = app.state.key_store.create_key("alice").token

    class _Server(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            return

    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.3) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.05)
        else:
            raise AssertionError("server did not start")

        async def roundtrip() -> None:
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}, timeout=30
            ) as http:
                async with streamable_http_client(
                    f"http://127.0.0.1:{port}/mcp", http_client=http
                ) as (reader, writer, *_rest):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        names = [tool.name for tool in (await session.list_tools()).tools]
                        assert "ping" in names
                        assert "report_job_finds" in names
                        assert "send_message_to_nori" in names
                        ping = await session.call_tool("ping", {})
                        assert '"ok": true' in ping.content[0].text or '"ok":true' in ping.content[0].text
                        reported = await session.call_tool(
                            "report_job_finds",
                            {
                                "finds": [
                                    {
                                        "title": "Intern",
                                        "company": "Example",
                                        "url": "https://example.com/jobs/live",
                                    }
                                ]
                            },
                        )
                        assert "stored" in reported.content[0].text
                        listed = await session.call_tool("list_recent_finds", {"limit": 3})
                        assert "Intern" in listed.content[0].text

        asyncio.run(roundtrip())
        from sync_inbox import sync_once

        box = tmp_path / "box"
        assert sync_once(base_url=f"http://127.0.0.1:{port}", token=token, inbox=box) == 1
        assert "https://example.com/jobs/live" in (box / "job_finds.jsonl").read_text(encoding="utf-8")
        assert sync_once(base_url=f"http://127.0.0.1:{port}", token=token, inbox=box) == 0
        with urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{port}/api/inbox",
                headers={"Authorization": f"Bearer {token}"},
            ),
            timeout=5,
        ) as response:
            body = response.read().decode()
        assert "https://example.com/jobs/live" in body
    finally:
        server.should_exit = True
        thread.join(timeout=5)
