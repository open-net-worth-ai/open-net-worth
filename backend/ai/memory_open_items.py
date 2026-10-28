"""The guard on an Open item the extractor proposes: it needs the person's own words.

An unfinished chat always looks "open" to a model — what Lucius offered, what he is waiting for,
a statement line still to match — so the prompt's rule is backed by a check it cannot talk past.
An Open item the extractor writes (a new one, or an update that moves a fact into Open items)
must carry a ``quote``: words from one of the person's own messages in this conversation, where
they say they will do it or ask to be reminded. The quote is matched against what the person
typed — never Lucius's turns, nor an attachment, a harness note or other context the app adds
(``core.chat_context_markers.is_not_said``) — ignoring case and spacing, and must share a content
word with the item (in the message it comes from), so a stray "hmm, maybe" grounds nothing. At most one new Open item is kept
per extraction pass (one quiet conversation). ADR
``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 9).
"""

from __future__ import annotations

import logging
import re
import unicodedata

from backend.ai import memory_structure as ms
from backend.core.chat_context_markers import is_not_said, strip_user_msg_fence_tokens

logger = logging.getLogger(__name__)

QUOTE_MIN_CHARS = 8
MAX_NEW_PER_CONVERSATION = ms.RECENT_MAX_NEW_PER_PASS

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_CONTENT_WORD = re.compile(r"[^\W\d_]{4,}")
_STOPWORDS = frozenset(
    "about after again also back been come could does from have here into just like maybe "
    "might next once only over sure that their them then there they think this those want "
    "week what when which will with would your".split()
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).translate(_QUOTES).casefold()
    return re.sub(r"\s+", " ", text).strip(" \"'")


def _typed(message: dict) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        blocks = [content]
    elif isinstance(content, list):
        blocks = [
            b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
    else:
        blocks = []
    return [strip_user_msg_fence_tokens(b) for b in blocks if b.strip() and not is_not_said(b)]


def user_words(history: list[dict]) -> str:
    """Everything the person typed in ``history``, normalised, one block per line."""
    return "\n".join(
        _norm(block) for m in history if m.get("role") == "user" for block in _typed(m)
    )


def _content_words(text: str) -> set[str]:
    return {w for w in _CONTENT_WORD.findall(_norm(text)) if w not in _STOPWORDS}


def _grounded(item: dict, said: str) -> bool:
    """The quote is in what the person typed, and the message it comes from shares a content
    word with the item — the message, not the quote alone, since "can we come back to that?"
    points at what the same message just said."""
    quote = _norm(item.get("quote") or "")
    if len(quote) < QUOTE_MIN_CHARS:
        return False
    item_words = _content_words(str(item.get("content") or ""))
    return any(quote in line and _content_words(line) & item_words for line in said.split("\n"))


def guard(ops: dict, said: str, current: dict[str, dict], *, may_add: bool = True) -> dict:
    """``ops`` with every Recent entry the person did not ask for dropped, and ``quote`` stripped
    from every item. ``said`` is ``user_words`` of the conversation; ``current`` maps fact ids
    to the facts as they stand (an update to an entry already in Recent needs no quote).
    ``may_add`` is False for an automation run: its "user" turn is the automation's own prompt,
    not the person speaking, so it may close a Recent entry but never add one."""
    if not may_add:
        said = ""
    kept_new, opened = [], 0
    for item in ops.get("new") or []:
        if not isinstance(item, dict):
            continue
        if ms.normalise_section(item.get("section")) == ms.OPEN_ITEMS:
            if opened >= MAX_NEW_PER_CONVERSATION or not _grounded(item, said):
                logger.info("memory: dropped an open item the person did not ask for")
                continue
            opened += 1
        kept_new.append({k: v for k, v in item.items() if k != "quote"})
    kept_updates = []
    for item in ops.get("updates") or []:
        if not isinstance(item, dict):
            continue
        was = (current.get(str(item.get("id"))) or {}).get("section")
        into = ms.normalise_section(item.get("section")) == ms.OPEN_ITEMS
        if into and was != ms.OPEN_ITEMS and not _grounded(item, said):
            logger.info(
                "memory: kept %s out of open items, no words of the person's", item.get("id")
            )
            continue
        kept_updates.append({k: v for k, v in item.items() if k != "quote"})
    return {**ops, "new": kept_new, "updates": kept_updates}


def overflow(facts: list[dict]) -> list[str]:
    """Ids of the live Recent entries past ``RECENT_MAX_LIVE``, least recently touched first to
    go — the cap that keeps what every run carries short."""
    live = [f for f in facts if f.get("state") == "live" and f.get("section") == ms.OPEN_ITEMS]
    live.sort(key=lambda f: str(f.get("updated_at") or f.get("created_at") or ""), reverse=True)
    return [f["id"] for f in live[ms.RECENT_MAX_LIVE :]]


def close_recent(handles: list[str]) -> list[str]:
    """Close the live Recent entries the handles (or full ids) name, as ``resolved``: the run
    that did what an entry said marks it done, so no later run waits for a payment that came.
    Returns the closed entries' texts; a handle naming anything else closes nothing."""
    from backend.ai.memory_citations import resolve_handles
    from backend.ai.memory_write import apply_memory_change
    from backend.db import MemoryStore

    recent = [
        f
        for f in MemoryStore().active_facts()
        if f.get("state") == "live" and f.get("section") == ms.OPEN_ITEMS
    ]
    wanted = [str(h or "").strip() for h in handles if isinstance(h, str)]
    by_id = {f["id"]: f for f in recent}
    ids = [h for h in wanted if h in by_id]
    ids += [
        i for i in resolve_handles([h for h in wanted if h not in by_id], recent) if i not in ids
    ]
    if not ids:
        return []
    apply_memory_change(
        {"retire": [{"id": i, "reason": "resolved"} for i in ids]}, source="implicit", strict=False
    )
    return [by_id[i]["content"] for i in ids]


__all__ = ["close_recent", "guard", "overflow", "user_words"]
