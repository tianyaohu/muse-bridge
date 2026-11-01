"""Muse -> Nori bridge: a tiny Streamable-HTTP MCP server.

Meta Muse (cloud) connects to this as a Custom Connector and pushes job finds
and free-form messages. Everything is appended as JSONL under ./inbox/ so Nori
(Grok Bot) can pick it up from the box. Optional webhook fan-out.

Env:
  MUSE_BRIDGE_TOKEN   required shared secret (Bearer / X-API-Key / ?key=)
  MUSE_BRIDGE_PORT    default 8765
  MUSE_BRIDGE_INBOX   default <this dir>/inbox
  NORI_WEBHOOK_URL    optional; each new record is POSTed here as JSON
  NORI_WEBHOOK_SECRET optional; sent as X-Bridge-Secret header on webhook
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

HERE = Path(__file__).resolve().parent
INBOX = Path(os.environ.get("MUSE_BRIDGE_INBOX", HERE / "inbox"))
INBOX.mkdir(parents=True, exist_ok=True)
FINDS = INBOX / "job_finds.jsonl"
MESSAGES = INBOX / "messages.jsonl"
TOKEN = os.environ.get("MUSE_BRIDGE_TOKEN", "")
PORT = int(os.environ.get("MUSE_BRIDGE_PORT", "8765"))
WEBHOOK = os.environ.get("NORI_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("NORI_WEBHOOK_SECRET", "")
VERSION = "0.1.0"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(path: Path, rec: dict) -> None:
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _webhook(rec: dict) -> None:
    if not WEBHOOK:
        return
    def go():
        try:
            req = urllib.request.Request(
                WEBHOOK, data=json.dumps(rec).encode(), method="POST",
                headers={"Content-Type": "application/json",
                         **({"X-Bridge-Secret": WEBHOOK_SECRET} if WEBHOOK_SECRET else {})})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # never fail the tool call on webhook errors
            _append(INBOX / "webhook_errors.jsonl", {"at": _now(), "id": rec.get("id"), "error": str(e)})
    threading.Thread(target=go, daemon=True).start()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _seen_keys() -> set[str]:
    return {r.get("dedupe_key", "") for r in _read(FINDS)}


mcp = MCPServer(
    name="nori-muse-bridge",
    title="Nori Muse Bridge",
    version=VERSION,
    instructions=(
        "Bridge from Meta Muse to Nori (Frank Hu's Grok Bot). Use report_job_finds to hand "
        "structured job postings to Nori for the job-hunt pipeline (one call can carry many "
        "finds). Use send_message_to_nori for anything else. list_recent_finds shows what "
        "was already reported so you can avoid duplicates."
    ),
)


@mcp.tool(description="Health check. Returns server name, version and time.")
def ping() -> dict:
    return {"ok": True, "server": "nori-muse-bridge", "version": VERSION, "time": _now()}


@mcp.tool(description=(
    "Report one or more job postings to Nori. Each find needs title, company and url; "
    "location, posted (date string as shown on the posting) and notes are optional. "
    "Duplicates (same url, or same title+company) are skipped. Returns stored ids."))
def report_job_finds(finds: list[dict[str, Any]], source: str = "muse", batch_note: str = "") -> dict:
    seen = _seen_keys()
    batch = str(uuid.uuid4())[:8]
    stored, skipped, errors = [], [], []
    for i, f in enumerate(finds or []):
        if not isinstance(f, dict):
            errors.append({"index": i, "error": "not an object"}); continue
        title = str(f.get("title", "")).strip()
        company = str(f.get("company", "")).strip()
        url = str(f.get("url", "")).strip()
        if not (title and company and url):
            errors.append({"index": i, "error": "title, company and url are required"}); continue
        key = url.lower().rstrip("/") or f"{title.lower()}|{company.lower()}"
        key = hashlib.sha1(key.encode()).hexdigest()[:16]
        if key in seen:
            skipped.append({"index": i, "url": url, "reason": "duplicate"}); continue
        seen.add(key)
        rec = {
            "id": f"find_{batch}_{i}", "type": "job_find", "received_at": _now(),
            "source": source, "batch": batch, "batch_note": batch_note,
            "title": title[:300], "company": company[:200],
            "location": str(f.get("location", ""))[:200], "url": url[:2000],
            "posted": str(f.get("posted", ""))[:100], "notes": str(f.get("notes", ""))[:4000],
            "dedupe_key": key, "status": "new",
        }
        _append(FINDS, rec); _webhook(rec)
        stored.append(rec["id"])
    return {"stored": stored, "skipped": skipped, "errors": errors, "inbox": str(FINDS)}


@mcp.tool(description="Send a free-form message/handoff to Nori (e.g. a summary, a question, a link).")
def send_message_to_nori(text: str, subject: str = "", urgency: str = "normal") -> dict:
    rec = {"id": f"msg_{uuid.uuid4().hex[:10]}", "type": "message", "received_at": _now(),
           "subject": subject[:300], "text": text[:20000], "urgency": urgency, "status": "new"}
    _append(MESSAGES, rec); _webhook(rec)
    return {"stored": rec["id"]}


@mcp.tool(description="List the most recent job finds already reported (newest first).")
def list_recent_finds(limit: int = 20, company: str = "") -> dict:
    rows = _read(FINDS)
    if company:
        rows = [r for r in rows if company.lower() in r.get("company", "").lower()]
    rows = rows[::-1][: max(1, min(int(limit), 200))]
    return {"count": len(rows), "finds": [
        {k: r.get(k) for k in ("id", "received_at", "title", "company", "location", "url", "posted", "status")}
        for r in rows]}


class AuthMiddleware:
    """Shared-secret gate. Accepts Authorization: Bearer, X-API-Key, or ?key=."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in ("/healthz", "/"):
            body = {"ok": True, "server": "nori-muse-bridge", "mcp": "/mcp"}
            return await JSONResponse(body)(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        supplied = ""
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        supplied = supplied or headers.get("x-api-key", "")
        if not supplied:
            from urllib.parse import parse_qs
            supplied = (parse_qs(scope.get("query_string", b"").decode()).get("key") or [""])[0]
        if not TOKEN or not hmac.compare_digest(supplied, TOKEN):
            return await JSONResponse({"error": "unauthorized"}, status_code=401,
                                      headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        if path == "/api/inbox":  # pull API for Nori when the server runs off-box
            from urllib.parse import parse_qs
            q = parse_qs(scope.get("query_string", b"").decode())
            since = (q.get("since") or [""])[0]
            kind = (q.get("type") or ["all"])[0]
            rows = []
            if kind in ("all", "finds"):
                rows += _read(FINDS)
            if kind in ("all", "messages"):
                rows += _read(MESSAGES)
            rows = sorted((r for r in rows if r.get("received_at", "") > since), key=lambda r: r.get("received_at", ""))
            return await JSONResponse({"count": len(rows), "records": rows})(scope, receive, send)
        return await self.app(scope, receive, send)


def build_app():
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,       # tolerant of tunnels/restarts; no sticky sessions needed
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host="0.0.0.0",
    )
    return AuthMiddleware(app), app


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set MUSE_BRIDGE_TOKEN (see README). Refusing to run unauthenticated.")
    wrapped, inner = build_app()
    # keep Starlette lifespan (session manager) running via the inner app's router
    class Lifespan:
        def __init__(self, outer, inner): self.outer, self.inner = outer, inner
        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                return await self.inner(scope, receive, send)
            return await self.outer(scope, receive, send)
    uvicorn.run(Lifespan(wrapped, inner), host=os.environ.get("MUSE_BRIDGE_HOST", "127.0.0.1"),
                port=PORT, log_level="info", proxy_headers=True, forwarded_allow_ips="*")
