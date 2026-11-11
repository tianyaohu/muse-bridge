"""Operator CLI for API keys, retention purge, and per-user deletion.

Examples::

    python -m muse_bridge.admin create-key --user alice
    python -m muse_bridge.admin list-keys
    python -m muse_bridge.admin revoke-key --prefix a1b2c3d4
    python -m muse_bridge.admin delete-user --user alice --yes
    python -m muse_bridge.admin purge
    python -m muse_bridge.admin import-legacy --user legacy --from ./inbox

The token from create-key is printed once. The store keeps only its hash.
``--data-dir`` overrides ``MUSE_BRIDGE_DATA_DIR`` / ``MUSE_BRIDGE_INBOX``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from muse_bridge.config import Settings, load_settings
from muse_bridge.domain import import_records
from muse_bridge.ids import validate_user_id
from muse_bridge.wiring import inbox_for, key_store_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m muse_bridge.admin")
    parser.add_argument("--data-dir", default=None, help="override the data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-key", help="issue an API key and print the token once")
    create.add_argument("--user", required=True)
    create.add_argument("--label", default="")

    listing = sub.add_parser("list-keys", help="list key ids and prefixes (not secrets)")
    listing.add_argument("--user", default="")

    revoke = sub.add_parser("revoke-key", help="revoke a key by prefix or key id")
    group = revoke.add_mutually_exclusive_group(required=True)
    group.add_argument("--prefix")
    group.add_argument("--key-id")

    delete = sub.add_parser("delete-user", help="delete one user's keys and inbox")
    delete.add_argument("--user", required=True)
    delete.add_argument("--yes", action="store_true", help="required")

    purge = sub.add_parser("purge", help="delete records older than the retention window")
    purge.add_argument("--days", type=int, default=None, help="override MUSE_BRIDGE_RETENTION_DAYS")

    legacy = sub.add_parser("import-legacy", help="copy JSONL records into one user's inbox")
    legacy.add_argument("--user", required=True)
    legacy.add_argument("--from", dest="source", required=True, help="directory of *.jsonl files")

    args = parser.parse_args(argv)
    try:
        settings = _settings(args.data_dir)
        if args.command == "create-key":
            return _create_key(settings, args.user, args.label)
        if args.command == "list-keys":
            return _list_keys(settings, args.user or None)
        if args.command == "revoke-key":
            return _revoke(settings, prefix=args.prefix, key_id=args.key_id)
        if args.command == "delete-user":
            return _delete_user(settings, args.user, yes=args.yes)
        if args.command == "purge":
            return _purge(settings, args.days)
        if args.command == "import-legacy":
            return _import_legacy(settings, args.user, Path(args.source))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"error: unknown command {args.command}", file=sys.stderr)
    return 2


def _settings(data_dir: str | None) -> Settings:
    settings = load_settings()
    if data_dir:
        settings = replace(settings, data_dir=Path(data_dir))
    return settings


def _create_key(settings: Settings, user: str, label: str) -> int:
    issued = key_store_for(settings).create_key(user, label=label)
    print(f"user: {issued.user_id}")
    print(f"key_id: {issued.key_id}")
    print(f"prefix: {issued.prefix}")
    print(f"token: {issued.token}")
    print("Store this token now. It cannot be shown again.")
    return 0


def _list_keys(settings: Settings, user: str | None) -> int:
    infos = key_store_for(settings).list_keys(user_id=user)
    if not infos:
        print("no keys")
        return 0
    for info in infos:
        state = "revoked" if info.revoked_at else "active"
        print(
            f"{info.key_id}\t{info.prefix}\t{info.user_id}\t{state}\t"
            f"created={info.created_at}\tlast_used={info.last_used_at or '-'}\t"
            f"label={info.label}"
        )
    return 0


def _revoke(settings: Settings, *, prefix: str | None, key_id: str | None) -> int:
    info = key_store_for(settings).revoke(prefix=prefix, key_id=key_id)
    if info is None:
        print("error: key not found", file=sys.stderr)
        return 1
    print(f"revoked {info.key_id} prefix={info.prefix} user={info.user_id}")
    return 0


def _delete_user(settings: Settings, user: str, *, yes: bool) -> int:
    validate_user_id(user)
    if not yes:
        print("error: pass --yes to delete the user", file=sys.stderr)
        return 2
    keys_removed = key_store_for(settings).delete_user(user)
    records_removed = inbox_for(settings).delete_user(user)
    print(f"deleted user {user}: keys={keys_removed} records={records_removed}")
    return 0


def _purge(settings: Settings, days: int | None) -> int:
    window = settings.retention_days if days is None else days
    if window < 0:
        print("error: days must be >= 0", file=sys.stderr)
        return 2
    removed = inbox_for(settings).purge_expired(window * 86400)
    print(f"purged {removed} record(s); retention_days={window}")
    return 0


def _import_legacy(settings: Settings, user: str, source: Path) -> int:
    validate_user_id(user)
    if not source.is_dir():
        print(f"error: {source} is not a directory", file=sys.stderr)
        return 2
    records: list[dict] = []
    for path in sorted(source.glob("*.jsonl")):
        if path.name == "webhook_errors.jsonl":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                records.append({})
                continue
            if isinstance(row, dict):
                records.append(row)
    result = import_records(inbox_for(settings), user, records)
    print(
        f"imported {result['imported']} skipped {result['skipped']} "
        f"errors {result['errors']} user={user}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
