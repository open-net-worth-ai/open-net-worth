"""Which of the card's three answers the owner gave, kept with the conversation.

A confirm card is answered one of three ways, and the three are different facts (ADR
``2026-10-11-permissions-are-one-value-per-tool``): **approved** (this call runs once),
**always_allowed** (it runs AND the permission is stored — chat: the global value; Automation run:
that Automation's list) and **denied** (this call only, nothing stored). The transcript keeps only
what each call returned, which cannot tell an approval from an Always allow, so the answer is
stored here, on the session's own meta row, keyed by the ``tool_use_id`` the card was bound to.
``assistant.rehydrate_messages`` reads it to rebuild an ANSWERED card with its label after a
reload or when a run's conversation is opened later.

Only the answer is stored. What the card showed is rebuilt from the tool_use itself (its name and
arguments); what the answer DID is already stored where it acts (the permission table, the
Automation's list, the tool result).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

APPROVED = "approved"
ALWAYS_ALLOWED = "always_allowed"
DENIED = "denied"
RESOLUTIONS = frozenset({APPROVED, ALWAYS_ALLOWED, DENIED})

_KEY = "tool_resolutions"


def resolution_for(decision: str, *, always: bool) -> str | None:
    """The stored answer for a resolve request, or ``None`` for a decision that is neither."""
    if decision == "deny":
        return DENIED
    if decision == "approve":
        return ALWAYS_ALLOWED if always else APPROVED
    return None


def record(session_id: str, tool_use_id: str, resolution: str) -> None:
    """Remember ``resolution`` for ``tool_use_id``. Best-effort: a failure costs a label after a
    reload, and must never fail the answer it describes."""
    if not session_id or not tool_use_id or resolution not in RESOLUTIONS:
        return
    try:
        from backend.db.session_store import SessionStore

        store = SessionStore()
        current = dict((store.get_meta(session_id) or {}).get(_KEY) or {})
        current[tool_use_id] = resolution
        store.upsert_meta(session_id, {_KEY: current}, fail_loud=True)
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("tool resolution not recorded for %s", tool_use_id, exc_info=True)


def for_session(session_id: str) -> dict[str, str]:
    """``{tool_use_id: resolution}`` for every answered card of this conversation."""
    if not session_id:
        return {}
    try:
        from backend.db.session_store import SessionStore

        stored = (SessionStore().get_meta(session_id) or {}).get(_KEY) or {}
    except Exception:  # noqa: BLE001 — a conversation without labels still opens
        logger.warning("tool resolutions unreadable for %s", session_id, exc_info=True)
        return {}
    return {k: v for k, v in stored.items() if isinstance(k, str) and v in RESOLUTIONS}
