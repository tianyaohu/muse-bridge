"""MCP tool annotations (hints). Wire names are readOnlyHint, destructiveHint, idempotentHint, openWorldHint."""

from __future__ import annotations

from mcp_types import ToolAnnotations


def hints(
    *,
    read: bool,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )
