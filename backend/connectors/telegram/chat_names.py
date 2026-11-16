"""Who each approved chat ID is, asked of Telegram when the setup form opens.

The allowlist stores only numbers — the number is what grants access. A name beside it is read
live (``getChat``) through the running bot and kept in this process's memory for a few minutes,
never stored: the connector's ``config``/``state`` may not hold Telegram's chat map, and a name
someone changes in Telegram should show up here without anything to re-sync.

A bot can only look up chats that have talked to it, so after switching to a new bot an
approved person shows no name until they open the new bot — the number still shows.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("backend.connectors.telegram.chat_names")

MAX_LOOKUPS = 50
_LOOKUP_TIMEOUT_S = 5.0
_TTL_S = 300.0

_cache: dict[str, tuple[str, float]] = {}
_cache_bot: int | None = None  # id() of the bot the cache was filled through


def display_label(who) -> str:
    """ "Ada Lovelace (@ada)" for a person, the title for a group; "" when Telegram gave neither."""
    if who is None:
        return ""
    name = " ".join(p for p in (getattr(who, "first_name", ""), getattr(who, "last_name", "")) if p)
    handle = getattr(who, "username", "") or ""
    if name and handle:
        return f"{name} (@{handle})"
    if name or handle:
        return name or f"@{handle}"
    return getattr(who, "title", "") or ""


def _valid_id(raw: str) -> bool:
    return raw.lstrip("-").isdigit()


async def _lookup(bot, chat_id: str) -> str:
    hit = _cache.get(chat_id)
    if hit and time.monotonic() - hit[1] < _TTL_S:
        return hit[0]
    try:
        chat = await asyncio.wait_for(bot.get_chat(int(chat_id)), _LOOKUP_TIMEOUT_S)
    except Exception as exc:  # unknown chat, network, timeout — the number alone still shows
        log.debug("telegram getChat %s failed: %s", chat_id, type(exc).__name__)
        return ""
    label = display_label(chat)
    _cache[chat_id] = (label, time.monotonic())
    return label


async def chat_labels(bot, chat_ids: list[str]) -> dict[str, str]:
    """``{chat_id: label}`` for the IDs Telegram could name; the rest are left out."""
    global _cache_bot
    if bot is None:
        return {}
    if _cache_bot != id(bot):
        _cache.clear()
        _cache_bot = id(bot)
    ids = [c for c in dict.fromkeys(chat_ids) if _valid_id(c)][:MAX_LOOKUPS]
    labels = await asyncio.gather(*(_lookup(bot, c) for c in ids))
    return {c: label for c, label in zip(ids, labels) if label}
