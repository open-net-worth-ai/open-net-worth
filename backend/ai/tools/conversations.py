"""The user's earlier conversations — ``search_past_conversations`` finds one,
``read_past_conversation`` reads it back."""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import quote

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of

DEFAULT_LIMIT = 5
MAX_LIMIT = 10
SNIPPET_MAX_CHARS = 300
_QUERY_MAX_CHARS = 200
_MAX_TERMS = 8
_CONTEXT_CHARS = 200
_ELLIPSIS = "…"
_UNTITLED = "New conversation"
_LINK_HINT = (
    "When you mention a conversation you found, link it with its `link` so the user can open it, "
    "e.g. [Car insurance renewal](/lucius?conversation=…)."
)
_ATTACHMENT_REF = re.compile(r"ref_id:\s*([\w.-]+)")
_TERM = re.compile(r'"([^"]+)"|(\S+)')
_EDGE_PUNCT = "\"'`.,;:!?()[]{}<>“”‘’«»…"
# Dropped only while another word remains: "what did we say about the deposit" searches "deposit".
_FILLER = frozenset(
    "a an and are about as at be by did do does for from had has have how i in is it me my of on "
    "or our so that the their this to us was we were what when where which who why with you your "
    "say said talk talked discuss discussed decide decided tell told mention mentioned remember "
    "chat conversation earlier before ago".split()
)

READ_DEFAULT_LIMIT = 20
READ_MAX_LIMIT = 50
READ_MESSAGE_MAX_CHARS = 4000


class SearchPastConversationsInput(ToolInput):
    """Schema-of-record for ``search_past_conversations`` (see ``schema_from_model``)."""

    query: str = Field(
        description=(
            "Words to look for in earlier conversations, e.g. 'house deposit' or a name. Each "
            'word is matched on its own; put a phrase in "double quotes" to match it as written.'
        ),
    )
    limit: Optional[int] = Field(
        default=DEFAULT_LIMIT,
        description=f"Max conversations to return (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
    )


class ReadPastConversationInput(ToolInput):
    """Schema-of-record for ``read_past_conversation`` (see ``schema_from_model``)."""

    session_id: str = Field(
        description="The conversation's id, as search_past_conversations returns it.",
    )
    offset: Optional[int] = Field(
        default=0,
        description="Messages to skip from the start, to page through a long conversation.",
    )
    limit: Optional[int] = Field(
        default=READ_DEFAULT_LIMIT,
        description=f"Max messages to return (default {READ_DEFAULT_LIMIT}, max {READ_MAX_LIMIT}).",
    )


def query_terms(query: str) -> list[str]:
    """The words of ``query`` to match on their own, a quoted phrase kept whole, at most
    ``_MAX_TERMS``, with filler words dropped while anything else remains."""
    from backend.db.session_search import fold

    terms: list[str] = []
    for phrase, word in _TERM.findall(query):
        term = " ".join(phrase.split()) if phrase else word.strip(_EDGE_PUNCT)
        if term and fold(term) not in {fold(t) for t in terms}:
            terms.append(term)
    kept = [t for t in terms if fold(t) not in _FILLER]
    return (kept or terms)[:_MAX_TERMS]


def _snippet(said: str, terms: list[str], *, cut_before: bool, cut_after: bool) -> str:
    """At most ``SNIPPET_MAX_CHARS`` including the ellipses, centred on the earliest term, an
    ellipsis only where the message really continues."""
    from backend.db.session_search import fold

    flat = " ".join(said.split())
    size = len(flat)
    folded = fold(flat)
    found = [(folded.find(fold(t)), len(t)) for t in terms if fold(t) in folded]
    at, width = min(found) if found else (0, 0)
    start = max(0, min(at - (SNIPPET_MAX_CHARS - width) // 2, size - SNIPPET_MAX_CHARS))
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

    return any(private_refusal(ref) for b in blocks for ref in _ATTACHMENT_REF.findall(b or ""))


def conversation_link(session_id: str) -> str:
    """The in-app path that opens ``session_id`` — a plain click on it in a reply switches the chat."""
    return f"/lucius?conversation={quote(session_id, safe='')}"


def _refusal(message: str) -> tuple[str, None]:
    return json.dumps({"ok": False, "message": message}, ensure_ascii=False), None


def _chat_search_off() -> Optional[tuple[str, None]]:
    from backend.db.memory_settings_store import MemorySettingsStore

    if MemorySettingsStore().search_chats():
        return None
    return _refusal(
        "Searching and reading past conversations is off (Settings → Memory → Search and "
        "reference chats), so there is nothing to look up. Say so rather than guessing."
    )


def _bounded(value, default: int, low: int, high: int) -> int:
    return min(max(default if value is None else int(value), low), high)


def _result(hit: dict, terms: list[str]) -> Optional[dict]:
    """One hit as the model sees it, or None when nothing that matched survives the guards."""
    from backend.core.chat_context_markers import strip_user_msg_fence_tokens
    from backend.db.session_search import fold

    title = hit["title"] or _UNTITLED
    said = strip_user_msg_fence_tokens(hit["excerpt"]) if hit["excerpt"] is not None else ""
    if said and _shows_private_attachment(hit["head"], said):
        return None
    in_said = [t for t in terms if fold(t) in fold(said)]
    if not in_said and not any(fold(t) in fold(title) for t in terms):
        return None
    result = {
        "session_id": hit["session_id"],
        "title": title,
        "link": conversation_link(hit["session_id"]),
        "when": hit["when"],
        "matched": [t for t in terms if fold(t) in hit["matched"]],
    }
    if in_said:
        result["snippet"] = _snippet(
            said, in_said, cut_before=hit["cut_before"], cut_after=hit["cut_after"]
        )
    return result


@tool(
    "search_past_conversations",
    description=(
        "Search the user's earlier conversations with you by keyword. Call it when the user "
        "refers to something said in a previous chat — 'what we discussed last week', 'the plan "
        "from before' — and the current conversation and your memory don't cover it. Each word "
        "is matched on its own, anywhere in a conversation or its title; the conversations "
        "holding the most of your words come back, newest first, one per chat, with its title, "
        "date, the words it holds and a short snippet. Search with a few distinctive words "
        "(a name, an amount, a topic), not a sentence. Searches only what was typed and what "
        "you replied in the conversations listed under Chats (Captures too, though for a "
        "Capture only his reply, not the app's own prompt) — never attachments, tool output, "
        "or the context the app adds to a message. To see more than the snippet, open the "
        "conversation with read_past_conversation. Each result carries a `link`: put it in your "
        "reply as a markdown link on the conversation's title, so the user can open that chat. Reference only: use what you find to answer, "
        "never recite old conversations back unprompted."
    ),
    input_schema=schema_of(SearchPastConversationsInput, tool_name="search_past_conversations"),
    mutates=(),
)
async def search_past_conversations(input_data: dict):
    from backend.core.chat_session import (
        INTERNAL_SESSION_IDS,
        INTERNAL_SESSION_PREFIXES,
        get_session_id,
    )
    from backend.db.engine import get_engine
    from backend.db.session_search import search_message_text

    if (off := _chat_search_off()) is not None:
        return off
    query = " ".join(str(input_data.get("query") or "").split())[:_QUERY_MAX_CHARS]
    terms = query_terms(query)
    if not terms:
        return _refusal("query is required: give the words to look for.")
    try:
        limit = _bounded(input_data.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT)
    except (TypeError, ValueError):
        return _refusal(f"limit must be a whole number from 1 to {MAX_LIMIT}.")

    hits = search_message_text(
        get_engine(),
        terms,
        limit=limit,
        exclude_session_id=get_session_id(),
        hidden_ids=tuple(INTERNAL_SESSION_IDS),
        hidden_prefixes=INTERNAL_SESSION_PREFIXES,
        context=_CONTEXT_CHARS,
    )
    results = [r for r in (_result(hit, terms) for hit in hits) if r is not None]
    payload: dict = {"ok": True, "searched_for": terms, "results": results}
    if results:
        payload["link_hint"] = _LINK_HINT
    if not results:
        payload["note"] = (
            "No earlier conversation holds any of these words. Try other words once; then ask "
            "the user rather than guessing."
        )
    elif len(results[0]["matched"]) < len(terms):
        payload["note"] = (
            "No conversation holds every word; these hold the most of them (see matched). "
            "Check the snippet fits before relying on it."
        )
    return json.dumps(payload, ensure_ascii=False), None


def _said_text(blocks: list[str]) -> tuple[str, bool]:
    from backend.core.chat_context_markers import strip_user_msg_fence_tokens

    cut = any(len(b) > READ_MESSAGE_MAX_CHARS for b in blocks)
    said = "\n\n".join(strip_user_msg_fence_tokens(b[:READ_MESSAGE_MAX_CHARS]) for b in blocks)
    if len(said) > READ_MESSAGE_MAX_CHARS:
        said, cut = said[:READ_MESSAGE_MAX_CHARS], True
    return (said + _ELLIPSIS if cut else said), cut


@tool(
    "read_past_conversation",
    description=(
        "Read one of the user's earlier conversations with you, oldest message first, by the "
        "session_id search_past_conversations returned. Call it when a snippet is not enough — "
        "the exact figure, wording or decision from that chat. Returns what was typed and what "
        "you replied (role 'assistant' is you), each message cut at "
        f"{READ_MESSAGE_MAX_CHARS:,} characters; never tool output, attachments or the context "
        "the app adds. Pages by offset when the conversation is long: `total` is its message "
        "count and `next_offset` is where the next page starts. Link the conversation in your "
        "reply with its `link`, as a markdown link on its title. Reference only: use what you "
        "find to answer, never recite old conversations back unprompted."
    ),
    input_schema=schema_of(ReadPastConversationInput, tool_name="read_past_conversation"),
    mutates=(),
)
async def read_past_conversation(input_data: dict):
    from backend.core.chat_session import is_internal_session
    from backend.db.engine import get_engine
    from backend.db.session_search import read_said_messages
    from backend.db.session_store import SessionStore

    if (off := _chat_search_off()) is not None:
        return off
    session_id = str(input_data.get("session_id") or "").strip()
    if not session_id:
        return _refusal("session_id is required: use one search_past_conversations returned.")
    try:
        offset = _bounded(input_data.get("offset"), 0, 0, 1_000_000)
        limit = _bounded(input_data.get("limit"), READ_DEFAULT_LIMIT, 1, READ_MAX_LIMIT)
    except (TypeError, ValueError):
        return _refusal(f"offset and limit must be whole numbers (limit 1 to {READ_MAX_LIMIT}).")
    meta = None if is_internal_session(session_id) else SessionStore().get_meta(session_id)
    if meta is None:
        return _refusal(
            f"No conversation {session_id!r}. Use a session_id search_past_conversations returned."
        )

    page = read_said_messages(
        get_engine(), session_id, offset=offset, limit=limit, max_chars=READ_MESSAGE_MAX_CHARS
    )
    messages = []
    for message in page["messages"]:
        if _shows_private_attachment(*message["blocks"]):
            continue
        said, cut = _said_text(message["blocks"])
        entry = {"role": message["role"], "when": message["at"], "text": said}
        if cut:
            entry["truncated"] = True
        messages.append(entry)
    payload: dict = {
        "ok": True,
        "session_id": session_id,
        "title": meta.get("title") or _UNTITLED,
        "link": conversation_link(session_id),
        "link_hint": _LINK_HINT,
        "total": page["total"],
        "offset": offset,
        "messages": messages,
    }
    if offset + len(page["messages"]) < page["total"]:
        payload["next_offset"] = offset + len(page["messages"])
    return json.dumps(payload, ensure_ascii=False), None
