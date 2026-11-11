"""Per-install API keys.

Token format: ``mb_<prefix>_<secret>`` where ``prefix`` is 8 lowercase hex
characters and ``secret`` is url-safe. Only SHA-256 of the full token is
stored. Lookup is by prefix; the hash is compared with ``hmac.compare_digest``.
Revoked keys stay in the file so a prefix is never reused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from muse_bridge.auth.principal import FULL_SCOPES, Principal
from muse_bridge.clock import parse_iso, utcnow, utcnow_iso
from muse_bridge.ids import validate_user_id
from muse_bridge.storage.locking import exclusive_lock

logger = logging.getLogger("muse_bridge.auth")

_API_KEY_RE = re.compile(r"^mb_([0-9a-f]{8})_([A-Za-z0-9_-]{20,128})$")
_DUMMY_HASH = hashlib.sha256(b"muse-bridge-missing-key").hexdigest()
_LAST_USED_MIN_INTERVAL_SECONDS = 60
_HASH_ALG = "sha256"


@dataclass(frozen=True)
class IssuedKey:
    """Returned once from ``create_key``. The token is not stored."""

    token: str
    key_id: str
    prefix: str
    user_id: str


@dataclass(frozen=True)
class KeyInfo:
    key_id: str
    prefix: str
    user_id: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None
    label: str

    @property
    def active(self) -> bool:
        return self.revoked_at is None


def parse_api_key(token: str) -> str | None:
    """Return the prefix when ``token`` has the API-key shape, else None."""
    if not token or len(token) > 200:
        return None
    match = _API_KEY_RE.fullmatch(token)
    if match is None:
        return None
    return match.group(1)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secrets_equal(presented: str, expected: str) -> bool:
    """Compare two secrets without raising on unequal length.

    Both sides are hashed first so ``compare_digest`` always sees equal-length
    digests. An empty expected secret never matches.
    """
    presented_digest = hashlib.sha256(presented.encode("utf-8")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest() if expected else hashlib.sha256(b"").digest()
    if not expected:
        hmac.compare_digest(presented_digest, expected_digest)
        return False
    return hmac.compare_digest(presented_digest, expected_digest)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".keys.", suffix=".tmp")
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


class KeyStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("keys"), list):
            raise ValueError("api key store is corrupt")
        return [row for row in raw["keys"] if isinstance(row, dict)]

    def _save(self, keys: list[dict]) -> None:
        _atomic_json(self.path, {"version": 1, "keys": keys})

    def create_key(self, user_id: str, *, label: str = "") -> IssuedKey:
        user_id = validate_user_id(user_id)
        label = str(label or "")[:120]
        with exclusive_lock(self.path):
            keys = self._load()
            used = {str(row.get("prefix", "")) for row in keys}
            prefix = ""
            for _ in range(8):
                candidate = secrets.token_hex(4)
                if candidate not in used:
                    prefix = candidate
                    break
            if not prefix:
                raise RuntimeError("could not allocate an API key prefix")
            secret = secrets.token_urlsafe(32)
            token = f"mb_{prefix}_{secret}"
            parsed = parse_api_key(token)
            if parsed != prefix:
                raise RuntimeError("generated API key failed format check")
            key_id = "key_" + secrets.token_hex(8)
            keys.append(
                {
                    "key_id": key_id,
                    "prefix": prefix,
                    "hash_alg": _HASH_ALG,
                    "secret_hash": token_hash(token),
                    "user_id": user_id,
                    "created_at": utcnow_iso(),
                    "last_used_at": None,
                    "revoked_at": None,
                    "label": label,
                }
            )
            self._save(keys)
        return IssuedKey(token=token, key_id=key_id, prefix=prefix, user_id=user_id)

    def list_keys(self, *, user_id: str | None = None) -> list[KeyInfo]:
        with exclusive_lock(self.path):
            rows = self._load() if self.path.exists() else []
        infos: list[KeyInfo] = []
        for row in rows:
            info = _info(row)
            if info is None:
                continue
            if user_id is not None and info.user_id != user_id:
                continue
            infos.append(info)
        return infos

    def verify(self, token: str) -> Principal | None:
        """Return the principal for an active key, or None.

        Unknown prefixes still run ``compare_digest`` against a dummy hash.
        A token with the API-key shape never falls through to another method;
        callers stop the chain when ``parse_api_key`` matches.
        """
        if not token or len(token) > 8192:
            return None
        prefix = parse_api_key(token)
        digest = token_hash(token)
        try:
            with exclusive_lock(self.path):
                if not self.path.exists():
                    rows: list[dict] = []
                else:
                    rows = self._load()
                match = None
                if prefix is not None:
                    for row in rows:
                        if row.get("prefix") == prefix and row.get("hash_alg", _HASH_ALG) == _HASH_ALG:
                            match = row
                            break
                expected = str(match.get("secret_hash")) if match else _DUMMY_HASH
                if len(expected) != len(digest):
                    expected = _DUMMY_HASH
                ok = hmac.compare_digest(digest, expected)
                if match is None or not ok or match.get("revoked_at"):
                    return None
                self._touch_last_used(rows, match)
                user_id = validate_user_id(str(match.get("user_id", "")))
                key_id = str(match.get("key_id", ""))
        except (OSError, ValueError, json.JSONDecodeError):
            logger.exception("api key verification failed closed")
            return None
        return Principal(
            user_id=user_id,
            auth_method="api_key",
            scopes=FULL_SCOPES,
            key_id=key_id or None,
        )

    def _touch_last_used(self, rows: list[dict], match: dict) -> None:
        previous = parse_iso(str(match.get("last_used_at") or ""))
        now = utcnow()
        if previous is not None and (now - previous).total_seconds() < _LAST_USED_MIN_INTERVAL_SECONDS:
            return
        match["last_used_at"] = now.isoformat(timespec="seconds")
        self._save(rows)

    def revoke(self, *, prefix: str | None = None, key_id: str | None = None) -> KeyInfo | None:
        if not prefix and not key_id:
            raise ValueError("prefix or key_id is required")
        with exclusive_lock(self.path):
            rows = self._load() if self.path.exists() else []
            for row in rows:
                if prefix and row.get("prefix") != prefix:
                    continue
                if key_id and row.get("key_id") != key_id:
                    continue
                if not row.get("revoked_at"):
                    row["revoked_at"] = utcnow_iso()
                    self._save(rows)
                return _info(row)
        return None

    def delete_user(self, user_id: str) -> int:
        user_id = validate_user_id(user_id)
        with exclusive_lock(self.path):
            if not self.path.exists():
                return 0
            rows = self._load()
            kept = [row for row in rows if row.get("user_id") != user_id]
            removed = len(rows) - len(kept)
            if removed:
                self._save(kept)
            return removed


def _info(row: dict) -> KeyInfo | None:
    prefix = row.get("prefix")
    key_id = row.get("key_id")
    user_id = row.get("user_id")
    created_at = row.get("created_at")
    if not all(isinstance(value, str) and value for value in (prefix, key_id, user_id, created_at)):
        return None
    last_used = row.get("last_used_at")
    revoked = row.get("revoked_at")
    return KeyInfo(
        key_id=str(key_id),
        prefix=str(prefix),
        user_id=str(user_id),
        created_at=str(created_at),
        last_used_at=str(last_used) if last_used else None,
        revoked_at=str(revoked) if revoked else None,
        label=str(row.get("label") or ""),
    )
