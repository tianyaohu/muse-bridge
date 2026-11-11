"""Identifiers that are safe to use as a single path segment."""

from __future__ import annotations

import re

# Case-sensitive. Lowercase ids are easier to operate, but the value is stored
# exactly as given so two ids that differ only by case stay distinct on
# case-sensitive filesystems.
USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Kinds are data labels, not path segments. job_find and message are built in.
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not USER_ID_RE.fullmatch(user_id):
        raise ValueError(
            "user id must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ "
            "(letters, digits, '.', '_' and '-'; no slashes)"
        )
    return user_id


def validate_kind(kind: str) -> str:
    if not isinstance(kind, str) or not KIND_RE.fullmatch(kind):
        raise ValueError("kind must match ^[a-z][a-z0-9_]{0,31}$")
    return kind
