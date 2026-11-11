"""Smoke test: python test_client.py [base_url]

Reads MUSE_BRIDGE_TOKEN from the environment or from .env. Does not call useapi.net.
The server must be running with MUSE_BRIDGE_LEGACY_SINGLE_TOKEN=1 for that token.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _token() -> str:
    env = os.environ.get("MUSE_BRIDGE_TOKEN", "").strip()
    if env:
        return env
    path = Path(__file__).with_name(".env")
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MUSE_BRIDGE_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


async def main() -> None:
    token = _token()
    if not token:
        raise SystemExit("Set MUSE_BRIDGE_TOKEN in the environment or .env")
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")
    async with httpx.AsyncClient(timeout=30) as http:
        health = await http.get(f"{base}/healthz")
        health.raise_for_status()
        print("healthz", health.json())
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as (
            reader,
            writer,
            *_rest,
        ):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                print("tools:", [tool.name for tool in tools.tools])
                calls = [
                    ("ping", {}),
                    (
                        "report_job_finds",
                        {
                            "finds": [
                                {
                                    "title": "TEST Software Intern",
                                    "company": "BridgeTest Inc",
                                    "location": "Remote",
                                    "url": "https://example.com/jobs/bridge-test",
                                    "posted": "today",
                                    "notes": "smoke test - safe to ignore",
                                }
                            ],
                            "source": "smoke-test",
                        },
                    ),
                    ("list_recent_finds", {"limit": 3}),
                ]
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    print(name, "->", result.content[0].text[:300])


if __name__ == "__main__":
    asyncio.run(main())
