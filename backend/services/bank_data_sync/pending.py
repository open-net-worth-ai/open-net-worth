"""Pending bank transactions (ADR 2026-11-13, Amendment 3). Cleared means settled at the bank,
whoever entered the row — so a pending card payment is booked UNCLEARED (it counts in the working
balance, not the cleared one), becomes CLEARED when the bank settles it, and comes off the book if
the bank drops it (a released hold, a cancelled payment). YNAB does the same.

The sync remembers each pending row it booked (``budget`` state ``pending``: ref → {txn_id,
account_id, date, amount}); that list is the only thing ever settled or removed here, so a row the
user typed is never touched.

Settling: the posted row is the same pending one when it keeps the pending ref (GoCardless often
does), names it (Plaid's ``pending_transaction_id`` → ``supersedes_ref``), or else, with neither
(Enable Banking), has the same amount on the same account a few days on. Same amount: the booking
is marked cleared and re-dated to the settled date, the posted ref linked to it. A different amount
(a tip added, a hotel hold settled lower): the pending booking is replaced by the settled one,
keeping the user's category, payee, memo and tags. Either way nothing is booked twice."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from backend.services import ledger as ledger_service
from backend.services.bank_data_sync import budget

logger = logging.getLogger(__name__)

_TOL = 0.005
# How far from the pending date a settled row may land and still be "the same one".
_BEFORE, _AFTER = timedelta(days=3), timedelta(days=14)
_CARRIED = ("category", "payee_id", "memo", "tags")


def tracked(provider: str) -> dict[str, dict]:
    return dict(budget.state(provider).get("pending") or {})


def save(provider: str, entries: dict[str, dict]) -> None:
    budget.update(provider, pending=entries)


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _match(entries: dict[str, dict], ref: str, row: dict) -> str | None:
    """The tracked pending ref this settled row is, or None."""
    account = row.get("linked_account_id")
    live = {k: e for k, e in entries.items() if not e.get("settled")}
    if ref in live and live[ref].get("account_id") == account:
        return ref
    old = row.get("supersedes_ref")
    if old and old in live and live[old].get("account_id") == account:
        return old
    posted, amount = _day(row.get("date")), float(row.get("amount") or 0)
    best: tuple[int, str] | None = None
    for key, e in entries.items():
        when = _day(e.get("date"))
        if e.get("settled") or e.get("account_id") != account or not when or not posted:
            continue
        if abs(float(e.get("amount") or 0) - amount) > _TOL:
            continue
        if not when - _BEFORE <= posted <= when + _AFTER:
            continue
        gap = abs((posted - when).days)
        if best is None or gap < best[0]:
            best = (gap, key)
    return best[1] if best else None


def settle(provider: str, ref: str, row: dict, entries: dict[str, dict]) -> str:
    """A posted row that may be a pending one this sync booked. Returns ``settled`` (the booking
    is now cleared; book nothing), ``replaced`` (the pending booking is gone and ``row`` carries
    its labels in ``_carry``; book the row) or ``none`` (not a pending one)."""
    key = _match(entries, ref, row)
    if key is None:
        return "none"
    entry = entries.pop(key)
    txn = ledger_service.transactions.find_by_id(entry.get("txn_id") or "")
    if not txn or txn.get("deleted"):
        return "none"
    if abs(float(entry.get("amount") or 0) - float(row.get("amount") or 0)) <= _TOL:
        if ref != key:
            ledger_service.attach_external_refs(
                txn["id"], row.get("external_refs") or [], reason="bank_data_sync: pending settled"
            )
            # Its pending ref now leaves the bank's list: remembered as settled, so that is
            # never read as the bank withdrawing a booked row (quiet_refs).
            entries[key] = {**entry, "settled": True}
        ledger_service.update_transaction(txn["id"], {"cleared": True, "date": row["date"]})
        return "settled"
    row["_carry"] = {k: txn.get(k) for k in _CARRIED if txn.get(k)}
    ledger_service.bulk_delete_transactions([txn["id"]], reject_identities=False)
    return "replaced"


def remember(entries: dict[str, dict], created: list[dict], pending_refs: dict[str, dict]) -> None:
    """Track each newly booked pending row by its ref."""
    for txn in created:
        for pair in txn.get("external_refs") or []:
            ref = str((pair or {}).get("ref") or "")
            row = pending_refs.get(ref)
            if row is not None:
                entries[ref] = {
                    "txn_id": txn["id"],
                    "account_id": row.get("linked_account_id"),
                    "date": str(row.get("date") or "")[:10],
                    "amount": float(row.get("amount") or 0),
                }


_KEEP_SETTLED = timedelta(days=60)


def quiet_refs(provider: str) -> set[str]:
    """Refs whose leaving the bank's list is this module's business, not a review item: every
    pending row tracked, and every settled one's old pending ref."""
    return set(tracked(provider))


def drop_vanished(
    entries: dict[str, dict], seen: dict[str, set[str]], since: dict[str, str]
) -> int:
    """Remove each tracked pending booking the bank no longer lists on an account it was just
    read for, inside the window it was read over — a released hold or a cancelled payment.
    Taken out as the sync's own booking, never remembered as deleted. Returns how many."""
    from backend.core.user_time import user_today

    old = (user_today() - _KEEP_SETTLED).isoformat()
    for key in [k for k, e in entries.items() if e.get("settled") and str(e.get("date")) < old]:
        entries.pop(key)
    gone = [
        key
        for key, e in entries.items()
        if not e.get("settled")
        and e.get("account_id") in seen
        and key not in seen[e["account_id"]]
        and str(e.get("date") or "") >= str(since.get(e["account_id"]) or "")
    ]
    removed = 0
    for key in gone:
        txn_id = entries.pop(key).get("txn_id")
        txn = ledger_service.transactions.find_by_id(txn_id or "")
        if txn and not txn.get("deleted") and not txn.get("cleared"):
            ledger_service.bulk_delete_transactions([txn["id"]], reject_identities=False)
            removed += 1
    return removed
