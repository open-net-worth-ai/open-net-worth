"""The wire half of ``claude_cli_native_tools``: Lucius's tools as an in-process MCP server.

Claude Code can talk to an MCP server of ``"type": "sdk"`` that lives in the process that spawned
it. It never opens a socket or starts a child: every MCP JSON-RPC message (``initialize``,
``tools/list``, ``tools/call``…) comes out of its stdout stream as a ``control_request`` with
``subtype: "mcp_message"``, and the answer goes back on its stdin as a ``control_response``. The
pipe we already speak ``stream-json`` on is the whole transport — no port, no token, no bridge
process, no dependency (the same channel the Agent SDK uses; verified on claude 2.1.329).

This module only knows the SHAPES: the config the CLI is spawned with, the answers to the
handshake and to ``tools/list``, and the ``control_response`` envelopes. What happens to a
``tools/call`` — it is PARKED while the host runs the tool — is ``claude_native_session``.

ADR ``2026-11-24-the-claude-cli-gets-tools-as-native-calls-over-its-own-control-channel``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

SERVER = "lucius"
PREFIX = f"mcp__{SERVER}__"
# The ONE generic native tool a tool loaded after the process started is called through. The CLI's
# tool list never changes during a live session, so a later `find_tools` load cannot add a tool to
# it — the model is told, in the conversation, to call the new tool by name through this one.
USE_TOOL = "use_tool"

_USE_TOOL_DESCRIPTION = (
    "Call a tool that was announced to you inside a <tools_added> block of the conversation. "
    "Pass its exact `name` and its arguments object as `args`. Tools you were given directly "
    "are called by their own name, never through this one."
)
_USE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The announced tool's exact name."},
        "args": {"type": "object", "description": "The arguments, as that tool's schema asks."},
    },
    "required": ["name", "args"],
}
_EMPTY_SCHEMA = {"type": "object", "properties": {}}

# JSON-RPC: "method not found" / "invalid params".
_NO_METHOD = -32601
_BAD_PARAMS = -32602


def mcp_config() -> str:
    """The ``--mcp-config`` value: one sdk-type server, served over the control channel."""
    return json.dumps({"mcpServers": {SERVER: {"type": "sdk", "name": SERVER}}})


def tool_payload(tools: list[dict]) -> list[dict]:
    """The ``tools/list`` answer for the tools a process is spawned with, plus ``use_tool``."""
    listed = []
    for tool in tools:
        name = tool.get("name") or ""
        if not name or name == USE_TOOL:
            continue
        schema = tool.get("input_schema")
        listed.append(
            {
                "name": name,
                "description": (tool.get("description") or "").strip(),
                "inputSchema": schema if isinstance(schema, dict) and schema else _EMPTY_SCHEMA,
            }
        )
    listed.append(
        {"name": USE_TOOL, "description": _USE_TOOL_DESCRIPTION, "inputSchema": _USE_TOOL_SCHEMA}
    )
    return listed


def unwrap(cli_name: str, arguments: Any) -> tuple[str, dict]:
    """The host's view of a call the model made: ``(real tool name, input)``.

    ``use_tool(name, args)`` IS the named tool to everything downstream — the chat row, the
    approval card, permissions and the audit trail never see ``use_tool``. A malformed wrapper
    keeps its own name, which the registry refuses as an unknown tool, like any invented name.
    """
    name = cli_name.removeprefix(PREFIX)
    args = arguments if isinstance(arguments, dict) else {}
    if name != USE_TOOL:
        return name, args
    inner, inner_args = args.get("name"), args.get("args")
    if not isinstance(inner, str) or not inner.strip():
        return USE_TOOL, args
    return inner.strip().removeprefix(PREFIX), inner_args if isinstance(inner_args, dict) else {}


def reply(message: dict, tools: list[dict]) -> Optional[dict]:
    """The JSON-RPC response to one non-``tools/call`` message; ``None`` for a notification."""
    request_id = message.get("id")
    if request_id is None:
        return None
    method = message.get("method")
    if method == "initialize":
        version = ((message.get("params") or {}).get("protocolVersion")) or "2025-06-18"
        result: dict = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER, "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": tools}
    elif method == "ping":
        result = {}
    else:
        return rpc_error(request_id, _NO_METHOD, f"method not found: {method}")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": text}}


def bad_params(request_id: Any, text: str) -> dict:
    return rpc_error(request_id, _BAD_PARAMS, text)


def call_result(request_id: Any, text: str, is_error: bool) -> dict:
    """A ``tools/call`` response carrying the HOST's result as the model reads it."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": text}], "isError": bool(is_error)},
    }


def control_response(request_id: str, mcp_response: dict) -> dict:
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": {"mcp_response": mcp_response},
        },
    }


def control_error(request_id: str, text: str) -> dict:
    return {
        "type": "control_response",
        "response": {"subtype": "error", "request_id": request_id, "error": text},
    }


def result_text(content: Any) -> str:
    """A ``tool_result`` block's content as the text the model is answered with."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type", "text") == "text"
        )
    return "" if content is None else str(content)
