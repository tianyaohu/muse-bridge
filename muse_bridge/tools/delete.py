"""Caller-scoped deletion. Does not touch any other user's inbox."""

from __future__ import annotations

from muse_bridge.auth.principal import SCOPE_WRITE
from muse_bridge.domain import delete_my_data as delete_my_data_op
from muse_bridge.tools.annotations import hints
from muse_bridge.tools.common import require_scope

DESCRIPTION = (
    "Delete the caller's own inbox records. confirm must be the exact string 'delete'. "
    "Optional kind, before (ISO time, records older than that are removed), and ids. "
    "With only confirm, every record for this user is deleted. "
    "Destructive and idempotent. Does not delete other users."
)
TITLE = "Delete my data"


def annotations(open_world: bool = False):
    del open_world
    return hints(read=False, destructive=True, idempotent=True, open_world=False)


def delete_my_data(
    confirm: str,
    kind: str = "",
    before: str = "",
    ids: list[str] | None = None,
) -> dict:
    principal, store, _settings = require_scope(SCOPE_WRITE)
    return delete_my_data_op(
        store,
        principal.user_id,
        confirm,
        kind=kind,
        before=before,
        ids=ids,
    )
