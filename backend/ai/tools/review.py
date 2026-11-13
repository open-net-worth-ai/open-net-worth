"""Review-queue tools — approve flagged transactions.

Approving is the owner's decision, so it is its own tool instead of a field on the generic edit:
a deliberate act gets a deliberate call, its own permission level and its own confirm card. An
Automation run is refused at the ledger choke (``services/review_approval.py``,
ADR ``2026-10-04-an-automation-may-flag-a-transaction-never-approve-it``). Rejecting a flagged
row stays ``delete_transactions`` — it already records the rejection.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from pydantic import Field

from backend.ai.tools._shared import svc
from backend.ai.tools.registry import PreviewAdapter, tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError, ValidationError
from backend.services import labels, review_approval

logger = logging.getLogger(__name__)

_MAX_IDS = 100
_CARD_ROWS = 20
_TITLE_MAX = 60


def _row_title(txn_id: str, row: dict) -> str:
    """One line a person can recognise a row by: date · payee (or the raw bank string) · amount."""
    snap = svc.booked_row_snapshot(txn_id) or {}
    labelled = labels.label_txn(row, labels.label_maps(payees=True))
    who = (labelled.get("payee_name") or snap.get("description") or txn_id).strip()
    if len(who) > _TITLE_MAX:
        who = who[: _TITLE_MAX - 1].rstrip() + "…"
    parts = [snap.get("date") or row.get("date") or "", who]
    amount = snap.get("amount")
    if amount is not None:
        sign = "-" if amount < 0 else "+"
        parts.append(f"{sign}{snap.get('currency') or ''} {abs(amount):,.2f}".replace("  ", " "))
    return " · ".join(p for p in parts if p)


class _ApprovePreview:
    """Confirm-card preview: one row per transaction about to be approved, resolved NOW.

    Reuses the card's ``field_diff`` renderer (title + old → new), so a person sees which
    transactions they are signing off on instead of a list of opaque ids. Raising here is how a
    call that cannot be applied returns its reason to the agent with no card staged — both the
    Automation refusal and a call with nothing left to approve."""

    def build(self, args: dict) -> dict:
        ids = list(dict.fromkeys(args.get("transaction_ids") or []))
        items: list[dict] = []
        skipped = 0
        beyond_card = 0
        for txn_id in ids:
            row = svc.transactions.find_by_id(txn_id)
            if row is None or row.get("deleted") or not row.get("needs_review"):
                skipped += 1
                continue
            review_approval.refuse_automation_approval(txn_id, row, {"needs_review": False})
            if len(items) < _CARD_ROWS:
                items.append(
                    {
                        "id": txn_id,
                        "name": _row_title(txn_id, row),
                        "changes": {"review": {"old": "Needs review", "new": "Approved"}},
                    }
                )
            else:
                beyond_card += 1
        if not items:
            raise ValidationError(
                f"Nothing to approve: all {len(ids)} id(s) are already reviewed, deleted or "
                "unknown. No confirmation was needed and nothing changed."
            )
        if beyond_card:
            items.append(
                {
                    "id": "more",
                    "name": f"+ {beyond_card} more transaction(s)",
                    "changes": {"review": {"old": "Needs review", "new": "Approved"}},
                }
            )
        return {
            "kind": "field_diff",
            "data": {"items": items, "already_reviewed_or_missing": skipped},
            "raw_arguments": args,
        }

    def validate(self, preview: dict) -> Optional[str]:
        for item in (preview.get("data") or {}).get("items") or []:
            txn_id = item.get("id")
            if not txn_id or txn_id == "more":
                continue
            current = svc.transactions.find_by_id(txn_id)
            if current is None or current.get("deleted"):
                return f"transaction {txn_id!r} was deleted"
        return None


_APPROVE_PREVIEW: PreviewAdapter = _ApprovePreview()


class ApproveTransactionsInput(ToolInput):
    """Schema-of-record for ``approve_transactions`` (see ``schema_from_model``)."""

    transaction_ids: List[str] = Field(
        min_length=1,
        max_length=_MAX_IDS,
        description="The flagged transactions to approve — get_transactions needs_review=true "
        "lists them. Only rows where needs_review is true are changed.",
    )


@tool(
    "approve_transactions",
    description=(
        "Approve flagged transactions — clears their needs-review flag, exactly the review "
        "queue's Approve. Use it when the user asks to approve, clear or mark reviewed rows "
        "that an import, Lucius or an Automation flagged — list the queue with "
        "get_transactions needs_review=true. Correct any wrong field FIRST with "
        "update_transactions, then approve: "
        "approval means the user has seen the row as it now stands.\n\n"
        "If the call asks the user first, the card lists the rows. An Automation run "
        "can NEVER approve — it is refused and nothing changes; leave its rows flagged for the "
        "owner. Rows already reviewed, deleted or unknown are reported back, not errors.\n\n"
        "To REJECT a flagged row use delete_transactions — it also stops that same source event "
        "(email, statement line) being booked again."
    ),
    input_schema=schema_of(ApproveTransactionsInput, tool_name="approve_transactions"),
    mutates=("ledger",),
    preview_adapter=_APPROVE_PREVIEW,
)
async def approve_transactions(input_data: dict):
    ids = [i for i in (input_data.get("transaction_ids") or []) if i]
    try:
        result = svc.approve_transactions(ids)
    except OpenNetWorthError as e:
        logger.warning("tool approve_transactions: refused: %s", e)
        return json.dumps({"ok": False, "error": e.message or str(e)}, ensure_ascii=False), None
    logger.info(
        "tool approve_transactions: approved=%d already_reviewed=%d not_found=%d",
        result["count"],
        len(result["already_reviewed"]),
        len(result["not_found"]),
    )
    return json.dumps({"ok": True, **result}, ensure_ascii=False), None
