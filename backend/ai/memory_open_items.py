"""The guard on an Open item the extractor proposes: it needs the person's own words.

An unfinished chat always looks "open" to a model — what Lucius offered, what he is waiting for,
a statement line still to match — so the prompt's rule is backed by a check it cannot talk past.
An Open item the extractor writes (a new one, or an update that moves a fact into Open items)
must carry a ``quote``: words from one of the person's own messages in this conversation, where
they say they will do it or ask to be reminded. The quote is matched against what the person
typed — never Lucius's turns, nor an attachment, a harness note or other context the app adds
(``core.chat_context_markers.is_not_said``) — ignoring case and spacing, and must share a content
word with the item, so a stray "hmm, maybe" grounds nothing. At most one new Open item is kept
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
MAX_NEW_PER_CONVERSATION = 1

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
    quote = _norm(item.get("quote") or "")
    if len(quote) < QUOTE_MIN_CHARS or quote not in said:
        return False
    return bool(_content_words(quote) & _content_words(str(item.get("content") or "")))


def guard(ops: dict, said: str, current: dict[str, dict]) -> dict:
    """``ops`` with every Open item the person did not ask for dropped, and ``quote`` stripped
    from every item. ``said`` is ``user_words`` of the conversation; ``current`` maps fact ids
    to the facts as they stand (an update to a fact already in Open items needs no quote)."""
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


__all__ = ["guard", "user_words"]
