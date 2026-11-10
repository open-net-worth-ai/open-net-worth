"""Provenance-linking tools (ADR 2026-09-07, CDS-5) — a small, dedicated module rather than
adding to ``ai/tools/ledger.py`` (already well over the ~400-line file-size guideline).

``add_transaction_refs`` is the model-facing half of the strong-duplicate refusal's third
exit: when a fetched provider/email row collides with a hand-typed transaction, the refusal
names this tool with the exact pair filled in (``services.ledger._attach_hint_for``), and this
tool links the ref onto the existing row instead of leaving it unbooked forever. The service
choke (``services.ledger.attach_external_refs``) owns validation, the "held by a different
live row" refusal, idempotency, and the audit/snapshot-promote side effects — this handler
only shapes the call and the response.

Security review (2026-09-07): a REQUIRED, sanitized ``reason`` (why this is the same event)
makes a model-decided identity link auditable rather than a bare "attach_refs" audit row with
no justification; a dedicated preview adapter renders the TARGET row (not just raw arguments)
so an approver can actually see what is being linked; local exceptions are caught and shaped
into the same ``{ok: false, error, duplicate_of, existing}`` contract the sibling ledger tools
return, instead of surfacing as a raw crash.
"""

from __future__ import annotations

import json
from typing import List, Optional

from pydantic import Field

from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools._shared import svc
from backend.ai.tools.registry import PreviewAdapter, tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import ConflictError, ValidationError
from backend.core.untrusted import sanitize_untrusted

_REASON_MAX_LEN = 300
# L4 (security review): an entity-set arity cap — a row absorbing dozens of provenance pairs
# in one call is not a realistic single event; bounds the write and the audit payload alike.
_REFS_MAX_ITEMS = 20


class TransactionRefItem(ToolInput):
    """One provenance pair for ``add_transaction_refs.refs`` (see ``schema_from_model``)."""

    source: Optional[str] = Field(
        default=None,
        description=(
            "The provider namespace, e.g. 'email', 'plaid', 'ibkr' — copy it verbatim from "
            "the duplicate refusal's own add_transaction_refs(...) suggestion. Null is a "
            "valid source for a legacy pair."
        ),
    )
    ref: str = Field(
        description=(
            "The provider's own stable id for this event (message_id, provider transaction "
            "id, ...) — copy it verbatim from the refusal, never compose one."
        )
    )


class AddTransactionRefsInput(ToolInput):
    """Schema-of-record for ``add_transaction_refs`` (see ``schema_from_model``)."""

    transaction_id: str = Field(
        description="The EXISTING row to link this provenance onto — the id the duplicate "
        "refusal named, never a new row's id."
    )
    refs: List[TransactionRefItem] = Field(
        min_length=1,
        max_length=_REFS_MAX_ITEMS,
        description="One or more {source, ref} pairs to attach. Almost always exactly the "
        "pair(s) a refused write just tried to book.",
    )
    reason: str = Field(
        max_length=_REASON_MAX_LEN,
        description=(
            "REQUIRED: why these are the same event — the specific amount/date/wording that "
            "convinced you (e.g. 'same $42.10 on 2026-09-01, same merchant wording as the "
            "email receipt'). This is a model-decided identity link with no human review "
            "before it writes, so it must be auditable — never a placeholder like 'matches'."
        ),
    )


class _AttachRefsPreview:
    """Confirm-card preview for ``add_transaction_refs`` (M5, security review): the generic
    literal-args card shows only the raw call arguments, which tells an approver nothing
    about WHICH row this links onto. Renders the target row's date/amount/description/
    account, the requested pairs, and the model's own reason."""

    def build(self, args: dict) -> dict:
        txn_id = args.get("transaction_id")
        target = None
        if txn_id:
            snap = svc.booked_row_snapshot(txn_id)
            if snap is not None:
                row = svc.transactions.find_by_id(txn_id) or {}
                account_id = row.get("from_account_id") or row.get("to_account_id")
                account = svc.accounts.find_by_id(account_id) if account_id else None
                target = {
                    "transaction_id": txn_id,
                    "date": snap.get("date"),
                    "amount": snap.get("amount"),
                    "currency": snap.get("currency"),
                    "description": snap.get("description"),
                    "account_name": (account or {}).get("name"),
                }
        refs = args.get("refs") if isinstance(args.get("refs"), list) else []
        return {
            "kind": "attach_refs",
            "data": {
                "target": target,
                "refs": [
                    {"source": r.get("source"), "ref": r.get("ref")}
                    for r in refs
                    if isinstance(r, dict)
                ],
                "reason": args.get("reason"),
            },
            "raw_arguments": args,
        }

    def validate(self, preview: dict) -> Optional[str]:
        target = (preview.get("data") or {}).get("target") or {}
        txn_id = target.get("transaction_id")
        if not txn_id:
            return None
        current = svc.transactions.find_by_id(txn_id)
        if current is None or current.get("deleted"):
            return f"transaction {txn_id!r} was deleted"
        return None


_ATTACH_REFS_PREVIEW: PreviewAdapter = _AttachRefsPreview()


@tool(
    "add_transaction_refs",
    description=(
        "Link an existing transaction to a provider or email record it was booked WITHOUT — "
        "use this when add_transactions/bulk_add_transactions/update_transactions refused a "
        "write as matching an existing row and it IS the same real-world event (the refusal's "
        "own hint names this call with the pair filled in). This teaches the system the two "
        "records describe one event, so a future re-fetch of that provider row books nothing "
        "new instead of colliding again.\n\n"
        "Only call this when you have genuinely confirmed it is the SAME event — never to "
        "silence a refusal you have not actually checked. If it is a different, separate "
        "charge, book it with allow_duplicate=true instead of attaching. Never use this to MOVE a ref "
        "between rows — it only ADDS to the target; detaching a wrong link is a user action "
        "in the transaction drawer, not something this tool does. `reason` is REQUIRED: state "
        "the specific amount/date/wording match — this writes without human review, so it "
        "must be auditable.\n\n"
        "Refuses when the pair already belongs to a DIFFERENT live transaction (naming it), "
        "when the target row is deleted, or when the target is the Starting Balance "
        "transaction. Re-attaching a pair the row already holds is a harmless no-op, "
        "reported back separately from newly attached pairs."
    ),
    input_schema=schema_of(AddTransactionRefsInput, tool_name="add_transaction_refs"),
    mutates=("ledger",),
    preview_adapter=_ATTACH_REFS_PREVIEW,
)
async def add_transaction_refs(input_data: dict):
    # Inert marker (security review R3) — mirrors every real ledger write in ai/tools/ledger.py
    # calling this unconditionally; self-guards on is_unattended() so it is a no-op on every
    # interactive/connector/skill turn today. See _confirm_gate.enforce_unattended_write's own
    # docstring for why it always returns None since 2026-08-10.
    guard = await _gate.enforce_unattended_write(
        "add_transaction_refs", account_id=None, kind=None, amount=None, category_id=None
    )
    if guard is not None:
        return guard

    transaction_id = input_data.get("transaction_id")
    refs = input_data.get("refs")
    reason = sanitize_untrusted(str(input_data.get("reason") or ""), max_len=_REASON_MAX_LEN)
    try:
        result = svc.attach_external_refs(transaction_id, refs, reason=reason)
    except (ValidationError, ConflictError) as exc:
        # R3: the same {ok, error, duplicate_of, existing} shape the sibling ledger tools
        # return on a duplicate refusal (ai/tools/ledger.py's DuplicateTransactionError
        # branch) — never a raw crash a model has to parse out of a stack trace.
        payload: dict = {"ok": False, "error": str(exc)}
        existing_id = (getattr(exc, "context", None) or {}).get("existing_id")
        if existing_id:
            payload["duplicate_of"] = existing_id
            payload["existing"] = svc.transactions.find_by_id(existing_id)
        return json.dumps(payload, ensure_ascii=False), None
    return json.dumps(result, ensure_ascii=False), None
