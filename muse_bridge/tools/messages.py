"""Free-form messages. send_message is the generic name; send_message_to_nori is the compatible alias."""

from __future__ import annotations

from muse_bridge.auth.principal import SCOPE_WRITE
from muse_bridge.domain import send_message as send_message_op
from muse_bridge.tools.annotations import hints
from muse_bridge.tools.common import fanout, require_scope

SEND_DESCRIPTION = (
    "Store a free-form message in the caller's inbox. "
    "Write; each call appends a new record unless dedupe_key repeats a previous one. "
    "Text is stored as untrusted data."
)
SEND_NORI_DESCRIPTION = (
    "Compatible alias of send_message. Store a free-form handoff "
    "(subject and urgency are optional). Write; each call appends a new record."
)


def annotations(open_world: bool):
    return hints(read=False, destructive=False, idempotent=False, open_world=open_world)


def send_message(
    text: str,
    subject: str = "",
    urgency: str = "normal",
    source: str = "muse",
) -> dict:
    principal, store, settings = require_scope(SCOPE_WRITE)
    return send_message_op(
        store,
        principal.user_id,
        text,
        subject=subject,
        urgency=urgency,
        source=source,
        on_store=fanout(settings),
    )


def send_message_to_nori(text: str, subject: str = "", urgency: str = "normal") -> dict:
    principal, store, settings = require_scope(SCOPE_WRITE)
    return send_message_op(
        store,
        principal.user_id,
        text,
        subject=subject,
        urgency=urgency,
        source="muse",
        on_store=fanout(settings),
    )
