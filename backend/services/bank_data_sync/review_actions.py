"""Deciding a review item — the only way a bank-side change reaches the ledger (ADR 2026-11-13).

* accept — ``modified``/``posted``: the booked row takes the bank's date, amount and wording
  (``ledger.update_transaction``, the edit choke; ``posted`` also links the posted ref);
  ``vanished``: the booked row is deleted (``bulk_delete_transactions``, which remembers the
  identity, so the bank re-listing it later asks again instead of re-booking);
  ``duplicate``: the bank row IS the booked one — its ref is linked to it
  (``attach_external_refs``), no money moves.
* dismiss — the ledger stays as it is. A dismissed ``duplicate``/``posted`` keeps holding its
  ref back, so the next run doesn't ask again.
* book — ``duplicate`` only: it is a different transaction after all; book it on its own.

Every write runs under the audit surface ``bank_data_sync`` as the user's act."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core import audit_context
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.bank_sync_store import BankSyncReviewStore
from backend.services import ledger as ledger_service
from backend.services.bank_data_sync.booker import SURFACE, draft_for

_ACCEPT_KINDS = ("modified", "vanished", "posted", "duplicate")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open(item_id: str) -> dict:
    item = BankSyncReviewStore().find_by_id(item_id)
    if item is None:
        raise NotFoundError("That review item doesn't exist.")
    if item.get("status") != "open":
        raise ConflictError("That review item was already decided.")
    return item


def _close(item: dict, status: str, resolution: str) -> dict:
    return BankSyncReviewStore().update_by_id(
        item["id"], {"status": status, "resolution": resolution, "resolved_at": _now()}
    )


def _live_txn(item: dict) -> dict:
    txn = ledger_service.transactions.find_by_id(item.get("txn_id") or "")
    if txn is None or txn.get("deleted"):
        raise ConflictError(
            "The booked transaction is gone (deleted or merged) — dismiss this item."
        )
    return txn


def _bank_changes(txn: dict, bank: dict) -> dict:
    """The edit that makes ``txn`` say what the bank now says. Refuses what is not a plain
    amount/date/wording change — a flipped direction or a transfer is for a hand edit."""
    amount = float(bank.get("amount") or 0)
    kind = txn.get("kind")
    if kind == "expense" and amount < 0:
        leg = {"from_amount": abs(amount)}
    elif kind == "income" and amount > 0:
        leg = {"to_amount": abs(amount)}
    else:
        raise ValidationError(
            "The bank's version changes the direction or the transaction isn't a plain expense "
            "or income — edit it by hand, then dismiss this item."
        )
    changes = {**leg}
    if bank.get("date"):
        changes["date"] = bank["date"]
    if bank.get("description"):
        changes["description"] = bank["description"]
    return changes


def _scoped(fn):
    token = audit_context.set_audit_scope("user", SURFACE, note="review decision")
    try:
        return fn()
    finally:
        audit_context.clear_audit_scope(token)


def accept(item_id: str) -> dict:
    item = _open(item_id)
    kind, detail = item.get("kind"), item.get("detail") or {}
    if kind not in _ACCEPT_KINDS:
        raise ValidationError("Unknown review item.")
    txn = _live_txn(item)

    def apply() -> str:
        if kind == "vanished":
            ledger_service.bulk_delete_transactions([txn["id"]])
            return "deleted"
        if kind == "duplicate":
            refs = (detail.get("row") or {}).get("external_refs") or []
            ledger_service.attach_external_refs(txn["id"], refs, reason="bank_data_sync review")
            return "linked"
        bank = detail.get("after") or {}
        ledger_service.update_transaction(txn["id"], _bank_changes(txn, bank))
        if kind == "posted":
            refs = (detail.get("row") or {}).get("external_refs") or []
            ledger_service.attach_external_refs(txn["id"], refs, reason="bank_data_sync review")
        return "applied"

    return _close(item, "accepted", _scoped(apply))


def dismiss(item_id: str) -> dict:
    return _close(_open(item_id), "dismissed", "kept")


def book_separately(item_id: str) -> dict:
    """A ``duplicate`` the user says is its own transaction: book it, past the duplicate guard
    only (a deleted identity is still refused)."""
    item = _open(item_id)
    if item.get("kind") != "duplicate":
        raise ValidationError("Only a possible duplicate can be booked separately.")
    draft = draft_for((item.get("detail") or {}).get("row") or {})
    if draft is None:
        raise ValidationError("This bank row has no account or amount to book.")

    def book() -> str:
        created, skipped = ledger_service.bulk_create_transactions(
            [draft], allow_duplicate=True, dedup_by_external_ref=True
        )
        if not created:
            reason = (skipped[0] if skipped else {}).get("_duplicate_reason")
            raise ConflictError(reason or "It couldn't be booked: that bank row is booked already.")
        return "booked"

    return _close(item, "accepted", _scoped(book))
