"""Shared construction of the key store and inbox so the server and admin CLI match."""

from __future__ import annotations

from pathlib import Path

from muse_bridge.auth.keys import KeyStore
from muse_bridge.config import Settings
from muse_bridge.storage.jsonl import JsonlInboxStore


def key_store_for(settings: Settings) -> KeyStore:
    return KeyStore(Path(settings.data_dir) / "keys.json")


def inbox_for(settings: Settings) -> JsonlInboxStore:
    return JsonlInboxStore(Path(settings.data_dir))
