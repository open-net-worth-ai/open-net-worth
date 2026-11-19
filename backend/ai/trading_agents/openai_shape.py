"""OpenAI chat-completions ⇄ ONW provider messages and events — pure, no I/O.

TradingAgents talks to the LLM bridge in OpenAI's chat format; every ONW provider speaks the
Anthropic-shaped messages and the normalised event stream that ``assistant.py`` builds and reads.
This module is the whole translation, so it is unit-tested without a server or a provider.

Pinned-commit facts it is built around (docs/specs/trading-agents-pinned-findings.md):
structured output arrives as ``tools=[<one function named after the schema>]`` with no
``tool_choice`` and is parsed from ``tool_calls``; ``response_format`` is translated too, as a
robustness feature. A model answer that makes no call is returned as plain text either way.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.core.errors import ValidationError

_MAX_CALL_ID = 40
_JSON_OBJECT_TOOL = "json_response"

_EMPTY_SCHEMA = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class OnwRequest:
    """What ``provider.stream_chat`` is called with, plus how to shape its answer."""

    system: str
    messages: list[dict]
    tools: list[dict]
    # The tool whose input IS the reply (``response_format``): returned as the message content.
    structured_tool: Optional[str] = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: Optional[float] = None
    cost_basis: Optional[str] = None
    # The endpoint itself said what the call cost (not a table estimate).
    cost_reported: bool = False

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens


@dataclass
class Reply:
    text: str = ""
    tool_uses: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: Optional[str] = None
    done: bool = False


# ── request: OpenAI → ONW ────────────────────────────────────────────────────────────────


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _text_of(content: Any, role: str) -> str:
    """The text of an OpenAI ``content`` (a string, or a list of typed parts)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValidationError(f"A {role} message's content must be text.")
    chunks: list[str] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") not in ("text", "input_text"):
            raise ValidationError("Only text content is supported.")
        chunks.append(str(part.get("text") or ""))
    return "".join(chunks)


def _arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise ValidationError("A tool call's arguments must be a JSON object.")
    return parsed


def _tool_calls(message: dict) -> list[dict]:
    calls = message.get("tool_calls")
    if calls is None:
        return []
    if not isinstance(calls, list) or not all(isinstance(c, dict) for c in calls):
        raise ValidationError("tool_calls must be a list of objects.")
    return calls


def _assistant_blocks(message: dict) -> list[dict]:
    blocks: list[dict] = []
    text = _text_of(message.get("content"), "assistant")
    if text:
        blocks.append({"type": "text", "text": text})
    for call in _tool_calls(message):
        function = call.get("function")
        if not isinstance(function, dict):
            raise ValidationError("A tool call needs a function object.")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValidationError("A tool call needs a function name.")
        blocks.append(
            {
                "type": "tool_use",
                "id": str(call.get("id") or new_call_id("call")),
                "name": name,
                "input": _arguments(function.get("arguments")),
            }
        )
    return blocks


def _blocks_of(message: dict) -> tuple[str, list[dict]]:
    """``(role, blocks)`` of one OpenAI message in ONW's two roles."""
    role = message.get("role")
    if role == "assistant":
        return "assistant", _assistant_blocks(message)
    if role == "tool":
        call_id = message.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise ValidationError("A tool message needs a tool_call_id.")
        content = _text_of(message.get("content"), "tool")
        return "user", [{"type": "tool_result", "tool_use_id": call_id, "content": content}]
    if role == "user":
        text = _text_of(message.get("content"), "user")
        return "user", [{"type": "text", "text": text}] if text else []
    raise ValidationError(f"Unsupported message role {role!r}.")


def _merge(turns: list[tuple[str, list[dict]]]) -> list[dict]:
    """Adjacent same-role turns become one message (tool results first, as providers require);
    a message of plain text collapses to a string."""
    merged: list[tuple[str, list[dict]]] = []
    for role, blocks in turns:
        if not blocks:
            continue
        if merged and merged[-1][0] == role:
            merged[-1][1].extend(blocks)
        else:
            merged.append((role, list(blocks)))
    out: list[dict] = []
    for role, blocks in merged:
        if role == "user":
            blocks.sort(key=lambda b: b["type"] != "tool_result")
        if all(b["type"] == "text" for b in blocks):
            out.append({"role": role, "content": "\n\n".join(b["text"] for b in blocks)})
        else:
            out.append({"role": role, "content": blocks})
    return out


def _tool_of(entry: Any) -> dict:
    function = entry.get("function") if isinstance(entry, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    if not isinstance(name, str) or not name:
        raise ValidationError("Only function tools are supported.")
    schema = function.get("parameters")
    return {
        "name": name,
        "description": str(function.get("description") or ""),
        "input_schema": schema if isinstance(schema, dict) and schema else dict(_EMPTY_SCHEMA),
    }


def _structured_tool(response_format: Any) -> Optional[dict]:
    """``response_format`` as a single tool whose input is the reply, or None for plain text."""
    kind = response_format.get("type") if isinstance(response_format, dict) else None
    if kind == "json_schema":
        spec = response_format.get("json_schema")
        if not isinstance(spec, dict):
            raise ValidationError("response_format json_schema needs a name and a schema.")
        schema = spec.get("schema")
        name = str(spec.get("name") or "").strip()
        if not name or not isinstance(schema, dict):
            raise ValidationError("response_format json_schema needs a name and a schema.")
        return {
            "name": name,
            "description": str(spec.get("description") or "Return the answer."),
            "input_schema": schema,
        }
    if kind == "json_object":
        return {
            "name": _JSON_OBJECT_TOOL,
            "description": "Return the answer as a JSON object.",
            "input_schema": {"type": "object", "additionalProperties": True},
        }
    if kind in (None, "text"):
        return None
    raise ValidationError(f"Unsupported response_format {kind!r}.")


def to_onw_request(body: dict) -> OnwRequest:
    """The provider call for one chat-completions request body.

    ``ValidationError`` for anything the bridge cannot honestly serve (streaming, images, a
    malformed tool call) — the caller turns that into a 400. Sampling parameters are ignored:
    the owner's model settings decide them, not the worker."""
    if body.get("stream"):
        raise ValidationError("Streaming is not supported; send stream:false.")
    raw = body.get("messages")
    if not isinstance(raw, list) or not raw:
        raise ValidationError("messages must be a non-empty list.")
    system: list[str] = []
    turns: list[tuple[str, list[dict]]] = []
    for message in raw:
        if not isinstance(message, dict):
            raise ValidationError("Each message must be an object.")
        if message.get("role") in ("system", "developer"):
            text = _text_of(message.get("content"), "system")
            if text:
                system.append(text)
        else:
            turns.append(_blocks_of(message))
    messages = _merge(turns)
    if not messages:
        raise ValidationError("messages must contain a user or assistant message.")
    listed = body.get("tools")
    if listed is not None and not isinstance(listed, list):
        raise ValidationError("tools must be a list.")
    tools = [_tool_of(t) for t in listed or []]
    structured = _structured_tool(body.get("response_format"))
    if structured is not None:
        tools = [*tools, structured]
        system.append(
            f"Reply by calling the `{structured['name']}` tool with your complete answer. "
            "Write no other text."
        )
    return OnwRequest(
        system="\n\n".join(system),
        messages=messages,
        tools=tools,
        structured_tool=structured["name"] if structured else None,
    )


# ── response: ONW events → OpenAI ────────────────────────────────────────────────────────


def new_call_id(prefix: str = "onwcall") -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class ReplyBuilder:
    """Folds one provider call's events into a ``Reply``. Events it does not know are ignored,
    so a provider's progress events (thinking, pills, drafts) never reach the worker."""

    def __init__(self) -> None:
        self.reply = Reply()
        self._usage_rows = 0

    def add(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "text_delta":
            self.reply.text += str(event.get("text") or "")
        elif kind == "tool_use":
            self.reply.tool_uses.append(
                {
                    "id": str(event.get("id") or ""),
                    "name": str(event.get("name") or ""),
                    "input": event.get("input") if isinstance(event.get("input"), dict) else {},
                }
            )
        elif kind == "usage":
            self._add_usage(event)
        elif kind == "done":
            self.reply.done = True
            self.reply.stop_reason = event.get("stop_reason") or self.reply.stop_reason

    def _add_usage(self, event: dict) -> None:
        usage = self.reply.usage
        usage.input_tokens += _int(event.get("input_tokens"))
        usage.output_tokens += _int(event.get("output_tokens"))
        usage.cache_creation_tokens += _int(event.get("cache_creation_tokens"))
        usage.cache_read_tokens += _int(event.get("cache_read_tokens"))
        cost = event.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            usage.cost_usd = (usage.cost_usd or 0.0) + float(cost)
        usage.cost_basis = event.get("cost_basis") or usage.cost_basis
        usage.cost_reported = usage.cost_reported or bool(event.get("cost_reported"))
        self._usage_rows += 1


def _unique_ids(tool_uses: list[dict]) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for use in tool_uses:
        candidate = use["id"][:_MAX_CALL_ID]
        if not candidate or candidate in seen:
            candidate = new_call_id("call")
        seen.add(candidate)
        ids.append(candidate)
    return ids


def _message(reply: Reply, request: OnwRequest) -> tuple[dict, str]:
    """The assistant message and its ``finish_reason``."""
    if request.structured_tool:
        for use in reply.tool_uses:
            if use["name"] == request.structured_tool:
                return {"role": "assistant", "content": json.dumps(use["input"])}, "stop"
    if reply.tool_uses and not request.structured_tool:
        calls = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": use["name"], "arguments": json.dumps(use["input"])},
            }
            for call_id, use in zip(_unique_ids(reply.tool_uses), reply.tool_uses)
        ]
        return {
            "role": "assistant",
            "content": reply.text or None,
            "tool_calls": calls,
        }, "tool_calls"
    finish = "length" if reply.stop_reason in ("max_tokens", "length") else "stop"
    return {"role": "assistant", "content": reply.text}, finish


def chat_completion(
    reply: Reply, request: OnwRequest, *, alias: str, call_id: str, created: int
) -> dict:
    """The OpenAI ``chat.completion`` body for a finished provider call."""
    message, finish = _message(reply, request)
    usage = reply.usage
    return {
        "id": call_id,
        "object": "chat.completion",
        "created": created,
        "model": alias,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.output_tokens,
            "total_tokens": usage.prompt_tokens + usage.output_tokens,
        },
    }


def models_list(aliases: list[str]) -> dict:
    return {
        "object": "list",
        "data": [{"id": a, "object": "model", "created": 0, "owned_by": "onw"} for a in aliases],
    }
