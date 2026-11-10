"""Read and write one conversation's approval MODE.

Stored as ``permission_mode`` on the session's own meta row — the same key an Automation
stores it under, because it is the same three-way concept and one vocabulary is worth more
than a tidy namespace. Deliberately NOT a ``tool_permissions`` row: a grant-store row is
durable, global and invisible from the composer, so putting it there would let *"skip
approvals while I import this statement"* quietly become *"skip approvals forever"* (RFC
``2026-08-27-per-conversation-approval-mode``). On the conversation it cannot outlive the
conversation.

Deliberately its own module rather than three more kwargs on
``assistant.upsert_session_meta``: that function is called from a dozen places, several of
them reachable from a turn, and this is a value only a human may write. A narrow surface is
what makes "no tool writes it" checkable rather than merely intended — see
``tests/test_conversation_approval.py``, which sweeps every registered tool schema for it.

Two human surfaces call it: the browser's REST route and a Telegram owner's ``/approvals`` (ADR
``2026-11-05-a-telegram-owner-sets-and-gets-the-approval-mode``). Neither is reachable by a tool.
"""

from __future__ import annotations

from backend.core.conversation_approval import AUTO, DEFAULT_MODE, MODES, normalize_mode
from backend.core.errors import ValidationError

_KEY = "permission_mode"


def owning_automation_mode(session_id: str) -> str | None:
    """The AUTOMATION's mode when this conversation is one of its runs, else ``None``.

    An Automation run is an ordinary durable conversation, so it has a session row like any
    other and would happily store a ``permission_mode`` nobody reads: inside a run the gate
    consults the Automation's verdict FIRST and only falls through to the conversation's when
    there is none (``_confirm_gate.should_gate``). So a picker bound to the session would have
    shown ``manual`` — the never-set default — for a run actually executing in ``auto``, and
    changing it would have done nothing at all. Reported live by the owner, 2026-08-27.

    Never raises: if this cannot be determined the caller falls back to the session's own
    value, which is the pre-existing behaviour.
    """
    if not session_id:
        return None
    try:
        from backend.services.automations.runs import automation_run_for_session

        owned = automation_run_for_session(session_id)
    except Exception:  # noqa: BLE001 — an undeterminable owner is not an error here
        return None
    if owned is None:
        return None
    automation, _run = owned
    return normalize_mode(automation.get("permission_mode"))


def get_mode(session_id: str) -> str:
    """The mode that ACTUALLY governs this conversation.

    For an Automation run that is the AUTOMATION's mode, not the session's — see
    ``owning_automation_mode``. For every other conversation, TWO different "nothing
    stored" cases resolve differently:

    - No row at all: this session has never been persisted, which only happens before its
      first message — i.e. it is a brand-new conversation. Reads as ``auto`` (and
      ``SessionStore._upsert`` bakes that same value into the row it creates, so this is
      just the read-side mirror of that write-side default, not a second source of truth).
    - A row exists but never stored a mode: an existing conversation from before this
      default existed, or one whose write failed. Reads as ``manual`` — an existing
      conversation's behaviour must never change silently.

    Never raises: an unreadable session and an unrecognised stored value both resolve to
    ``manual``, which is the mode that asks. A failure here must not silently widen what
    runs without a card.
    """
    if not session_id:
        return DEFAULT_MODE
    owned = owning_automation_mode(session_id)
    if owned is not None:
        return owned
    try:
        from backend.db.session_store import SessionStore

        meta = SessionStore().get_meta(session_id)
    except Exception:  # noqa: BLE001 — fail toward asking, never toward skip
        return DEFAULT_MODE
    if meta is None:
        return AUTO
    return normalize_mode(meta.get(_KEY))


def set_mode(session_id: str, mode: str) -> str:
    """Persist the mode for one conversation. Returns the stored value.

    Raises ``ValidationError`` on an unknown mode rather than coercing — this is the WRITE
    path, where a typo must be reported, not silently rounded to ``manual``. ``get_mode``
    coerces because a READ has to produce something usable no matter what is on disk.
    """
    if not session_id:
        raise ValidationError("session_id is required")
    if owning_automation_mode(session_id) is not None:
        # The Automation's own setting governs its runs, and the Automation editor is where
        # it is changed. Accepting a write here would store a value the gate never reads.
        raise ValidationError(
            "this conversation is an Automation run — change its approval mode on the Automation"
        )
    text = str(mode or "").strip().lower()
    if text not in MODES:
        raise ValidationError(f"permission_mode must be one of: {', '.join(MODES)}")

    from backend.db.session_store import SessionStore

    SessionStore().upsert_meta(session_id, {_KEY: text}, fail_loud=True)
    return text
