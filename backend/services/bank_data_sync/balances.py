"""The bank's own balance for each synced account (ADR 2026-11-13, Amendment 3). Every sync read
already brings it back with the transactions, so keeping it costs no read.

The bank's SETTLED figure (``cleared``: GoCardless and Enable Banking send it as a booked
balance, ``aggregation/balance_kinds``; Plaid's ``current`` holds no pending, so the runner reads
it as settled, ``Provider.balance_is_settled``) is compared with the account's CLEARED balance —
pending rows are booked uncleared (pending.py), so the two speak of the same money. A bank with
one undivided figure that may hold pending (``amount`` alone) is in step if it matches the
cleared OR the working balance. A card or loan's balance is owed money: the book holds it
negative, whichever way round the bank reports it — unless the card is IN CREDIT, which a
balance alone can't say (neither provider signs it), but its available credit can: more
available than the limit means the card holds the difference (Amendment 20). A card's
AVAILABLE figure (credit left) is never its balance: with only that, the balance owed is the
account's credit limit minus it, and with no limit set there is no figure (``needs_limit``) —
Amendment 19. A card with no limit set takes the one its bank states (``fill_limit``).

An account that held nothing but its Starting Balance when its history was first imported is set
to the bank's figure (YNAB does the same on linking). Any other account is never changed
silently: its row offers "Use the bank's balance", which moves the Starting Balance by the
difference when the user clicks it."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import Provider
from backend.services.bank_data_sync import budget

logger = logging.getLogger(__name__)

_TOL = 0.005
_LIABILITY = {"credit", "loan"}


def stored(provider: str) -> dict[str, dict]:
    return dict(budget.state(provider).get("balances") or {})


def _target(account: dict, amount: float, bank: Optional[dict] = None) -> float:
    if account.get("type") not in _LIABILITY:
        return amount
    return abs(amount) if _in_credit(account, bank or {}, amount) else -abs(amount)


def _in_credit(account: dict, bank: dict, amount: float) -> bool:
    """A card whose available credit exceeds its limit by exactly this balance holds it."""
    limit, available = account.get("credit_limit"), bank.get("available")
    if account.get("type") != "credit" or not limit or available is None:
        return False
    over = float(available) - float(limit)
    return over > _TOL and abs(abs(amount) - over) <= _TOL


def _settled(bank: dict) -> Optional[float]:
    """The bank's settled figure, when it says which figure is settled."""
    value = bank.get("cleared")
    return None if value is None else float(value)


def _only_available(bank: dict) -> bool:
    """The one figure the bank sent is an AVAILABLE one (``balance_kinds``): no booked and no
    expected balance, and ``amount`` is the available figure. For a card that is credit left,
    never what is owed (a user report, 2026-11-19: a card's credit left read as the
    balance)."""
    available = bank.get("available")
    if bank.get("cleared") is not None or bank.get("working") is not None or available is None:
        return False
    amount = bank.get("amount")
    return amount is None or abs(float(amount) - float(available)) <= _TOL


def bank_figure(account: dict, bank: Optional[dict]) -> tuple[Optional[float], bool, Optional[str]]:
    """``(target, settled, why_none)``: the bank's balance in the book's sign (a card or loan
    held negative), whether it is the settled figure, and, when there is none, why.

    A card whose bank sent only its available credit is owed ``limit − available`` when the
    account has a credit limit (in credit when available exceeds the limit); without one there
    is no figure (``needs_limit``) — the available credit is never used as the balance."""
    bank = bank or {}
    settled = _settled(bank)
    if settled is not None:
        return _target(account, settled, bank), True, None
    if account.get("type") == "credit" and _only_available(bank):
        limit = account.get("credit_limit")
        if not limit or float(limit) <= 0:
            return None, False, "needs_limit"
        return float(bank["available"]) - float(limit), False, None
    amount = bank.get("amount")
    if amount is None:
        return None, False, None
    return _target(account, float(amount), bank), False, None


def _books(account: dict) -> tuple[list[dict], Optional[str]]:
    txns = ledger_service.transactions.all()
    return txns, ledger_service.opening_date(account, txns)


def compare(account: dict, bank: Optional[dict]) -> dict:
    """``{bank_balance, cleared_balance, balance_state}``: ``match``, ``differs``,
    ``needs_limit`` (a card whose bank sent only its available credit, and no credit limit is
    set), or None when there's nothing to compare (no figure yet, another currency)."""
    target, settled, why = bank_figure(account, bank)
    if not bank or target is None:
        return {
            "bank_balance": None,
            "cleared_balance": None,
            "balance_state": why if bank else None,
        }
    txns, start = _books(account)
    cleared = round(ledger_service.cleared_account_balance(account, txns, start), 2)
    working = round(ledger_service.account_balance(account, txns, start), 2)
    target = round(target, 2)
    same_currency = not bank.get("currency") or bank["currency"] == account.get("currency")
    state = None
    if same_currency:
        in_step = abs(target - cleared) <= _TOL or (not settled and abs(target - working) <= _TOL)
        state = "match" if in_step else "differs"
    return {"bank_balance": target, "cleared_balance": cleared, "balance_state": state}


def use_bank(account_id: str, bank: dict) -> None:
    """Move the Starting Balance by the difference, so the CLEARED balance is the bank's."""
    account = ledger_service.accounts.find_by_id(account_id)
    if not account:
        return
    target, _settled_figure, why = bank_figure(account, bank)
    if target is None:
        if why == "needs_limit":
            raise ValidationError(
                "The bank sent only this card's available credit. Add the card's credit limit "
                "to its account, and the balance owed is worked out from it."
            )
        return
    txns, start = _books(account)
    cleared = ledger_service.cleared_account_balance(account, txns, start)
    delta = target - cleared
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


def fill_limit(account_id: str, bank: dict) -> bool:
    """A card with no credit limit set takes the one its bank states — never one already set,
    never a non-card's (on a current account the bank's figure is an overdraft)."""
    limit = bank.get("limit")
    account = ledger_service.accounts.find_by_id(account_id) or {}
    if not limit or account.get("type") != "credit" or account.get("credit_limit"):
        return False
    if (
        bank.get("currency")
        and str(bank["currency"]).upper() != str(account.get("currency") or "").upper()
    ):
        return False
    from backend.services import accounts as accounts_service

    try:
        accounts_service.update_account(account_id, {"credit_limit": round(abs(float(limit)), 2)})
    except Exception:  # noqa: BLE001 — a limit never costs the run its balances
        logger.warning("bank_data_sync: the card's credit limit couldn't be set", exc_info=True)
        return False
    return True


def record(p: Provider, read: dict[str, dict], empty_on_first_import: set[str]) -> None:
    """Keep what the bank said this run; set each empty first-imported account to it."""
    for account_id, bank in read.items():
        fill_limit(account_id, bank)
    keep = stored(p.key)
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for account_id, bank in read.items():
        keep[account_id] = {**bank, "at": at}
    budget.update(p.key, balances=keep)
    for account_id in empty_on_first_import & set(read):
        bank = read[account_id]
        if _settled(bank) is None and abs(float(bank.get("pending") or 0)) > _TOL:
            continue  # the one figure may hold pending: the row asks instead of guessing
        account = ledger_service.accounts.find_by_id(account_id) or {}
        if bank.get("currency") and bank["currency"] != account.get("currency"):
            continue
        if bank_figure(account, bank)[0] is None:
            continue  # a card the bank sent only its available credit for: never guessed
        use_bank(account_id, bank)
