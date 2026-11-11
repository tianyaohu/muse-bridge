"""UTC timestamps stored on records.

``parse_iso`` accepts ISO-8601, including a ``Z`` suffix and numeric offsets.
A naive timestamp (no offset) is UTC. Unparseable input returns None.
Do not order these strings as text: ``+00:00`` and ``-05:00`` do not sort
in time order.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
