"""Smoke test: python test_client.py [base_url]  (reads MUSE_BRIDGE_TOKEN from env/.env)"""
import asyncio, json, os, sys, httpx
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
tok = os.environ.get("MUSE_BRIDGE_TOKEN") or dict(l.split("=",1) for l in Path(__file__).with_name(".env").read_text().split()).get("MUSE_BRIDGE_TOKEN")
url = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/") + "/mcp"
async def main():
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {tok}"}, timeout=30) as hc:
        async with streamable_http_client(url, http_client=hc) as (r, w, *_):
            async with ClientSession(r, w) as s:
                await s.initialize()
                print("tools:", [t.name for t in (await s.list_tools()).tools])
                for name, args in [("ping", {}),
                    ("report_job_finds", {"finds": [{"title": "TEST Software Intern", "company": "BridgeTest Inc", "location": "Remote", "url": "https://example.com/jobs/bridge-test", "posted": "today", "notes": "smoke test - safe to ignore"}], "source": "smoke-test"}),
                    ("list_recent_finds", {"limit": 3})]:
                    res = await s.call_tool(name, args)
                    print(name, "->", res.content[0].text[:300])
asyncio.run(main())
