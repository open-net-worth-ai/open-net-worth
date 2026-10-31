"""One retry for an extracted fact over the hard length limit: ask the model to shorten it.

The extractor is asked for facts under ``FACT_TARGET_CHARS`` but cannot count characters, so a
fact that packs in context lands over ``FACT_MAX_CHARS`` now and then. The write choke refuses it
and the conversation's cursor still advances, so without this the fact was lost for good (a Citi
loan application, 293 characters, 2026-10-30). Every new and updated fact over the limit goes in
ONE call; a reply still over the limit is left as it was, refused by the choke and logged with its
text. The shortened wording goes through the choke's guards like any other.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from backend.ai import memory_structure as ms

logger = logging.getLogger(__name__)

Ask = Callable[[str], Awaitable[object]]


def _length(text) -> int:
    return len(" ".join(text.split())) if isinstance(text, str) else 0


def _over(ops: dict) -> list[dict]:
    return [
        item
        for key in ("new", "updates")
        for item in ops.get(key) or []
        if isinstance(item, dict) and _length(item.get("content")) > ms.FACT_MAX_CHARS
    ]


def shorten_prompt(texts: list[str]) -> str:
    listing = "\n".join(f"{n}. {t}" for n, t in enumerate(texts))
    return f"""Each memory note below is over the {ms.FACT_MAX_CHARS}-character limit. Rewrite
each one to say its lasting fact in under {ms.FACT_TARGET_CHARS} characters (never over
{ms.FACT_MAX_CHARS}). Keep the fact itself, with its names, amounts and dates exactly as written;
add nothing. Drop what a note does not need: detail the app already holds elsewhere (a
commitment's due dates and amounts, balances), history ("as of…", "decided…") and any request or
instruction addressed to the assistant.

{listing}

Return ONLY JSON: {{"short": [{{"n": 0, "content": "..."}}]}}"""


async def shorten_long_facts(ops: dict, ask: Ask) -> dict:
    """``ops`` with each new or updated fact over ``FACT_MAX_CHARS`` reworded once by ``ask``
    (prompt -> parsed JSON). Never raises: a failed call leaves the facts for the choke to refuse."""
    long_items = _over(ops)
    if not long_items:
        return ops
    texts = [" ".join(item["content"].split()) for item in long_items]
    try:
        parsed = await ask(shorten_prompt(texts))
    except Exception as exc:
        logger.warning("memory shorten call failed: %s", exc)
        parsed = None
    replies = parsed.get("short") if isinstance(parsed, dict) else None
    by_n = {
        r.get("n"): r.get("content")
        for r in replies or []
        if isinstance(r, dict) and isinstance(r.get("content"), str)
    }
    for n, item in enumerate(long_items):
        short = " ".join((by_n.get(n) or "").split())
        if 0 < len(short) <= ms.FACT_MAX_CHARS:
            logger.info(
                "memory: shortened a fact from %d to %d characters", len(texts[n]), len(short)
            )
            item["content"] = short
        else:
            logger.warning(
                "memory: a %d-character fact is still over %d after one retry: %s",
                len(texts[n]),
                ms.FACT_MAX_CHARS,
                texts[n],
            )
    return ops


__all__ = ["shorten_long_facts", "shorten_prompt"]
