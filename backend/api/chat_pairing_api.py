"""Chat-channel pairing — the operator's approve/reject surface (chat-pairing).

When a chat channel runs the ``pairing`` access policy, an UNKNOWN sender's first message is
recorded as a pending request (with a pairing code) by the inbound seam and gets ONLY a
code reply — no Lucius turn. The operator reviews + approves it HERE:

  * ``GET  /api/chat-pairing[?connector=]`` — list pending requests (code + sender + when).
  * ``POST /api/chat-pairing/approve {connector, sender_id}`` — append the sender to that
    channel's allowlist (so their NEXT message proceeds normally) + clear the pending row.
  * ``POST /api/chat-pairing/reject  {connector, sender_id}`` — just clear the pending row
    (the sender stays unapproved; a later message re-records a fresh request).

All routes are gated by the app-wide ``require_auth`` dependency — this is a HUMAN-only
security action (it grants a sender access to the whole financial book), so there is
intentionally **no Lucius tool** (mirrors auth / connector-secret config). The actual
allowlist write goes through the connector's own ``add_allowed_sender`` (a config write,
checkpoint-irrelevant — the chat config stores are checkpoint-excluded secrets).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.pairing_store import PairingStore

log = logging.getLogger("backend.api.chat_pairing")

router = APIRouter()


def _resolve_adapter(connector: str):
    """Resolve a connector id to its ChatChannelAdapter instance (for the allowlist write).

    Only the bidirectional chat connector is pairable. An unknown id → None (the route
    returns 404). Imported lazily so this module stays cheap + avoids any connector import cycle.
    """
    if connector == "telegram":
        from backend.connectors.telegram.handlers import _TelegramAdapter

        # The Telegram adapter needs no live bot for the allowlist write (it reads/writes the
        # config store), so a None bot is fine here.
        return _TelegramAdapter(bot=None)
    return None


class PairingActionBody(BaseModel):
    connector: str
    sender_id: str


@router.get("/api/chat-pairing")
def list_pairing(connector: Optional[str] = None):
    """List pending pairing requests, optionally filtered to one ``connector``. Never 500s on a
    missing/empty store (returns ``{requests: []}``)."""
    try:
        rows = PairingStore().list_pending(connector)
    except Exception:  # a corrupt/missing file degrades to empty, never a 500
        log.exception("failed to list pairing requests")
        rows = []
    return {"requests": rows}


_WELCOME = (
    "You're in ✅ Ask me anything — for example, \"What's my net worth?\" "
    "Send /help to see what I can do."
)


async def _welcome(connector: str, sender_id: str) -> bool:
    """Tell a just-approved sender they're in — the pairing reply promised it. Best-effort: an
    approval never fails because the bot couldn't deliver a courtesy message."""
    if connector != "telegram":
        return False
    from backend.connectors.telegram import bot as telegram_bot

    return await telegram_bot.send_text(sender_id, _WELCOME)


@router.post("/api/chat-pairing/approve")
async def approve_pairing(body: PairingActionBody):
    """Approve a pending sender → append to the channel allowlist + clear the pending row.

    404 on an unknown connector; 404 if there is no pending request for that sender (so a stale
    double-approve is a clean not-found, not a silent allowlist add of an arbitrary id)."""
    adapter = _resolve_adapter(body.connector)
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"unknown chat connector: {body.connector}")
    store = PairingStore()
    if store.get(body.connector, body.sender_id) is None:
        raise HTTPException(status_code=404, detail="no pending pairing request for that sender")
    added = adapter.add_allowed_sender(body.sender_id)
    store.remove(body.connector, body.sender_id)
    log.info("approved pairing %s sender=%s (added=%s)", body.connector, body.sender_id, added)
    welcomed = await _welcome(body.connector, body.sender_id)
    return {
        "ok": True,
        "connector": body.connector,
        "sender_id": body.sender_id,
        "added": added,
        "welcomed": welcomed,
    }


@router.post("/api/chat-pairing/reject")
def reject_pairing(body: PairingActionBody):
    """Reject (clear) a pending request without granting access. Idempotent — a no-op if the row
    is already gone (``removed: false``). The sender stays unapproved; a later message re-records
    a fresh request."""
    removed = PairingStore().remove(body.connector, body.sender_id)
    log.info("rejected pairing %s sender=%s (removed=%s)", body.connector, body.sender_id, removed)
    return {
        "ok": True,
        "connector": body.connector,
        "sender_id": body.sender_id,
        "removed": removed,
    }
