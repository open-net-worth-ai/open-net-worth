"""What a run is told about its own Automation beyond the instructions: the rule that woke it,
when it last ran to completion, and what that run sent the user.

The instructions say WHAT to do; the trigger alone says WHEN (ADR 2026-10-11, instructions
carry no timing). So a run must see the configured trigger — with the names of the accounts
and payees it points at — to know why it fired, and the start of the last run that COMPLETED,
to scope work "since the last run" whatever cadence the user picks later. A failed or stopped
run may have done nothing, so it never moves that mark.

These are facts about runs and know nothing of what a run works on. What to do with them is the
instructions' call (ADR 2026-11-05, a run's behaviour lives in its instructions).
"""

from __future__ import annotations

import json
import logging

from backend.db.automation_run_store import AutomationRunStore
from backend.db.session_store import SessionStore

from .options import condition_references
from .references import referenced_ids

logger = logging.getLogger(__name__)

_SUMMARY_MAX_CHARS = 2000
_MESSAGE_MAX_CHARS = 4000
_MESSAGES_MAX = 5


def configured_trigger(automation: dict) -> dict:
    """The stored trigger, plus ``names`` for every account and payee id it mentions."""
    trigger = dict(automation.get("trigger") or {})
    ids = referenced_ids(trigger)
    if not ids:
        return trigger
    references = condition_references()
    names = {
        row["id"]: row["label"] for rows in references.values() for row in rows if row["id"] in ids
    }
    return {**trigger, "names": names} if names else trigger


def last_completed_run(automation_id: str, run: dict) -> dict | None:
    """The latest run before ``run`` that completed — ``{"started_at"}`` — or None."""
    row = _latest_completed(automation_id, run)
    return {"started_at": row.get("started_at")} if row else None


def run_before_last(automation_id: str, run: dict) -> dict | None:
    """The completed run before the last one — ``{"started_at"}`` — or None."""
    last = _latest_completed(automation_id, run)
    if not last:
        return None
    row = _latest_completed(automation_id, last)
    return {"started_at": row.get("started_at")} if row else None


def last_run_record(automation_id: str, run: dict) -> dict | None:
    """What the last completed run said it did and what it sent, delivered or not — or None."""
    row = _latest_completed(automation_id, run)
    if not row:
        return None
    return {
        "summary": str(row.get("summary") or "")[:_SUMMARY_MAX_CHARS],
        "sent": _sent_messages(str(row.get("session_id") or "")),
    }


def _latest_completed(automation_id: str, run: dict) -> dict | None:
    return AutomationRunStore().latest_completed(
        automation_id, before=str(run.get("started_at") or ""), exclude_id=str(run.get("id") or "")
    )


def _sent_messages(session_id: str) -> list[dict]:
    """Every message the run's send tools carried, newest last. A record that can't be read must
    not stop the next run from starting, so it comes back empty."""
    try:
        transcript = SessionStore().get_transcript(session_id) if session_id else None
    except Exception:
        logger.exception("automation session %s: could not read what it sent", session_id)
        return []
    blocks = [
        block
        for message in transcript or []
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict)
    ]
    results = {
        b.get("tool_use_id"): b.get("content") for b in blocks if b.get("type") == "tool_result"
    }
    sent = [
        {
            "channel": str(block["name"]).removesuffix("_send_message"),
            "message": str(block["input"]["message"])[:_MESSAGE_MAX_CHARS],
            "delivered": _delivered(results.get(block.get("id"))),
        }
        for block in blocks
        if _is_send(block)
    ]
    return sent[-_MESSAGES_MAX:]


def _is_send(block: dict) -> bool:
    # `<connector>_send_message` is the enforced tool name for a channel, so a new one counts here.
    tool_input = block.get("input")
    return (
        block.get("type") == "tool_use"
        and str(block.get("name") or "").endswith("_send_message")
        and isinstance(tool_input, dict)
        and bool(tool_input.get("message"))
    )


def _delivered(result: object) -> bool:
    """A send tool reports ``ok`` only when the message got there; anything else did not."""
    try:
        return json.loads(result).get("ok") is True  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        return False
