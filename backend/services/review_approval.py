"""Who may clear a transaction's review flag.

An Automation books its rows into the owner's review queue (``automation`` is not a trusted
writer — ``core.txn_sources.TRUSTED_WRITERS``) and may flag a row, never approve one. Approving
is the owner's glance; a run that clears the flag on what it just wrote empties the queue before
anyone has looked.

The signal is the audit scope's ``surface``. This is not a tool permission — it holds in every
approval mode, "Skip all approvals" included.
"""

from __future__ import annotations

from backend.core.audit_context import get_audit_scope
from backend.core.errors import ReviewApprovalRefusedError


def acting_as_automation() -> bool:
    scope = get_audit_scope()
    return scope is not None and scope.surface == "automation"


def refuse_automation_approval(txn_id: str, existing: dict, changes: dict) -> None:
    """Raise when this write would flip ``needs_review`` True → False under an Automation.

    Only the transition is refused: re-flagging is the safe direction, and clearing a flag the
    row does not carry changes nothing. A falsy value counts as a clear — the row builder
    coerces ``None`` to ``False``.
    """
    if "needs_review" not in changes or changes["needs_review"]:
        return
    if not existing.get("needs_review") or not acting_as_automation():
        return
    raise ReviewApprovalRefusedError(
        f"Refused: an Automation cannot approve a transaction — clearing needs_review is the "
        f"owner's decision, made from the review queue. {txn_id} stays flagged for review and "
        "NOTHING in this call was applied to it. If the rest of your edit is still wanted, "
        "send it again WITHOUT needs_review. Do not try to clear the flag again by any route: "
        "leaving the row flagged is the correct end state, not a failure to fix.",
        txn_id=txn_id,
    )
