"""Lucius's tools, as Claude Code sees them on the Agent SDK path (flag ``claude_cli_native_tools``).

Every offered tool becomes one tool of an in-process MCP server named ``lucius``, which Claude
Code lists as ``mcp__lucius__<name>``. The server's handler never runs the tool — the HOST does,
through ``assistant.run``'s dispatch, confirm gate and permissions. The handler only answers with
an obvious "not run" placeholder; ``claude_sdk_live`` swaps the host's real result in from a
blocking ``PostToolUse`` hook, keyed by the call's id. ADR
``2026-11-23-the-claude-cli-gets-tools-as-native-calls-and-the-host-still-runs-them``.

The lockdown is the same one the text transport has, plus the SDK's own: no Claude built-in tool
(``tools=[]``), no user settings/hooks/CLAUDE.md, only the servers we pass, ``dontAsk`` so nothing
ever waits on a prompt, only ``mcp__lucius__*`` allowed, and a ``PreToolUse`` hook that refuses
anything else by name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from claude_agent_sdk import (
    HookMatcher,
    SdkMcpTool,
    ToolAnnotations,
    create_sdk_mcp_server,
)

from backend.core.child_env import scrubbed_environ

SERVER = "lucius"
PREFIX = f"mcp__{SERVER}__"
#: What the model is told if a call ever reaches it unanswered. It must read as "no result" —
#: never as data — and the process is killed on every path that could leave it in place.
PENDING_TEXT = "Not run: this call is being run by the host and has no result yet."

# A tool's full name (`mcp__lucius__<name>`) may be at most 64 characters, from [A-Za-z0-9_-].
_NAME_LIMIT = 64 - len(PREFIX)
_NOT_ALLOWED = re.compile(r"[^A-Za-z0-9_-]")
# A result stays inline in the conversation up to this many characters (Claude Code otherwise
# saves one over ~50,000 to a file the model has no tool to open). Tools bound their own output.
_INLINE_RESULT_CHARS = 500_000
_MAX_OUTPUT_TOKENS = "200000"


def wire_name(name: str) -> str:
    """The name Claude Code calls ``name`` by: its own when it fits, else cut and hash-suffixed
    (the same name always gives the same answer, so a call maps back to its tool)."""
    if len(name) <= _NAME_LIMIT and not _NOT_ALLOWED.search(name):
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    cleaned = _NOT_ALLOWED.sub("_", name)[: _NAME_LIMIT - len(digest) - 1].rstrip("_")
    return f"{cleaned}_{digest}"


def host_names(tools: list[dict]) -> dict[str, str]:
    """wire name -> the host's tool name, for every offered tool."""
    return {wire_name(t["name"]): t["name"] for t in tools if t.get("name")}


async def _pending(_args: dict) -> dict:
    return {"content": [{"type": "text", "text": PENDING_TEXT}]}


def _schema(tool: dict) -> dict:
    schema = tool.get("input_schema")
    if isinstance(schema, dict) and schema.get("type") == "object":
        return schema
    return {"type": "object", "properties": {}}


def build_server(tools: list[dict]) -> Any:
    """The in-process MCP server holding every offered tool, none of which can run itself."""
    sdk_tools = [
        SdkMcpTool(
            name=wire_name(t["name"]),
            description=(t.get("description") or "").strip() or t["name"],
            input_schema=_schema(t),
            handler=_pending,
            annotations=ToolAnnotations(maxResultSizeChars=_INLINE_RESULT_CHARS),
        )
        for t in tools
        if t.get("name")
    ]
    return create_sdk_mcp_server(SERVER, tools=sdk_tools)


def refuse_unoffered(offered: set[str]) -> HookMatcher:
    """A ``PreToolUse`` hook refusing every tool that is not one we offered, by full name."""
    allowed = {PREFIX + w for w in offered}

    async def only_ours(input_data, _tool_use_id, _context):
        if input_data.get("tool_name") in allowed:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Only the tools you were given exist. "
                "Call one of those.",
            }
        }

    return HookMatcher(hooks=[only_ours])


def child_env(api_key: str, no_thinking: bool) -> dict[str, str]:
    """``ClaudeAgentOptions.env`` for the CLI child.

    The SDK lays this OVER a copy of this process's whole environment (it cannot be replaced), so
    every variable the allowlist (``core.child_env``) would drop is set to an empty string here:
    the child never sees an ``ONW_*`` setting, a provider key or a cloud credential, only the
    names. ``ANTHROPIC_API_KEY`` is passed only when one is stored for this provider; without it
    the CLI uses the owner's own sign-in.
    """
    allowed = scrubbed_environ()
    env = {key: "" for key in os.environ if key not in allowed}
    env.update(allowed)
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    env["CI"] = env.get("CI") or "1"
    # Claude Code loads every MCP tool behind its own search tool once there are many; the
    # search tool is a built-in we turned off, so deferred tools would be unreachable.
    env["ENABLE_TOOL_SEARCH"] = "false"
    env["MAX_MCP_OUTPUT_TOKENS"] = _MAX_OUTPUT_TOKENS
    if no_thinking:
        env["MAX_THINKING_TOKENS"] = "0"
    return env


def tool_content(content: Any, is_error: bool) -> list[dict]:
    """A host ``tool_result`` payload as the MCP content list a result is replaced with."""
    parts: list[dict] = []
    if isinstance(content, str) or content is None:
        parts.append({"type": "text", "text": content or ""})
    else:
        for part in content if isinstance(content, list) else [content]:
            if not isinstance(part, dict):
                parts.append({"type": "text", "text": str(part)})
            elif part.get("type") == "image":
                source = part.get("source") or {}
                if source.get("type") in (None, "base64") and source.get("data"):
                    parts.append(
                        {
                            "type": "image",
                            "data": source["data"],
                            "mimeType": source.get("media_type") or "image/png",
                        }
                    )
            elif part.get("type") == "text":
                parts.append({"type": "text", "text": part.get("text") or ""})
            else:
                parts.append({"type": "text", "text": json.dumps(part, default=str)})
    if not parts:
        parts.append({"type": "text", "text": ""})
    if is_error and parts[0]["type"] == "text":
        parts[0] = {"type": "text", "text": "Tool error: " + parts[0]["text"]}
    return parts
