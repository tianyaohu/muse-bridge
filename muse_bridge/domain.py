"""Inbox operations shared by MCP tools and the HTTP pull API.

Job finds and messages keep the flat record shape the original bridge stored
(``type``, ``title``, ``text``, ...). Other kinds store a ``payload`` object.
Every record is written only through ``InboxStore.append``, which stamps
``user_id`` from the authenticated caller.

Record text is untrusted data. This module stores and returns it. It does not
interpret it.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from muse_bridge.clock import parse_iso, utcnow_iso
from muse_bridge.ids import validate_kind
from muse_bridge.storage.base import InboxStore

MAX_FINDS = 100
MAX_LIST = 200
MAX_PAYLOAD_BYTES = 32_000
MAX_GENERIC_STRING = 4_000
MAX_DELETE_IDS = 500

OnStore = Callable[[dict[str, Any]], None]


def inbox_label(user_id: str) -> str:
    return f"users/{user_id}/records.jsonl"


def report_job_finds(
    store: InboxStore,
    user_id: str,
    finds: list[dict[str, Any]] | None,
    *,
    source: str = "muse",
    batch_note: str = "",
    on_store: OnStore | None = None,
) -> dict[str, Any]:
    label = inbox_label(user_id)
    if finds is not None and not isinstance(finds, list):
        return {
            "stored": [],
            "skipped": [],
            "errors": [{"index": None, "error": "finds must be a list"}],
            "inbox": label,
        }
    rows = finds or []
    if len(rows) > MAX_FINDS:
        return {
            "stored": [],
            "skipped": [],
            "errors": [{"index": None, "error": f"at most {MAX_FINDS} finds per call"}],
            "inbox": label,
        }
    seen = {
        str(row.get("dedupe_key", ""))
        for row in store.list_records(user_id, kind="job_find")
        if row.get("dedupe_key")
    }
    batch = uuid.uuid4().hex[:8]
    stored: list[str] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    source_text = _clip(source, 200)
    note = _clip(batch_note, 2000)
    for index, find in enumerate(rows):
        if not isinstance(find, dict):
            errors.append({"index": index, "error": "not an object"})
            continue
        title = str(find.get("title", "")).strip()
        company = str(find.get("company", "")).strip()
        url = str(find.get("url", "")).strip()
        if not (title and company and url):
            errors.append({"index": index, "error": "title, company and url are required"})
            continue
        key = _job_dedupe_key(url, title, company)
        if key in seen:
            skipped.append({"index": index, "url": url, "reason": "duplicate"})
            continue
        seen.add(key)
        record = {
            "id": f"find_{batch}_{index}",
            "type": "job_find",
            "kind": "job_find",
            "received_at": utcnow_iso(),
            "source": source_text,
            "batch": batch,
            "batch_note": note,
            "title": title[:300],
            "company": company[:200],
            "location": str(find.get("location", ""))[:200],
            "url": url[:2000],
            "posted": str(find.get("posted", ""))[:100],
            "notes": str(find.get("notes", ""))[:4000],
            "dedupe_key": key,
            "status": "new",
        }
        saved = store.append(user_id, record)
        if on_store:
            on_store(saved)
        stored.append(saved["id"])
    return {"stored": stored, "skipped": skipped, "errors": errors, "inbox": label}


def submit_item(
    store: InboxStore,
    user_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    source: str = "muse",
    dedupe_key: str = "",
    on_store: OnStore | None = None,
) -> dict[str, Any]:
    try:
        kind = validate_kind(kind)
    except ValueError:
        return {"stored": False, "id": None, "kind": kind, "error": "invalid_kind"}
    if not isinstance(payload, dict):
        return {"stored": False, "id": None, "kind": kind, "error": "payload must be an object"}
    if kind == "job_find":
        result = report_job_finds(
            store,
            user_id,
            [payload],
            source=source,
            batch_note=_clip(payload.get("batch_note", ""), 2000),
            on_store=on_store,
        )
        if result["stored"]:
            return {"stored": True, "id": result["stored"][0], "kind": kind, "error": None}
        if result["skipped"]:
            return {"stored": False, "id": None, "kind": kind, "skipped": "duplicate", "error": None}
        message = result["errors"][0]["error"] if result["errors"] else "not_stored"
        return {"stored": False, "id": None, "kind": kind, "error": message}
    if kind == "message":
        sent = send_message(
            store,
            user_id,
            text=_clip(payload.get("text", ""), 20_000),
            subject=_clip(payload.get("subject", ""), 300),
            urgency=_clip(payload.get("urgency", "normal"), 40) or "normal",
            source=source,
            dedupe_key=dedupe_key,
            on_store=on_store,
        )
        if sent.get("error"):
            return {"stored": False, "id": None, "kind": kind, "error": sent["error"]}
        if sent.get("skipped"):
            return {"stored": False, "id": None, "kind": kind, "skipped": "duplicate", "error": None}
        return {"stored": True, "id": sent["stored"], "kind": kind, "error": None}
    try:
        clean = _sanitize_payload(payload)
    except ValueError as exc:
        return {"stored": False, "id": None, "kind": kind, "error": str(exc)}
    duplicate = _generic_duplicate(store, user_id, kind, dedupe_key)
    if duplicate:
        return {"stored": False, "id": None, "kind": kind, "skipped": "duplicate", "error": None}
    record: dict[str, Any] = {
        "id": "itm_" + uuid.uuid4().hex[:12],
        "type": kind,
        "kind": kind,
        "received_at": utcnow_iso(),
        "source": _clip(source, 200),
        "status": "new",
        "payload": clean,
    }
    hashed = _hash_dedupe_key(dedupe_key)
    if hashed:
        record["dedupe_key"] = hashed
    saved = store.append(user_id, record)
    if on_store:
        on_store(saved)
    return {"stored": True, "id": saved["id"], "kind": kind, "error": None}


def send_message(
    store: InboxStore,
    user_id: str,
    text: str,
    *,
    subject: str = "",
    urgency: str = "normal",
    source: str = "muse",
    dedupe_key: str = "",
    on_store: OnStore | None = None,
) -> dict[str, Any]:
    if _generic_duplicate(store, user_id, "message", dedupe_key):
        return {"stored": None, "skipped": "duplicate"}
    record: dict[str, Any] = {
        "id": "msg_" + uuid.uuid4().hex[:10],
        "type": "message",
        "kind": "message",
        "received_at": utcnow_iso(),
        "source": _clip(source, 200),
        "subject": _clip(subject, 300),
        "text": _clip(text, 20_000),
        "urgency": _clip(urgency, 40) or "normal",
        "status": "new",
    }
    hashed = _hash_dedupe_key(dedupe_key)
    if hashed:
        record["dedupe_key"] = hashed
    saved = store.append(user_id, record)
    if on_store:
        on_store(saved)
    return {"stored": saved["id"]}


def list_items(
    store: InboxStore,
    user_id: str,
    *,
    kind: str = "",
    limit: int = 20,
    since: str = "",
) -> dict[str, Any]:
    if kind:
        try:
            validate_kind(kind)
        except ValueError:
            return {"count": 0, "items": [], "error": "invalid_kind"}
    try:
        rows = store.list_records(user_id, kind=kind or None, since=since if since else None)
    except ValueError as exc:
        if str(exc) == "invalid_since":
            return {"count": 0, "items": [], "error": "invalid_since"}
        raise
    rows = list(reversed(rows))[:_limit(limit)]
    return {"count": len(rows), "items": rows}


def list_recent_finds(
    store: InboxStore,
    user_id: str,
    *,
    limit: int = 20,
    company: str = "",
) -> dict[str, Any]:
    rows = store.list_records(user_id, kind="job_find")
    if company:
        needle = company.lower()
        rows = [row for row in rows if needle in _company_text(row).lower()]
    rows = list(reversed(rows))[:_limit(limit)]
    fields = ("id", "received_at", "title", "company", "location", "url", "posted", "status")
    return {
        "count": len(rows),
        "finds": [{field: row.get(field) for field in fields} for row in rows],
    }


def pull_inbox(
    store: InboxStore,
    user_id: str,
    *,
    since: str = "",
    type_filter: str = "all",
    kind: str = "",
) -> dict[str, Any]:
    selected = _pull_kind(type_filter, kind)
    rows = store.list_records(user_id, kind=selected, since=since)
    rows.sort(key=_received_at_sort_key)
    return {"count": len(rows), "records": rows}


def delete_my_data(
    store: InboxStore,
    user_id: str,
    confirm: str,
    *,
    kind: str = "",
    before: str = "",
    ids: list[str] | None = None,
) -> dict[str, Any]:
    if confirm != "delete":
        return {
            "deleted": 0,
            "error": "confirmation_required",
            "hint": "pass confirm='delete' to delete your own records",
        }
    if kind:
        try:
            validate_kind(kind)
        except ValueError:
            return {"deleted": 0, "error": "invalid_kind"}
    before_dt: datetime | None = None
    if before:
        before_dt = parse_iso(before)
        if before_dt is None:
            return {"deleted": 0, "error": "invalid_before"}
    id_set: set[str] | None = None
    if ids:
        id_set = {str(item).strip() for item in ids if str(item).strip()}
        if len(id_set) > MAX_DELETE_IDS:
            return {"deleted": 0, "error": "too_many_ids"}
        if not id_set:
            id_set = None
    deleted = store.delete_records(
        user_id,
        ids=id_set,
        kind=kind or None,
        before=before_dt,
    )
    return {"deleted": deleted}


def import_records(store: InboxStore, user_id: str, records: list[dict[str, Any]]) -> dict[str, int]:
    existing = {str(row.get("id")) for row in store.list_records(user_id)}
    imported = skipped = errors = 0
    for record in records:
        if not isinstance(record, dict) or not record.get("id"):
            errors += 1
            continue
        record_id = str(record["id"])
        if record_id in existing:
            skipped += 1
            continue
        raw_kind = str(record.get("kind") or record.get("type") or "item")
        try:
            kind = validate_kind(raw_kind)
        except ValueError:
            kind = "item"
        stored = dict(record)
        stored["id"] = record_id
        stored["kind"] = kind
        stored["type"] = str(record.get("type") or kind)
        store.append(user_id, stored)
        existing.add(record_id)
        imported += 1
    return {"imported": imported, "skipped": skipped, "errors": errors}


def _pull_kind(type_filter: str, kind: str) -> str | None:
    if kind:
        validate_kind(kind)
        return kind
    if type_filter in ("", "all"):
        return None
    if type_filter == "finds":
        return "job_find"
    if type_filter == "messages":
        return "message"
    raise ValueError("type must be all, finds, or messages")


def _job_dedupe_key(url: str, title: str, company: str) -> str:
    # Same non-cryptographic fingerprint as the original bridge, so a URL
    # imported from the old JSONL still counts as a duplicate.
    raw = url.lower().rstrip("/") or f"{title.lower()}|{company.lower()}"
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _hash_dedupe_key(dedupe_key: str) -> str:
    text = str(dedupe_key or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _generic_duplicate(store: InboxStore, user_id: str, kind: str, dedupe_key: str) -> bool:
    hashed = _hash_dedupe_key(dedupe_key)
    if not hashed:
        return False
    for row in store.list_records(user_id, kind=kind):
        if row.get("dedupe_key") == hashed:
            return True
    return False


def _limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(value, MAX_LIST))


def _company_text(row: dict[str, Any]) -> str:
    """Missing and JSON null are empty. They are not the letters ``none``."""
    value = row.get("company") or ""
    return value if isinstance(value, str) else ""


_UNDATED = datetime.max.replace(tzinfo=timezone.utc)


def _received_at_sort_key(row: dict[str, Any]) -> datetime:
    value = row.get("received_at")
    parsed = parse_iso(value) if isinstance(value, str) else None
    return parsed or _UNDATED


def _clip(value: object, limit: int) -> str:
    if value is None:
        return ""
    return str(value)[:limit]


def _sanitize_payload(value: object, *, depth: int = 0) -> Any:
    if depth > 6:
        raise ValueError("payload nested too deeply")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("payload has a non-finite number")
        return value
    if isinstance(value, str):
        return value[:MAX_GENERIC_STRING]
    if isinstance(value, list):
        if len(value) > 100:
            raise ValueError("payload list is too long")
        return [_sanitize_payload(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("payload object is too large")
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            cleaned[str(key)[:100]] = _sanitize_payload(item, depth=depth + 1)
        encoded = json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
        if depth == 0 and len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload is too large")
        return cleaned
    raise ValueError("payload must be made of JSON values")
