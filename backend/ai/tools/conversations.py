"""Search the user's earlier conversations — ``search_past_conversations``."""

from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of

DEFAULT_LIMIT = 5
MAX_LIMIT = 10
SNIPPET_MAX_CHARS = 300
_QUERY_MAX_CHARS = 200
_CONTEXT_CHARS = 200
_ELLIPSIS = "…"
_UNTITLED = "New conversation"
_ATTACHMENT_REF = re.compile(r"ref_id:\s*([\w.-]+)")


class SearchPastConversationsInput(ToolInput):
    """Schema-of-record for ``search_past_conversations`` (see ``schema_from_model``)."""

    query: str = Field(
        description="Words to look for in earlier conversations, e.g. 'house deposit' or a name.",
    )
    limit: Optional[int] = Field(
        default=DEFAULT_LIMIT,
        description=f"Max conversations to return (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
    )


def _snippet(excerpt: str, query: str, *, cut_before: bool, cut_after: bool) -> str:
    """At most ``SNIPPET_MAX_CHARS`` including the ellipses, centred on the match, an ellipsis
    only where the message really continues."""
    flat = " ".join(excerpt.split())
    size = len(flat)
    at = max(flat.lower().find(query.lower()), 0)
    start = max(0, min(at - (SNIPPET_MAX_CHARS - len(query)) // 2, size - SNIPPET_MAX_CHARS))
    end = min(size, start + SNIPPET_MAX_CHARS)
    lead = start > 0 or cut_before
    tail = end < size or cut_after
    shed = (end - start) - (SNIPPET_MAX_CHARS - lead - tail)
    if shed > 0:
        if lead and tail:
            start, end = start + shed // 2, end - (shed - shed // 2)
        elif lead:
            start += shed
        else:
            end -= shed
    return (_ELLIPSIS if lead else "") + flat[start:end] + (_ELLIPSIS if tail else "")


def _shows_private_attachment(*blocks: str) -> bool:
    """Defence in depth behind the SQL: a block naming a Private attachment's ref_id is dropped."""
    from backend.services.attachments import private_refusal

    return any(private_refusal(ref) for b in blocks for ref in _ATTACHMENT_REF.findall(b))


def _refusal(message: str) -> tuple[str, None]:
    return json.dumps({"ok": False, "message": message}, ensure_ascii=False), None


@tool(
    "search_past_conversations",
    description=(
        "Search the user's earlier conversations with you by keyword. Call it when the user "
        "refers to something said in a previous chat — 'what we discussed last week', 'the plan "
        "from before' — and the current conversation and your memory don't cover it. Returns the "
        "newest matching conversations first, one per chat, each with its title, date and a "
        "short snippet around the match. Searches only what was typed and what you replied "
        "in the conversations listed under Chats (Captures included) — never attachments, "
        "tool output, or the context the app adds to a message. Reference only: use what you find to answer, never recite "
        "old conversations back unprompted."
    ),
    input_schema=schema_of(SearchPastConversationsInput, tool_name="search_past_conversations"),
    mutates=(),
)
async def search_past_conversations(input_data: dict):
    from backend.core.chat_context_markers import strip_user_msg_fence_tokens
    from backend.core.chat_session import (
        INTERNAL_SESSION_IDS,
        INTERNAL_SESSION_PREFIXES,
        get_session_id,
    )
    from backend.db.engine import get_engine
    from backend.db.memory_settings_store import MemorySettingsStore
    from backend.db.session_search import fold, search_message_text

    if not MemorySettingsStore().search_chats():
        return _refusal(
            "Searching past conversations is off (Settings → Memory → Search and reference "
            "chats), so there is nothing to look up. Say so rather than guessing."
        )
    query = " ".join(str(input_data.get("query") or "").split())[:_QUERY_MAX_CHARS]
    if not query:
        return _refusal("query is required: give the words to look for.")
    try:
        asked = input_data.get("limit")
        limit = min(max(DEFAULT_LIMIT if asked is None else int(asked), 1), MAX_LIMIT)
    except (TypeError, ValueError):
        return _refusal(f"limit must be a whole number from 1 to {MAX_LIMIT}.")

    hits = search_message_text(
        get_engine(),
        query,
        limit=limit,
        exclude_session_id=get_session_id(),
        hidden_ids=tuple(INTERNAL_SESSION_IDS),
        hidden_prefixes=INTERNAL_SESSION_PREFIXES,
        context=_CONTEXT_CHARS,
    )
    results = []
    for hit in hits:
        said = strip_user_msg_fence_tokens(hit["excerpt"])
        if fold(query) not in fold(said) or _shows_private_attachment(hit["head"], said):
            continue
        results.append(
            {
                "session_id": hit["session_id"],
                "title": hit["title"] or _UNTITLED,
                "when": hit["when"],
                "snippet": _snippet(
                    said, query, cut_before=hit["cut_before"], cut_after=hit["cut_after"]
                ),
            }
        )
    payload: dict = {"ok": True, "results": results}
    if not results:
        payload["note"] = "No earlier conversation matches. Ask the user rather than guessing."
    return json.dumps(payload, ensure_ascii=False), None
