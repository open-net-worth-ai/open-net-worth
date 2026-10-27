"""Cross-session memory tools — Lucius shared memory."""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of


class RememberFactInput(ToolInput):
    """Schema-of-record for ``remember_fact`` (see ``schema_from_model``)."""

    content: str = Field(
        description=("The fact to remember, as a concise statement (e.g. 'Wants to retire at 45')"),
    )
    tier: Optional[Literal["durable", "time_bound"]] = Field(
        default=None,
        description=(
            "'durable' = standing preference/truth; 'time_bound' = tied to a date/event. "
            "Default durable."
        ),
    )
    expires_at: Optional[str] = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) after which a time_bound fact is stale. Omit for durable facts."
        ),
    )


class ForgetFactInput(ToolInput):
    """Schema-of-record for ``forget_fact`` (see ``schema_from_model``)."""

    description: str = Field(
        description="Natural-language description of the fact to forget",
    )


class RecallMemoryInput(ToolInput):
    """Schema-of-record for ``recall_memory`` (see ``schema_from_model``)."""

    query: str = Field(
        description=(
            "Specific words for what to look up, e.g. 'apartment', 'retirement plans', or the "
            "nickname the user used"
        ),
    )
    limit: Optional[int] = Field(
        default=10, description="Max facts to return (default 10, max 25)."
    )


@tool(
    "remember_fact",
    description=(
        "Save a fact about the user to long-term memory so it persists across future "
        "conversations. Use when the user says 'remember that…', shares a standing "
        "preference/constraint, or states a dated plan worth recalling later. "
        "Keep the content a concise, self-contained statement."
    ),
    input_schema=schema_of(RememberFactInput, tool_name="remember_fact"),
    mutates=("memory",),
)
async def remember_fact(input_data: dict):
    from backend.ai.memory import MemoryDisabledError, add_explicit
    from backend.core.errors import ValidationError

    try:
        item = add_explicit(
            content=input_data["content"],
            section="other",
            topic=None,
            expires_at=input_data.get("expires_at"),
        )
    except MemoryDisabledError as exc:
        return json.dumps({"ok": False, "message": exc.message}, ensure_ascii=False), None
    except ValidationError as exc:
        return json.dumps({"ok": False, "message": exc.message}, ensure_ascii=False), None
    return json.dumps({"ok": True, "saved": item["content"]}, ensure_ascii=False), None


@tool(
    "forget_fact",
    description=(
        "Remove a remembered fact from long-term memory. Use when the user says 'forget…' "
        "or when a previously-remembered fact is clearly no longer true. "
        "Describe the fact to remove in natural language; the best match is deleted."
    ),
    input_schema=schema_of(ForgetFactInput, tool_name="forget_fact"),
    mutates=("memory",),
)
async def forget_fact(input_data: dict):
    from backend.ai.memory import MemoryDisabledError, remove_fact

    try:
        removed = await remove_fact(input_data["description"])
    except MemoryDisabledError as exc:
        return json.dumps({"ok": False, "message": exc.message}, ensure_ascii=False), None
    return json.dumps({"ok": removed}), None


@tool(
    "recall_memory",
    description=(
        "Search EVERYTHING you have remembered about the user, not just the facts shown in "
        "your prompt (that block holds only a subset). Call it when the user refers to "
        "something they may have told you before — 'my apartment', 'what do you know about "
        "my plans', a nickname for a person, account or goal — and the remembered facts in "
        "your prompt don't cover it. Returns the best keyword matches, newest first on a tie, "
        "each with its tier, source and dates. Reference only: use what you find to answer, "
        "never recite remembered facts back unprompted."
    ),
    input_schema=schema_of(RecallMemoryInput, tool_name="recall_memory"),
    mutates=(),
)
async def recall_memory(input_data: dict):
    from backend.ai.memory import MemoryDisabledError, recall_facts

    try:
        found = recall_facts(input_data.get("query", ""), input_data.get("limit") or 10)
    except MemoryDisabledError:
        message = "Memory is off (Settings → Memory), so there are no remembered facts to search."
        return json.dumps({"ok": False, "memory_enabled": False, "message": message}), None
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), None
    payload = {"ok": True, "showing": len(found["matches"]), **found}
    if found["total_matches"] > len(found["matches"]):
        payload["note"] = "More facts matched — narrow the query or raise limit (max 25)."
    elif not found["matches"]:
        payload["note"] = "Nothing remembered matches. Ask the user rather than guessing."
    return json.dumps(payload, ensure_ascii=False), None
