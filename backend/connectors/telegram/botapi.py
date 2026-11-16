"""Where the Telegram Bot API lives, and the one check a token gets before it is saved.

``API_BASE`` is a module constant so the end-to-end journey (``frontend/qa/
telegram-setup.mjs``) can point the whole connector at a fake Bot API by patching it in its
launcher — the same seam the GoCardless journey uses. Read it at CALL time (never copy it into
another module's globals at import), or that patch would miss.
"""

from __future__ import annotations

import logging

from backend.core.errors import ValidationError

log = logging.getLogger("backend.connectors.telegram.botapi")

API_BASE = "https://api.telegram.org"


class BotTokenRejected(ValidationError):
    """Telegram answered, and said this is not a bot token it knows."""


def base_urls() -> dict:
    """``base_url`` / ``base_file_url`` for a python-telegram-bot ``Bot`` or builder."""
    return {"base_url": f"{API_BASE}/bot", "base_file_url": f"{API_BASE}/file/bot"}


async def check_token(token: str) -> str | None:
    """Ask Telegram who this token belongs to (``getMe``).

    Returns the bot's username. Raises ``BotTokenRejected`` when Telegram answers that the token
    is not valid — the one case worth refusing a save over. Returns ``None`` when Telegram cannot
    be reached: the token may well be fine, and an offline save must still land (the connector
    retries in the background, exactly as it does at boot)."""
    from telegram import Bot
    from telegram.error import InvalidToken, TelegramError

    try:
        bot = Bot(token, **base_urls())
        async with bot:
            return bot.username
    except InvalidToken as exc:
        raise BotTokenRejected(
            "Telegram didn't accept this bot token. Copy it again from @BotFather — "
            "it looks like 123456789:ABC-DEF…"
        ) from exc
    except TelegramError as exc:
        log.warning("Telegram token check could not reach Telegram: %s", type(exc).__name__)
        return None
