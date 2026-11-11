"""Pull the authenticated user's records into the local inbox (idempotent).

Usage (on the box)::

    MUSE_BRIDGE_URL=https://muse-bridge.example.com \\
    MUSE_BRIDGE_API_KEY=mb_... \\
        .venv/bin/python sync_inbox.py

Auth is ``Authorization: Bearer``. ``MUSE_BRIDGE_API_KEY`` is preferred.
``MUSE_BRIDGE_TOKEN`` is the fallback for legacy single-token mode.
Unset variables are filled from ``./.env``. Records land in ``inbox/`` by
kind: ``job_find`` -> ``job_finds.jsonl``, ``message`` -> ``messages.jsonl``,
anything else -> ``<kind>.jsonl``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

_KIND_FILE = re.compile(r"[^a-z0-9_-]+")


def _load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _credential(env: dict[str, str]) -> str:
    return (
        os.environ.get("MUSE_BRIDGE_API_KEY")
        or env.get("MUSE_BRIDGE_API_KEY")
        or os.environ.get("MUSE_BRIDGE_TOKEN")
        or env.get("MUSE_BRIDGE_TOKEN")
        or ""
    )


def _destination(inbox: Path, record: dict) -> Path:
    kind = str(record.get("type") or record.get("kind") or "other").lower()
    if kind == "job_find":
        return inbox / "job_finds.jsonl"
    if kind == "message":
        return inbox / "messages.jsonl"
    safe = _KIND_FILE.sub("_", kind).strip("_") or "other"
    return inbox / f"{safe}.jsonl"


def _known_ids(inbox: Path) -> set[str]:
    found: set[str] = set()
    if not inbox.exists():
        return found
    for path in inbox.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("id"):
                found.add(str(row["id"]))
    return found


def sync_once(*, base_url: str, token: str, inbox: Path) -> int:
    """Pull ``/api/inbox`` and append records whose ids are not local yet."""
    inbox.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/inbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"inbox pull failed: HTTP {exc.code}") from exc
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise SystemExit("inbox pull failed: response has no records list")
    have = _known_ids(inbox)
    new = [row for row in records if isinstance(row, dict) and row.get("id") and str(row["id"]) not in have]
    for row in new:
        path = _destination(inbox, row)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(new)


def main() -> None:
    here = Path(__file__).resolve().parent
    env = _load_env(here / ".env")
    token = _credential(env)
    base = (os.environ.get("MUSE_BRIDGE_URL") or env.get("MUSE_BRIDGE_URL", "")).rstrip("/")
    if not token or not base:
        raise SystemExit("set MUSE_BRIDGE_URL and MUSE_BRIDGE_API_KEY (or MUSE_BRIDGE_TOKEN)")
    # MUSE_BRIDGE_INBOX is the server's data directory. The pull script keeps
    # writing the historical box inbox (./inbox) unless MUSE_BRIDGE_SYNC_INBOX
    # points somewhere else.
    inbox = Path(
        os.environ.get("MUSE_BRIDGE_SYNC_INBOX")
        or env.get("MUSE_BRIDGE_SYNC_INBOX")
        or (here / "inbox")
    )
    added = sync_once(base_url=base, token=token, inbox=inbox)
    print(f"pulled {added} new record(s) from {base}")


if __name__ == "__main__":
    main()
