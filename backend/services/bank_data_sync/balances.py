"""The bank's own balance for each synced account (ADR 2026-11-13, Amendment 3). Every sync read
already brings it back with the transactions, so keeping it costs no read.

It is compared with the account's CLEARED balance — what the bank has settled, which is what a
bank's booked balance is too (pending rows are booked uncleared, pending.py). A bank whose figure
includes pending (Plaid's "current" may) matches the WORKING balance instead; both count as in
step. A card or loan's balance is owed money: the book holds it negative, whichever way round
the bank reports it.

An account that held nothing but its Starting Balance when its history was first imported is set
to the bank's figure (YNAB does the same on linking). Any other account is never changed
silently: its row offers "Use the bank's balance", which moves the Starting Balance by the
difference when the user clicks it."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import Provider
from backend.services.bank_data_sync import budget

_TOL = 0.005
_LIABILITY = {"credit", "loan"}
# Providers whose balance is the bank's BOOKED figure (pending left out): safe to set an empty
# account to on its first import even with pending rows about.
_BOOKED = {"enable_banking", "gocardless"}


def stored(provider: str) -> dict[str, dict]:
    return dict(budget.state(provider).get("balances") or {})


def _target(account: dict, amount: float) -> float:
    return -abs(amount) if account.get("type") in _LIABILITY else amount


def _books(account: dict) -> tuple[list[dict], Optional[str]]:
    txns = ledger_service.transactions.all()
    return txns, ledger_service.opening_date(account, txns)


def compare(account: dict, bank: Optional[dict]) -> dict:
    """``{bank_balance, cleared_balance, balance_state}``: ``match``, ``differs``, or None when
    there's nothing to compare (no figure yet, another currency)."""
    if not bank or bank.get("amount") is None:
        return {"bank_balance": None, "cleared_balance": None, "balance_state": None}
    txns, start = _books(account)
    cleared = round(ledger_service.cleared_account_balance(account, txns, start), 2)
    working = round(ledger_service.account_balance(account, txns, start), 2)
    target = round(_target(account, float(bank["amount"])), 2)
    same_currency = not bank.get("currency") or bank["currency"] == account.get("currency")
    state = None
    if same_currency:
        in_step = abs(target - cleared) <= _TOL or abs(target - working) <= _TOL
        state = "match" if in_step else "differs"
    return {"bank_balance": target, "cleared_balance": cleared, "balance_state": state}


def use_bank(account_id: str, bank: dict) -> None:
    """Move the Starting Balance by the difference, so the CLEARED balance is the bank's."""
    account = ledger_service.accounts.find_by_id(account_id)
    if not account:
        return
    txns, start = _books(account)
    cleared = ledger_service.cleared_account_balance(account, txns, start)
    delta = _target(account, float(bank["amount"])) - cleared
    if abs(delta) <= _TOL:
        return
    opening = ledger_service.opening_amount(account, txns)
    ledger_service.set_account_opening(account_id, round(opening + delta, 2), as_of=start)


def only_opening(account_ids: set[str]) -> set[str]:
    """The accounts holding nothing live but their Starting Balance — read BEFORE a run books."""
    if not account_ids:
        return set()
    openings = ledger_service.opening_txn_ids()
    busy: set[str] = set()
    for t in ledger_service.transactions.all():
        if t.get("deleted") or t.get("id") in openings:
            continue
        busy.update(a for a in (t.get("from_account_id"), t.get("to_account_id")) if a)
    return {a for a in account_ids if a not in busy}


def record(p: Provider, read: dict[str, dict], empty_on_first_import: set[str]) -> list[str]:
    """Keep what the bank said this run; set each empty first-imported account to it."""
    keep = stored(p.key)
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for account_id, bank in read.items():
        keep[account_id] = {**bank, "at": at}
    budget.update(p.key, balances=keep)
    notes: list[str] = []
    for account_id in empty_on_first_import & set(read):
        bank = read[account_id]
        if p.key not in _BOOKED and abs(float(bank.get("pending") or 0)) > _TOL:
            continue  # the figure may hold pending: the row asks instead of guessing
        account = ledger_service.accounts.find_by_id(account_id) or {}
        if bank.get("currency") and bank["currency"] != account.get("currency"):
            continue
        use_bank(account_id, bank)
    return notes
