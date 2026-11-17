"""The conversation a ``show_visual`` call reads its numbers from.

An inline visual never carries a number the model typed: each value is a reference to a tool
result already in the conversation, and the server copies the number out. The turn loop owns
the conversation (``assistant.run``'s ``history`` list), so it binds that LIVE list here before
any tool runs; the tool reads it. A list rather than a snapshot, because the results of earlier
rounds of the same turn are appended to it as the turn goes on.

ContextVar for the same reason as ``run_plan``: tools dispatch as asyncio Tasks, which copy the
context at creation, and a copied context still points at the same list object.
"""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator, Optional

_HISTORY: ContextVar[Optional[list]] = ContextVar("visual_sources_history", default=None)


@dataclass(frozen=True)
class ToolCall:
    """One executed tool call: what was asked and what came back (``result`` is the raw text)."""

    id: str
    name: str
    args: dict
    result: str


def bind(history: list) -> Token:
    return _HISTORY.set(history)


def reset(token: Token) -> None:
    _HISTORY.reset(token)


def bound() -> bool:
    return _HISTORY.get() is not None


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def calls() -> Iterator[ToolCall]:
    """Every executed tool call in the bound conversation, newest first. A call still awaiting
    approval has no result yet and is skipped; so is a result marked as an error."""
    history = _HISTORY.get() or []
    uses: dict[str, tuple[str, dict]] = {}
    results: list[tuple[str, str]] = []
    for entry in history:
        content = entry.get("content") if isinstance(entry, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                uses[str(block.get("id"))] = (str(block.get("name") or ""), args)
            elif block.get("type") == "tool_result" and not block.get("is_error"):
                results.append((str(block.get("tool_use_id")), _result_text(block.get("content"))))
    for use_id, text in reversed(results):
        if use_id in uses:
            name, args = uses[use_id]
            yield ToolCall(id=use_id, name=name, args=args, result=text)


def parse_result(call: ToolCall) -> Any:
    """The call's result as JSON, or raises ValueError naming the tool."""
    try:
        return json.loads(call.result)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{call.name}'s result is not structured data") from exc
