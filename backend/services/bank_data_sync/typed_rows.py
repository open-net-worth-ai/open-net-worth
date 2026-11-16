"""A bank row that IS a transaction you typed yourself is matched to it, not asked about
(ADR 2026-11-13, Amendment 17).

The duplicate guard held back a bank row that looked like one already in the book, and each one
waited in Review; one typed in other words (same day, same amount) wasn't even held, and was
booked a second time. Actual Budget, Sure and GnuCash all attach the bank's id to a hand-typed
row with no question when the match leaves no doubt, and only ever a row with no bank id of its
own (a row a bank booked is matched by its ref, never guessed). So, with no question, when ALL of:

* the bank row is settled (a pending one changes or goes away; it is matched when it settles);
* the row in the book was typed: no external ref from any source, a single leg (income or
  expense, no splits, not an account's Starting Balance), live;
* it is on the bank row's account, in the same currency, for the same signed amount to the cent,
  on the same day;
* it is the ONLY such typed row on that account that day, and the bank row is the only row of
  the run wanting it — two identical coffees are never folded into one.

The bank's ref is attached (``attach_external_refs``, as accepting the review item does) and the
row is marked cleared, since the bank has settled it. Your category, payee and memo stay. Refs
are append-only, so Undo of the run leaves the ref on your row: it is the same transaction.
Card payments and transfers are paired first. Anything less certain goes on to the duplicate
guard as before, and waits in Review as a possible duplicate."""

from __future__ import annotations

import logging
from collections import Counter

from backend.services import ledger as ledger_service
from backend.services import rejected_identities

logger = logging.getLogger(__name__)


def _key(account_id: object, day: object, amount: object, currency: object) -> tuple | None:
    try:
        cents = round(float(amount) * 100)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return (str(account_id or ""), str(day or "")[:10], cents, str(currency or "").upper())


def _row_key(row: dict) -> tuple | None:
    return _key(
        row.get("linked_account_id"), row.get("date"), row.get("amount"), row.get("currency")
    )


def _typed_key(txn: dict) -> tuple | None:
    """The key of a hand-typed single-leg row, or None for anything else."""
    if txn.get("deleted") or txn.get("merged_into") or txn.get("external_refs"):
        return None
    if txn.get("splits") or txn.get("kind") not in ("income", "expense"):
        return None
    if txn.get("kind") == "income":
        return _key(
            txn.get("to_account_id"), txn.get("date"), txn.get("to_amount"), txn.get("to_currency")
        )
    try:
        out = -abs(float(txn.get("from_amount")))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return _key(txn.get("from_account_id"), txn.get("date"), out, txn.get("from_currency"))


def claim_all(candidates: list[tuple[dict, dict]]) -> tuple[list[tuple[dict, dict]], int]:
    """The run's ``(row, draft)`` candidates less each one matched to the typed row it is, and
    how many were. Runs after card payments and transfers are paired, before the ledger write."""
    keys = Counter(_row_key(r) for r, _ in candidates)
    days = sorted({k[1] for k in keys if k})
    if not days:
        return candidates, 0
    opening = ledger_service.opening_txn_ids()
    typed: Counter = Counter()
    ids: dict[tuple, str] = {}
    for t in ledger_service.transactions.query_txns(date_from=days[0], date_to=days[-1]):
        key = _typed_key(t)
        if key is not None and t["id"] not in opening:
            typed[key] += 1
            ids[key] = t["id"]
    kept: list[tuple[dict, dict]] = []
    matched = 0
    for row, draft in candidates:
        key = _row_key(row)
        if (
            key is not None
            and keys[key] == 1
            and typed[key] == 1
            and _bookable_bank_row(row, draft)
            and claim(row, ids[key])
        ):
            typed[key] = 0  # taken
            matched += 1
            continue
        kept.append((row, draft))
    return kept, matched


def _bookable_bank_row(row: dict, draft: dict) -> bool:
    """A settled single-leg bank row with a ref, that you never deleted."""
    if row.get("pending") or not row.get("external_refs") or draft.get("kind") == "transfer":
        return False
    return not rejected_identities.rejections_for([draft])


def claim(row: dict, txn_id: str) -> bool:
    """Attach the bank row's ref to the typed row and mark it cleared. False if it failed (the
    row then waits in Review as before)."""
    try:
        ledger_service.attach_external_refs(
            txn_id, row.get("external_refs") or [], reason="bank_data_sync: a row you typed"
        )
        txn = ledger_service.transactions.find_by_id(txn_id) or {}
        if not txn.get("cleared"):
            ledger_service.update_transaction(txn_id, {"cleared": True})
        return True
    except Exception:  # noqa: BLE001 — asked about in Review instead
        logger.exception("bank_data_sync: could not match a bank row to typed row %s", txn_id)
        return False
