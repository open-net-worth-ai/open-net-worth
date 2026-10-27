"""Cross-session memory tools — Lucius shared memory."""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import Field

from backend.ai.memory_structure import (
    FACT_MAX_CHARS,
    OPEN_ITEM_DEFAULT_DAYS,
    OPEN_ITEM_MAX_DAYS,
    routing_guide_for_tool,
)
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of


class RememberFactInput(ToolInput):
    """Schema-of-record for ``remember_fact`` (see ``schema_from_model``)."""

    content: str = Field(
        description=(
            f"The fact, one concise self-contained statement of at most {FACT_MAX_CHARS} "
            "characters, in the third person (e.g. 'Rents a flat in Tampines')"
        ),
    )
    section: Literal["you", "money", "plans", "open_items", "other"] = Field(
        description=(
            "Where it is filed: you (about the user, how they like to work), money (their money "
            "life: housing, family, investing, tax, cards, income and work), plans (an "
            "intention or upcoming event), open_items (something to come back to; expires), "
            "other."
        ),
    )
    topic: Optional[str] = Field(
        default=None,
        description=(
            "A short name for the subject (e.g. 'Housing'); reuse one from the remembered facts "
            "when it fits. Omit for open_items."
        ),
    )
    expires_at: Optional[str] = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) after which the fact is stale. Open items default to "
            f"{OPEN_ITEM_DEFAULT_DAYS} days and never run past {OPEN_ITEM_MAX_DAYS}."
        ),
    )
    sensitive: Optional[bool] = Field(
        default=None,
        description=(
            "Set true for health, religion, political views, sexuality, ethnicity, or criminal "
            "and legal facts. Refused unless the user allows sensitive topics in Settings → Memory."
        ),
    )


class ForgetFactInput(ToolInput):
    """Schema-of-record for ``forget_fact`` (see ``schema_from_model``)."""

    description: str = Field(
        description="Natural-language description of the fact to forget",
    )


class RecallMemoryInput(ToolInput):
    """Schema-of-record for ``recall_memory`` (see ``schema_from_model``)."""

    query: Optional[str] = Field(
        default=None,
        description=(
            "Specific words for what to look up, e.g. 'apartment', 'retirement plans', or the "
            "nickname the user used. Optional when topic is given."
        ),
    )
    topic: Optional[str] = Field(
        default=None,
        description="A topic name from the remembered facts (e.g. 'Housing'): returns that topic's facts.",
    )
    limit: Optional[int] = Field(
        default=10, description="Max facts to return (default 10, max 25)."
    )


@tool(
    "remember_fact",
    description=(
        "Save a fact to long-term memory so it persists across future conversations. Use when "
        "the user says 'remember that…' or shares something worth recalling that no profile "
        "field, instruction, rule or record already holds. Keep it a concise, self-contained "
        "statement. " + routing_guide_for_tool()
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
            section=input_data.get("section"),
            topic=input_data.get("topic"),
            expires_at=input_data.get("expires_at"),
            sensitive=bool(input_data.get("sensitive")),
        )
    except MemoryDisabledError as exc:
        return json.dumps({"ok": False, "message": exc.message}, ensure_ascii=False), None
    except ValidationError as exc:
        return json.dumps({"ok": False, "message": exc.message}, ensure_ascii=False), None
    saved = {"ok": True, "saved": item["content"], "section": item["section"]}
    if item.get("topic"):
        saved["topic"] = item["topic"]
    return json.dumps(saved, ensure_ascii=False), None


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
        "your prompt, which may leave out older facts not yet sorted. Call it when the user "
        "refers to something they may have told you before — 'my apartment', 'what do you "
        "know about my plans', a nickname for a person, account or goal — and the remembered "
        "facts in your prompt don't cover it. Pass topic to list one topic's facts. Returns "
        "the best keyword matches, newest first on a tie, each with its section, topic, "
        "source and dates. Reference only: use what you find to answer, never recite "
        "remembered facts back unprompted."
    ),
    input_schema=schema_of(RecallMemoryInput, tool_name="recall_memory"),
    mutates=(),
)
async def recall_memory(input_data: dict):
    from backend.ai.memory import MemoryDisabledError, recall_facts

    try:
        found = recall_facts(
            input_data.get("query"), input_data.get("limit") or 10, topic=input_data.get("topic")
        )
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
