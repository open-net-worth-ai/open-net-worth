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


def _snippet(hit: dict, query: str) -> str:
    """At most ``SNIPPET_MAX_CHARS``, centred on the match, an ellipsis wherever text was cut."""
    flat = " ".join(hit["excerpt"].split())
    at = max(flat.lower().find(query.lower()), 0)
    start, end = 0, len(flat)
    if len(flat) > SNIPPET_MAX_CHARS:
        start = min(
            max(0, at - (SNIPPET_MAX_CHARS - len(query)) // 2), len(flat) - SNIPPET_MAX_CHARS
        )
        end = start + SNIPPET_MAX_CHARS
    cut_before = start > 0 or hit["cut_before"]
    cut_after = end < len(flat) or hit["cut_after"]
    body = flat[start:end]
    if cut_before:
        body = _ELLIPSIS + body[1:] if start > 0 else _ELLIPSIS + body
    if cut_after:
        body = body[:-1] + _ELLIPSIS if end < len(flat) else body + _ELLIPSIS
    return body[:SNIPPET_MAX_CHARS]


def _shows_private_attachment(excerpt: str) -> bool:
    from backend.services.attachments import private_refusal

    return any(private_refusal(ref) for ref in _ATTACHMENT_REF.findall(excerpt))


def _refusal(message: str) -> tuple[str, None]:
    return json.dumps({"ok": False, "message": message}, ensure_ascii=False), None


@tool(
    "search_past_conversations",
    description=(
        "Search the user's earlier conversations with you by keyword. Call it when the user "
        "refers to something said in a previous chat — 'what we discussed last week', 'the plan "
        "from before' — and the current conversation and your memory don't cover it. Returns the "
        "newest matching conversations, one per chat, each with its title, date and a short "
        "snippet around the match. Matches only the words typed and your replies, never "
        "attachments or tool output. Reference only: use what you find to answer, never recite "
        "old conversations back unprompted."
    ),
    input_schema=schema_of(SearchPastConversationsInput, tool_name="search_past_conversations"),
    mutates=(),
)
async def search_past_conversations(input_data: dict):
    from backend.ai.assistant import _INTERNAL_SESSION_IDS, _INTERNAL_SESSION_PREFIXES
    from backend.core.chat_session import get_session_id
    from backend.db.memory_settings_store import MemorySettingsStore
    from backend.db.session_store import SessionStore

    if not MemorySettingsStore().search_chats():
        return _refusal(
            "Searching past conversations is off (Settings → Memory → Search and reference "
            "chats), so there is nothing to look up. Say so rather than guessing."
        )
    query = " ".join(str(input_data.get("query") or "").split())[:_QUERY_MAX_CHARS]
    if not query:
        return _refusal("query is required: give the words to look for.")
    limit = min(max(int(input_data.get("limit") or DEFAULT_LIMIT), 1), MAX_LIMIT)

    hits = SessionStore().search_message_text(
        query,
        limit=limit,
        exclude_session_id=get_session_id(),
        hidden_ids=tuple(_INTERNAL_SESSION_IDS),
        hidden_prefixes=_INTERNAL_SESSION_PREFIXES,
        context=_CONTEXT_CHARS,
    )
    results = [
        {
            "session_id": h["session_id"],
            "title": h["title"],
            "when": h["when"],
            "snippet": _snippet(h, query),
        }
        for h in hits
        if not _shows_private_attachment(h["excerpt"])
    ]
    payload: dict = {"ok": True, "results": results}
    if not results:
        payload["note"] = "No earlier conversation matches. Ask the user rather than guessing."
    return json.dumps(payload, ensure_ascii=False), None
