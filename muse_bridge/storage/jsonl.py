"""Per-user append-only JSONL inbox.

Layout under the data directory::

    users/<user_id>/records.jsonl

A call for user A opens only ``users/A/``. User ids are a single safe path
segment, checked again after ``resolve`` so a crafted id cannot escape
``users/``.

JSONL matches the existing inbox workflow (inspectable files, ``sync_inbox.py``
pulls the same records over HTTP). Deletion and retention rewrite a user's
file under an exclusive lock. That is the right trade for this volume and is
the main reason a later SQLite store is straightforward: the rewrite step is
the part SQL would do better. The protocol in ``base.py`` is that seam.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from muse_bridge.clock import parse_iso, utcnow
from muse_bridge.ids import validate_user_id
from muse_bridge.storage.locking import exclusive_lock, shared_lock


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _matches_kind(row: dict[str, Any], kind: str) -> bool:
    return row.get("kind") == kind or row.get("type") == kind


class JsonlInboxStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _users_root(self) -> Path:
        return (self.root / "users").resolve()

    def _user_dir(self, user_id: str) -> Path:
        user_id = validate_user_id(user_id)
        directory = (self._users_root() / user_id).resolve()
        if directory.parent != self._users_root():
            raise ValueError("invalid user id")
        return directory

    def _records_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "records.jsonl"

    def append(self, user_id: str, record: dict[str, Any]) -> dict[str, Any]:
        path = self._records_path(user_id)
        stamped = dict(record)
        stamped["user_id"] = validate_user_id(user_id)
        line = json.dumps(stamped, ensure_ascii=False)
        with exclusive_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, (line + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        return stamped

    def list_records(
        self,
        user_id: str,
        *,
        kind: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        path = self._records_path(user_id)
        if not path.exists():
            return []
        try:
            with shared_lock(path):
                text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        rows = _parse_jsonl(text)
        if kind:
            rows = [row for row in rows if _matches_kind(row, kind)]
        if since is not None:
            rows = [row for row in rows if str(row.get("received_at", "")) > since]
        return rows

    def delete_records(
        self,
        user_id: str,
        *,
        ids: set[str] | None = None,
        kind: str | None = None,
        before: datetime | None = None,
    ) -> int:
        path = self._records_path(user_id)
        if not path.exists():
            return 0

        def keep(row: dict[str, Any]) -> bool:
            if ids is not None and str(row.get("id", "")) not in ids:
                return True
            if kind and not _matches_kind(row, kind):
                return True
            if before is not None:
                stamped = parse_iso(str(row.get("received_at", "")))
                if stamped is None or stamped >= before:
                    return True
            return False

        with exclusive_lock(path):
            if not path.exists():
                return 0
            rows = _parse_jsonl(path.read_text(encoding="utf-8"))
            kept = [row for row in rows if keep(row)]
            removed = len(rows) - len(kept)
            if removed:
                body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept)
                _atomic_write(path, body)
            return removed

    def delete_user(self, user_id: str) -> int:
        directory = self._user_dir(user_id)
        path = directory / "records.jsonl"
        if not directory.exists():
            return 0
        with exclusive_lock(path):
            count = 0
            if path.exists():
                count = len(_parse_jsonl(path.read_text(encoding="utf-8")))
            shutil.rmtree(directory, ignore_errors=True)
            return count

    def purge_expired(self, max_age_seconds: int, *, now: datetime | None = None) -> int:
        if max_age_seconds <= 0:
            return 0
        cutoff = (now or utcnow()) - timedelta(seconds=max_age_seconds)
        removed = 0
        for user_id in self.user_ids():
            removed += self.delete_records(user_id, before=cutoff)
        return removed

    def user_ids(self) -> list[str]:
        root = self.root / "users"
        if not root.exists():
            return []
        found: list[str] = []
        for child in sorted(root.iterdir()):
            if child.is_dir():
                try:
                    found.append(validate_user_id(child.name))
                except ValueError:
                    continue
        return found
