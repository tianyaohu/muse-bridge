"""Inbox storage. Import the protocol or the JSONL implementation from here."""

from muse_bridge.storage.base import InboxStore
from muse_bridge.storage.jsonl import JsonlInboxStore

__all__ = ["InboxStore", "JsonlInboxStore"]
