"""Status + config endpoints for external integrations (Telegram, etc.).

The Telegram connector is UI-configurable: its bot token / allowed chats live in
``TelegramConfigStore`` (the ``telegram`` connectors row). The bot token
is **write-only** — ``GET /api/telegram/config`` returns only ``has_bot_token``; a blank
``bot_token`` in a ``PUT`` leaves the saved one unchanged. On save we hot-reload the bot
(stop+start) so the new config takes effect without a process restart.

Long-polling ONLY — a Telegram ``webhook_url`` config field existed at one point but was
never wired to a receiving endpoint (Telegram would have pushed updates nowhere), so it was
removed entirely 2026-08-22 rather than left as a live footgun. See git history if webhook
delivery is ever actually implemented.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.telegram_config_store import TelegramConfigStore

router = APIRouter()


class TelegramConfigPatch(BaseModel):
    enabled: Optional[bool] = None
    # Write-only — a blank/omitted value leaves the saved token unchanged.
    bot_token: Optional[str] = None
    # Accepts a list of ids OR a comma-separated string (normalized by the store).
    allowed_chat_ids: Optional[object] = None
    # Access policy: pairing|allowlist|open|disabled. Telegram DEFAULTS to ``allowlist``.
    access_policy: Optional[str] = None


@router.get("/api/integrations/telegram/status")
def telegram_status():
    """Compact runtime status for the connector card summary line (no secret)."""
    cfg = TelegramConfigStore().public()
    ids: List[str] = cfg.get("allowed_chat_ids") or []
    return {
        "configured": bool(cfg.get("configured")),
        "enabled": bool(cfg.get("enabled")),
        "allowed_count": len(ids),
    }


def _public_with_connection() -> dict:
    from backend.connectors.telegram import bot as telegram_bot

    return TelegramConfigStore().public() | {"connection": telegram_bot.connection_state()}


@router.get("/api/telegram/config")
def get_telegram_config():
    """Full (non-secret) Telegram config for the Configure form. The bot token is
    surfaced only as ``has_bot_token``; ``bot_username`` and ``connection``
    (connected | connecting | off) drive the form's "your bot is online" step."""
    return _public_with_connection()


@router.put("/api/telegram/config")
async def update_telegram_config(body: TelegramConfigPatch):
    """Persist a Telegram config change, then hot-reload the bot so token / allowed-chat /
    enabled changes take effect live (no restart). The bot token is write-only;
    a blank value keeps the saved one."""
    changes = body.model_dump(exclude_none=True)
    from backend.connectors.telegram import botapi

    token = (body.bot_token or "").strip()
    username = None
    if token:
        # A token Telegram refuses is never saved: a "Connected" bot that can't log in was the
        # silent failure at the end of the old setup. Unreachable is not refusal — that saves.
        try:
            username = await botapi.check_token(token)
        except botapi.BotTokenRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = TelegramConfigStore()
    store.update(changes)
    if token:
        store.set_bot_username(username)

    # Hot-reload the connector (stop + start) so the new config is live. Best-effort:
    # a reload failure (e.g. a bad token) must not fail the save — the config is already
    # persisted and the next restart picks it up. ONW_DISABLE_TELEGRAM is honored inside.
    from backend.connectors.telegram import bot as telegram_bot

    reloaded = await telegram_bot.reload_best_effort()

    return _public_with_connection() | {
        "_applied": list(changes.keys()),
        "reloaded": reloaded,
    }
