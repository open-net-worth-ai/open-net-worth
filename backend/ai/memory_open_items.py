"""The guard on a Recent entry the extractor proposes: it needs the person's own words.

An unfinished chat always looks "open" to a model — what Lucius offered, what he is waiting for,
a statement line still to match — so the prompt's rule is backed by a check it cannot talk past.
A Recent entry the extractor writes (a new one, or an update that moves a fact into Recent) must
carry a ``quote``: words from one of the person's own messages in this conversation, where they
agree, decide or ask. The quote is matched against what the person typed — never Lucius's turns,
an Automation run's own prompt, an attachment, a harness note or other context the app adds
(``core.chat_context_markers.is_not_said``) — ignoring case and spacing, and the message it comes
from must share a content word with the item, so a stray "hmm, maybe" grounds nothing. At most
``RECENT_MAX_NEW_PER_PASS`` new entries are kept per extraction pass. A run that does what an entry
says closes it (``close_recent``); at most ``RECENT_MAX_LIVE`` stay live (``overflow``). ADR
``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendments 9 and 10).
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


def guard(ops: dict, said: str, current: dict[str, dict]) -> dict:
    """``ops`` with every Recent entry the person did not ask for dropped, and ``quote`` stripped
    from every item. ``said`` is ``user_words`` of what the person typed (never a run's own
    prompt); ``current`` maps fact ids to the facts as they stand (an update to an entry already
    in Recent needs no quote)."""
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


def _touched(fact: dict) -> str:
    stamps = (fact.get("updated_at"), fact.get("last_used_at"), fact.get("created_at"))
    return max((str(s) for s in stamps if s), default="")


def overflow(facts: list[dict]) -> list[str]:
    """Ids of the live Recent entries past ``RECENT_MAX_LIVE``, the least recently edited or
    used first to go — the cap that keeps what every run carries short. ``facts`` must already
    leave out what is expiring, or an expired entry would hold a slot."""
    live = [f for f in facts if f.get("state") == "live" and f.get("section") == ms.OPEN_ITEMS]
    live.sort(key=_touched, reverse=True)
    return [f["id"] for f in live[ms.RECENT_MAX_LIVE :]]


def close_recent(handles: list[str]) -> list[str]:
    """Close the live Recent entries the handles (or full ids) name, as ``resolved``: the run
    that did what an entry said marks it done, so no later run waits for a payment that came.
    Returns the closed entries' texts; a handle naming anything else closes nothing."""
    from backend.ai.memory_citations import resolve_handles
    from backend.ai.memory_write import apply_memory_change
    from backend.db import MemoryStore

    live = [f for f in MemoryStore().active_facts() if f.get("state") == "live"]
    by_id = {f["id"]: f for f in live if f.get("section") == ms.OPEN_ITEMS}
    wanted = [str(h or "").strip() for h in handles if isinstance(h, str)]
    ids = [h for h in wanted if h in by_id]
    # A handle is judged against every live fact, so a prefix another fact shares is ambiguous
    # and closes nothing; only then is the match held to Recent.
    named = resolve_handles([h for h in wanted if h not in by_id], live)
    ids += [i for i in named if i in by_id and i not in ids]
    if not ids:
        return []
    apply_memory_change(
        {"retire": [{"id": i, "reason": "resolved"} for i in ids]}, source="implicit", strict=False
    )
    return [by_id[i]["content"] for i in ids]


__all__ = ["close_recent", "guard", "overflow", "user_words"]
