"""What each switched-on MCP server tells an AI about its own tools.

A server's handshake ``instructions`` are written for the model that calls its tools, so
Lucius gets them — the same contract Claude Code and other MCP clients keep. They are
sanitized and bounded at capture (``mcp.client.parse_server_info``); a paused server's are
left out, since its tools are too.
"""

from __future__ import annotations

from typing import NamedTuple

from backend.db.mcp_client_store import McpClientStore


class ServerInstructions(NamedTuple):
    server_id: str
    name: str
    text: str


def enabled_server_instructions() -> list[ServerInstructions]:
    return [
        ServerInstructions(s.id, s.name or s.id, s.server_info_instructions)
        for s in McpClientStore().all()
        if s.enabled and s.server_info_instructions
    ]
