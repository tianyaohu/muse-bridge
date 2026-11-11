"""Inbox storage interface.

JSONL is the current implementation. A SQLite store can replace it by
implementing this protocol; callers (tools, the pull API, admin purge) depend
on the protocol only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class InboxStore(Protocol):
    def append(self, user_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Persist ``record`` for ``user_id`` and return the stored copy.

        The stored copy's ``user_id`` is the path user, whatever the caller put
        in the dict.
        """

    def list_records(
        self,
        user_id: str,
        *,
        kind: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return that user's records, oldest first.

        ``since`` keeps records whose ``received_at`` is strictly later than
        that instant. Both sides are parsed with ``parse_iso`` (naive
        timestamps are UTC). A blank ``since`` does not filter. An unparseable
        ``since`` raises ``ValueError("invalid_since")``. A record whose
        ``received_at`` cannot be parsed is omitted when ``since`` is set.
        """

    def delete_records(
        self,
        user_id: str,
        *,
        ids: set[str] | None = None,
        kind: str | None = None,
        before: datetime | None = None,
    ) -> int:
        """Delete the user's matching records. No filter deletes all of them."""

    def delete_user(self, user_id: str) -> int:
        """Remove the user's inbox. Returns how many records were removed."""

    def purge_expired(self, max_age_seconds: int, *, now: datetime | None = None) -> int:
        """Drop records older than ``max_age_seconds`` for every user.

        ``max_age_seconds`` <= 0 deletes nothing.
        """

    def user_ids(self) -> list[str]:
        """User ids that have an inbox directory."""
