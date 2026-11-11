"""Register one MCP tool per module.

Write tools that fan out to a webhook are marked open-world. Annotations are
hints (readOnlyHint, destructiveHint, idempotentHint, openWorldHint).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from muse_bridge.config import Settings
from muse_bridge.tools import delete, items, jobs, messages, ping


def register_tools(mcp: MCPServer, settings: Settings) -> None:
    open_world = bool(settings.webhook_url)
    mcp.add_tool(
        ping.ping,
        name="ping",
        title=ping.TITLE,
        description=ping.DESCRIPTION,
        annotations=ping.annotations(),
    )
    mcp.add_tool(
        items.submit_item,
        name="submit_item",
        title="Submit item",
        description=items.SUBMIT_DESCRIPTION,
        annotations=items.submit_annotations(open_world),
    )
    mcp.add_tool(
        items.list_items,
        name="list_items",
        title="List items",
        description=items.LIST_DESCRIPTION,
        annotations=items.list_annotations(),
    )
    mcp.add_tool(
        messages.send_message,
        name="send_message",
        title="Send message",
        description=messages.SEND_DESCRIPTION,
        annotations=messages.annotations(open_world),
    )
    mcp.add_tool(
        jobs.report_job_finds,
        name="report_job_finds",
        title="Report job finds",
        description=jobs.REPORT_DESCRIPTION,
        annotations=jobs.report_annotations(open_world),
    )
    mcp.add_tool(
        jobs.list_recent_finds,
        name="list_recent_finds",
        title="List recent finds",
        description=jobs.LIST_DESCRIPTION,
        annotations=jobs.list_annotations(),
    )
    mcp.add_tool(
        messages.send_message_to_nori,
        name="send_message_to_nori",
        title="Send message to Nori",
        description=messages.SEND_NORI_DESCRIPTION,
        annotations=messages.annotations(open_world),
    )
    mcp.add_tool(
        delete.delete_my_data,
        name="delete_my_data",
        title=delete.TITLE,
        description=delete.DESCRIPTION,
        annotations=delete.annotations(),
    )
