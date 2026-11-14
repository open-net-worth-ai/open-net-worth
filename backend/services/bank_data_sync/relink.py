"""A bank account linked again under a new id (ADR 2026-11-13, Amendment 7).

Linking a bank afresh (a Plaid bank linked again instead of reconnected; a GoCardless or Enable
Banking consent started over) gives its accounts new ids and every row a new reference. Unlink
the old account and Link the new one, and the next read listed the whole window under refs the
book had never seen: all of it was booked a second time (UAT 2026-11-14, 73 rows).

The sync remembers which bank account it last read for each book account (``read_as``: book
account id → provider account id, ``runtime_state``). When that changes — or, with nothing
remembered, when the read lists none of the rows the account holds in its window
(``looks_relinked``) — the first read is a RE-LINK read: each row with no known ref is matched to a row already booked on that account
from this connector whose own refs the bank no longer lists — same signed amount, same day
first, then the nearest within ``_NEAR`` days, never one row twice — and the new ref is attached
to it instead of booking it. A pending row matches only a pending booking (its tracked entry
moves to the new ref, so it is not dropped as vanished), a settled row only a settled one. What
nothing matches is new and is booked as usual."""

from __future__ import annotations

import logging
from datetime import date

from backend.services import ledger as ledger_service
from backend.services.bank_data_sync import budget, pending

logger = logging.getLogger(__name__)

_TOL = 0.005
_NEAR = 3


def read_as(provider: str) -> dict[str, str]:
    return dict(budget.state(provider).get("read_as") or {})


def is_relinked(provider: str, account_id: str, provider_account_id: str) -> bool:
    """True when the book account was last read from ANOTHER bank account of this connector.
    Never read before (or read before this was remembered): not a re-link."""
    prior = read_as(provider).get(account_id)
    return bool(prior) and prior != provider_account_id


def looks_relinked(provider: str, account_id: str, rows: list[dict], since: str) -> bool:
    """A re-link seen in the read itself, for a book account with no ``read_as`` yet — linked
    before this was remembered, or whose bank failed every read since (an expired sign-in, then
    linked again: the 73-row UAT duplicate, live 2026-11-15). The bank lists rows in the window,
    the account holds rows from this connector dated in it, and not one of them is listed: the
    bank's ids changed. A normal read always lists some of what it booked last time."""
    listed = _listed(rows)
    if not listed:
        return False
    from backend.core.txn_sources import ext_ref_pairs

    openings = ledger_service.opening_txn_ids()
    held: set[str] = set()
    for t in ledger_service.transactions.all():
        if t.get("deleted") or t.get("id") in openings or str(t.get("date") or "")[:10] < since:
            continue
        if account_id in (t.get("from_account_id"), t.get("to_account_id")):
            held.update(ref for src, ref in ext_ref_pairs(t) if src == provider)
    return bool(held) and not held & listed


def _listed(rows: list[dict]) -> set[str]:
    return {_ref(r) for r in rows if _ref(r)} | {
        a for r in rows for a in r.get("ref_aliases") or [] if isinstance(a, str)
    }


def remember(provider: str, read: dict[str, str]) -> None:
    """Book account id → the bank account it was just read from. Kept only once the run's
    booking is done, so a run that failed matches again on the next one."""
    if not read:
        return
    budget.mutate(provider, lambda e: e.update(read_as={**(e.get("read_as") or {}), **read}))


def _ref(row: dict) -> str:
    refs = row.get("external_refs") or []
    return str(refs[0].get("ref") or "") if refs and isinstance(refs[0], dict) else ""


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _signed(txn: dict, account_id: str) -> float:
    if txn.get("to_account_id") == account_id and txn.get("to_amount") is not None:
        return abs(float(txn["to_amount"]))
    if txn.get("from_account_id") == account_id and txn.get("from_amount") is not None:
        return -abs(float(txn["from_amount"]))
    return 0.0


def _candidates(provider: str, accounts: set[str], listed: set[str]) -> dict[str, list[dict]]:
    """Per book account: the live rows booked from ``provider`` that the bank no longer lists
    under any of their refs — the old ids' rows."""
    from backend.core.txn_sources import ext_ref_pairs

    openings = ledger_service.opening_txn_ids()
    out: dict[str, list[dict]] = {a: [] for a in accounts}
    for t in ledger_service.transactions.all():
        if t.get("deleted") or t.get("id") in openings:
            continue
        refs = {ref for src, ref in ext_ref_pairs(t) if src == provider}
        if not refs or refs & listed:
            continue
        for a in accounts & {t.get("from_account_id"), t.get("to_account_id")}:
            out[a].append(t)
    return out


def carry(provider: str, rows: list[dict], relinked: set[str]) -> int:
    """Attach each re-linked account's unbooked rows to the bookings they already are. Marks a
    matched row ``booked_txn_id`` (+ ``_relinked``) so the booker leaves it alone. Returns how
    many were matched. Runs inside the booker's audit scope."""
    if not relinked:
        return 0
    listed = _listed(rows)
    pool = _candidates(provider, relinked, listed)
    entries = pending.tracked(provider)
    pending_by_txn = {e.get("txn_id"): k for k, e in entries.items() if not e.get("settled")}
    todo = [
        r
        for r in rows
        if r.get("linked_account_id") in relinked and not r.get("booked_txn_id") and _ref(r)
    ]
    used: set[str] = set()
    matched = 0
    for near in (0, _NEAR):
        for row in todo:
            if row.get("booked_txn_id"):
                continue
            txn = _best(row, pool.get(row["linked_account_id"]) or [], used, near, pending_by_txn)
            if txn is None:
                continue
            used.add(txn["id"])
            ledger_service.attach_external_refs(
                txn["id"], row.get("external_refs") or [], reason="bank_data_sync: linked again"
            )
            row["booked_txn_id"], row["_relinked"] = txn["id"], True
            old_key = pending_by_txn.get(txn["id"])
            if row.get("pending") and old_key in entries:
                entries[_ref(row)] = entries.pop(old_key)
            matched += 1
    pending.save(provider, entries)
    return matched


def _best(row: dict, pool: list[dict], used: set[str], near: int, pending_by_txn: dict):
    account, day = row["linked_account_id"], _day(row.get("date"))
    try:
        amount = float(row.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if day is None:
        return None
    best: tuple[int, dict] | None = None
    for t in pool:
        if t["id"] in used or bool(row.get("pending")) != (t["id"] in pending_by_txn):
            continue
        when = _day(t.get("date"))
        if when is None or abs(_signed(t, account) - amount) > _TOL:
            continue
        gap = abs((when - day).days)
        if gap <= near and (best is None or gap < best[0]):
            best = (gap, t)
    return best[1] if best else None
