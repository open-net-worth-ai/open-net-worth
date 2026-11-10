"""How an investment or retirement account is valued: Brokerage total or Live holdings.

ADR 2026-11-10-an-investment-account-is-valued-by-its-brokerage-total-or-its-live-holdings.

Valuation stays ONE formula everywhere — holdings + ledger balance. What this module decides is
what the ledger balance MEANS:

* ``total`` (Brokerage total): the account is worth the brokerage's total. The ledger balance is
  the part of that total not tracked as holdings, so a holding added with no money moving is
  carved OUT of it (``around_lot_change``) and the total stays put. A linked account follows the
  total its connector reports (``account_value_sync``).
* ``holdings`` (Live holdings): the account is worth its holdings plus its cash, the ledger
  balance. Nothing here writes to it.

Every entry point is a no-op while the ``investment_value_source`` flag is off, so callers call
it unconditionally and the flag is read in this one module (docs/FEATURE_FLAGS.md rule 1).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from backend.core.errors import ValidationError
from backend.core.user_time import user_today

logger = logging.getLogger(__name__)

FLAG = "investment_value_source"
TOTAL = "total"
HOLDINGS = "holdings"
SOURCES = (TOTAL, HOLDINGS)
VALUED_TYPES = frozenset({"investment", "retirement"})
# Connectors whose account list carries a balance (each provider's `_account_to_common`).
# Enable Banking, GoCardless, Lunch Flow and Lunch Money report none; IBKR has no account list.
BALANCE_SOURCES = frozenset({"plaid", "simplefin", "yodlee", "alpaca", "ynab"})
# An untracked part this small (share of the total) is kept as cash on a switch to Live
# holdings; a larger one isn't cash and is zeroed.
SMALL_REMAINDER = 0.02
SURFACE = "account_value_sync"


def enabled() -> bool:
    from backend.services.feature_flags import enabled as flag_enabled

    return flag_enabled(FLAG)


def applies(account: dict | None) -> bool:
    return bool(account) and account.get("type") in VALUED_TYPES


def balance_link(account: dict) -> Optional[dict]:
    """The account's link to a balance-reporting connector, or None."""
    for entry in account.get("external_ids") or []:
        if isinstance(entry, dict) and entry.get("source") in BALANCE_SOURCES and entry.get("ref"):
            return entry
    return None


def siblings(account: dict, rows: list[dict]) -> list[dict]:
    """Other live accounts at the same institution — a connector's brokerage-wide total, synced
    into one of them, would count the others twice."""
    inst = account.get("institution_id")
    if not inst:
        return []
    return [
        r
        for r in rows
        if r.get("id") != account.get("id")
        and not r.get("archived")
        and r.get("institution_id") == inst
    ]


# ── holdings ──────────────────────────────────────────────────────────────────────────


def _vested_rows() -> list[dict]:
    from backend.services import positions as positions_service

    return [r for r in positions_service.hydrated() if r.get("vesting_state") != "unvested"]


def held_account_ids(rows: list[dict] | None = None) -> set[str]:
    """Accounts holding at least one vested unit — priced or not."""
    out: set[str] = set()
    for r in _vested_rows() if rows is None else rows:
        try:
            qty = float(r.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty > 0 and r.get("account_id"):
            out.add(r["account_id"])
    return out


def holdings_value(account: dict, rows: list[dict] | None = None) -> float:
    """The account's vested holdings at last-known prices, in the ACCOUNT's own currency — the
    same canonical per-row fold net worth uses (``ledger.row_base_value``). ``rows``: hydrated
    vested rows a caller valuing several accounts already read."""
    from backend.services import ledger as ledger_service

    currency = account_currency(account)
    total = sum(
        _row_value(r, currency, ledger_service.row_base_value)
        for r in (_vested_rows() if rows is None else rows)
        if r.get("account_id") == account.get("id")
    )
    return round(total, 2)


def account_currency(account: dict) -> str:
    from backend.core.fx import base_currency

    return (account.get("currency") or base_currency()).upper()


def _row_value(row: dict, currency: str, fold) -> float:
    """A row's market value, or — while it has no price yet (a ticker added a moment ago) — its
    cost, so a carve-out never takes 0 out of the total for a holding that will be priced on the
    next refresh (which would then count it on top)."""
    from backend.core.fx import to_base

    value = fold(row, currency)
    if value or row.get("price"):
        return value
    try:
        qty = float(row.get("quantity") or 0)
        cost = float(row.get("cost_basis") or 0)
    except (TypeError, ValueError):
        return 0.0
    if qty <= 0 or cost <= 0:
        return 0.0
    try:
        return to_base(qty * cost, row.get("currency") or currency, currency)
    except Exception:  # noqa: BLE001 — no rate for the cost currency: value it at 0, as net worth does
        return 0.0


# ── which source an account uses ──────────────────────────────────────────────────────


def default_source(account: dict, *, held: set[str], rows: list[dict]) -> str:
    """An account that already holds something keeps Live holdings, and so does a linked one at
    an institution split across several accounts. Everything else starts as Brokerage total."""
    if account.get("id") in held:
        return HOLDINGS
    if balance_link(account) and siblings(account, rows):
        return HOLDINGS
    return TOTAL


def source_of(
    account: dict, *, held: set[str] | None = None, rows: list[dict] | None = None
) -> str:
    stored = account.get("value_source")
    if stored in SOURCES:
        return stored
    if held is None:
        held = held_account_ids()
    if rows is None:
        from backend.services.accounts import all_account_rows

        rows = all_account_rows()
    return default_source(account, held=held, rows=rows)


def ensure_source(account: dict) -> str:
    """The account's source, STORED if it wasn't yet — the first write that depends on it fixes
    it, so a later holding can never flip a Brokerage total account to Live holdings."""
    source = source_of(account)
    if account.get("value_source") != source:
        from backend.services import ledger as ledger_service

        ledger_service.accounts.update_by_id(account["id"], {"value_source": source})
    return source


# ── booking ───────────────────────────────────────────────────────────────────────────


def book_adjustment(
    account: dict, amount: float, description: str, *, on_date: Optional[str] = None, conn=None
) -> Optional[dict]:
    """One cleared ``kind=adjustment`` moving the ledger balance by ``amount`` (native)."""
    from backend.core.fx import base_currency
    from backend.services import ledger as ledger_service

    amount = round(float(amount), 2)
    if amount == 0:
        return None
    currency = (account.get("currency") or base_currency()).upper()
    data = {
        "date": on_date or user_today().isoformat(),
        "kind": "adjustment",
        "description": description,
        "source": "manual",
        "cleared": True,
    }
    if amount > 0:
        data |= {"to_account_id": account["id"], "to_amount": amount, "to_currency": currency}
    else:
        data |= {
            "from_account_id": account["id"],
            "from_amount": -amount,
            "from_currency": currency,
        }
    return ledger_service.create_transaction(data, conn=conn)


def ledger_balance(account_id: str) -> float:
    from backend.services import ledger as ledger_service

    acct = ledger_service.accounts.find_by_id(account_id)
    txns = ledger_service.transactions.all()
    return ledger_service.account_balance(acct, txns, ledger_service.opening_date(acct, txns))


# ── serializer ────────────────────────────────────────────────────────────────────────


def decorate(accounts: list[dict]) -> list[dict]:
    """Add ``value_source``, ``value_sync`` and ``value_siblings`` to each investment/retirement
    account (flag on), and to a Brokerage total account ``value_breakdown`` — tracked holdings +
    untracked == total, native currency, last-known prices — so the screen and Lucius read the
    same split. Holdings are read once, and only when some account needs them."""
    if not any(applies(a) for a in accounts):
        return accounts
    if not enabled():
        # Off means the old response: the stored source and reading are kept (on → off → on
        # is safe) but never shown, or a client would draw the new card for a stamped account.
        return [_without_stored_fields(a) for a in accounts]
    from backend.services.accounts import all_account_rows

    rows = all_account_rows()
    vested: list[dict] | None = None
    if any(applies(a) for a in accounts):
        vested = _vested_rows()
    held = held_account_ids(vested)
    out = []
    for a in accounts:
        if not applies(a):
            out.append(a)
            continue
        source = source_of(a, held=held, rows=rows)
        row = {
            **a,
            "value_source": source,
            "value_sync": sync_view(a, source),
            "value_siblings": [r.get("name") or "" for r in siblings(a, rows)],
        }
        if source == TOTAL and "balance" in a:
            tracked = holdings_value(a, vested)
            untracked = round(float(a.get("balance") or 0), 2)
            row["value_breakdown"] = {
                "tracked_holdings": tracked,
                "untracked": untracked,
                "total": round(tracked + untracked, 2),
                "currency": a.get("currency"),
            }
        out.append(row)
    return out


def _without_stored_fields(account: dict) -> dict:
    if "value_source" not in account and "value_sync" not in account:
        return account
    return {k: v for k, v in account.items() if k not in ("value_source", "value_sync")}


def sync_view(account: dict, source: str) -> Optional[dict]:
    """What the account screen shows about its connector's total. None when it isn't linked to
    a connector that reports balances; ``status: waiting`` until the first reading lands."""
    from backend.services.accounts import provider_label

    link = balance_link(account)
    if link is None:
        return None
    stored = account.get("value_sync") or {}
    same_link = stored.get("source") == link.get("source") and stored.get("ref") == link.get("ref")
    view = dict(stored) if same_link else {"status": "waiting"}
    view["source"] = link.get("source")
    view["provider_label"] = provider_label(link.get("source"))
    view.pop("ref", None)
    if source == TOTAL and view.get("status") == "fresh" and _older_than(view.get("changed_at"), 7):
        view["status"] = "stale"
    return view


def _older_than(iso: Optional[str], days: int) -> bool:
    if not iso:
        return False
    try:
        when = datetime.fromisoformat(str(iso))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).days >= days


# ── lot writes: the carve-out ───────────────────────────────────────────────────────────


def around_lot_change(change: dict, dispatch: Callable[[dict], dict]) -> dict:
    """Run one ``apply_lot_change`` dispatch, keeping a Brokerage total account's total where it
    was when the write moves no money. A write with a cash leg needs nothing: that leg moves the
    total by exactly the money that came from, or went to, another account."""
    if not enabled():
        return dispatch(change)
    account = _lot_change_account(change)
    if not applies(account) or ensure_source(account) != TOTAL:
        return dispatch(change)
    change = _keep_proceeds_home(change, account)
    moves_money = _moves_money(change)
    before = None if moves_money else _value_or_none(account)
    result = dispatch(change)
    if not isinstance(result, dict) or result.get("error") or result.get("idempotent"):
        return result
    if moves_money or result.get("cash_leg_id") or result.get("transfer_to_account_id"):
        return result
    after = _value_or_none(account)
    if before is None or after is None:
        result["value_carve_out_error"] = _NO_RATE
        return result
    _note_unpriced(account)
    delta = round(after - before, 2)
    if delta == 0:
        return result
    description = "Holding tracked from the total" if delta > 0 else "Holding returned to the total"
    try:
        txn = book_adjustment(account, -delta, description, on_date=_carve_date(account, change))
    except Exception:  # noqa: BLE001 — the lot is written; say the total is off, never hide it
        logger.exception("account_value: carve-out failed for %s", account.get("id"))
        result["value_carve_out_error"] = _carve_failed(delta)
        return result
    result["value_carve_out"] = {
        "account_id": account["id"],
        "amount": -delta,
        "txn_id": (txn or {}).get("id"),
    }
    return result


def around_position_remove(position_id: str, remove: Callable[[], bool]) -> tuple[bool, dict]:
    """``remove_position`` on a Brokerage total account: removing a tracked holding returns its
    value to the untracked part, so the total stays — unless one of its lots carried a linked
    purchase, whose reversal moves the money back itself. Returns ``(removed, extra)``."""
    if not enabled():
        return remove(), {}
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.services import ledger as ledger_service

    pos = PositionStore().find_by_id(position_id) or {}
    account = ledger_service.accounts.find_by_id(pos.get("account_id") or "") if pos else None
    if not applies(account) or ensure_source(account) != TOTAL:
        return remove(), {}
    moves_money = any(
        lot.get("txn_id") for lot in LotStore().all() if lot.get("position_id") == position_id
    )
    before = None if moves_money else _value_or_none(account)
    removed = remove()
    if not removed or moves_money:
        return removed, {}
    after = _value_or_none(account)
    if before is None or after is None:
        return removed, {"value_carve_out_error": _NO_RATE}
    delta = round(after - before, 2)
    if delta == 0:
        return removed, {}
    try:
        txn = book_adjustment(account, -delta, "Holding returned to the total")
    except Exception:  # noqa: BLE001 — the holding is gone; say the total is off, never hide it
        logger.exception("account_value: carve-out failed for %s", account.get("id"))
        return removed, {"value_carve_out_error": _carve_failed(delta)}
    return removed, {
        "value_carve_out": {
            "account_id": account["id"],
            "amount": -delta,
            "txn_id": (txn or {}).get("id"),
        }
    }


def around_position_move(position_id: str, new_account_id: str, move: Callable[[], object]):
    """Moving a holding to another account moves no money, so each Brokerage total account on
    either side keeps its total: the one it left gets the value back into its untracked part,
    the one it joined has it carved out (UAT TRADE-18, owner decision 2026-11-10). Returns
    ``move()``'s own result; a carve that can't be booked is logged, the move stands."""
    if not enabled():
        return move()
    from backend.db.position_store import PositionStore
    from backend.services import ledger as ledger_service

    pos = PositionStore().find_by_id(position_id) or {}
    ids = {pos.get("account_id"), new_account_id} - {None, ""}
    sides = [ledger_service.accounts.find_by_id(i) for i in ids]
    sides = [a for a in sides if applies(a) and ensure_source(a) == TOTAL]
    if not sides or pos.get("account_id") == new_account_id:
        return move()
    before = {a["id"]: _value_or_none(a) for a in sides}
    result = move()
    if result is None:
        return result
    for account in sides:
        after = _value_or_none(account)
        if before[account["id"]] is None or after is None:
            logger.warning("account_value: no rate to keep %s's total on a move", account["id"])
            continue
        _note_unpriced(account)
        delta = round(after - before[account["id"]], 2)
        try:
            book_adjustment(account, -delta, "Holding moved — total kept")
        except Exception:  # noqa: BLE001 — the move stands; the total is off until reconciled
            logger.exception("account_value: move carve-out failed for %s", account["id"])
    return result


def _lot_change_account(change: dict) -> Optional[dict]:
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.realized_event_store import RealizedEventStore
    from backend.services import ledger as ledger_service

    ctype = change.get("type")
    account_id = None
    if ctype in ("delete", "amend"):
        account_id = (LotStore().find_by_id(change.get("lot_id") or "") or {}).get("account_id")
    elif ctype in ("dispose_undo", "dispose_amend"):
        ev = RealizedEventStore().find_by_id(change.get("event_id") or "") or {}
        pos = PositionStore().find_by_id(ev.get("position_id") or "") or {}
        account_id = pos.get("account_id")
    elif change.get("position_id"):
        account_id = (PositionStore().find_by_id(change["position_id"]) or {}).get("account_id")
    else:
        account_id = change.get("account_id")
    return ledger_service.accounts.find_by_id(account_id) if account_id else None


def _carve_date(account: dict, change: dict) -> Optional[str]:
    """A holding added with a past date is carved out on that date — net-worth history must not
    count it on top of the total in between — but never before the account's tracking start,
    where the ledger would ignore the row. Every other write is today's."""
    if change.get("type") != "acquire" or not change.get("acquired_at"):
        return None
    from backend.services import ledger as ledger_service

    acquired = str(change["acquired_at"])[:10]
    start = ledger_service.opening_date(account, ledger_service.transactions.all())
    return max(acquired, start) if start else acquired


def _note_unpriced(account: dict) -> None:
    """Remember what each still-unpriced holding was carved at, so its first price trues the
    total up (``account_value_cost``). Never fails the write it follows."""
    from backend.services import account_value_cost

    try:
        account_value_cost.note(account, _vested_rows())
    except Exception:  # noqa: BLE001 — the carve-out stands; only the later true-up is lost
        logger.exception("account_value: could not note unpriced holdings for %s", account["id"])


def _carve_failed(delta: float) -> str:
    return (
        f"The holding was saved, but its {abs(delta):,.2f} wasn't moved in or out of the "
        "account's untracked part, so the account's total is off by that much. Reconcile the "
        "account."
    )


_NO_RATE = (
    "The holding was saved, but its value couldn't be worked out in this account's currency (no "
    "exchange rate yet), so it wasn't moved out of the account's untracked part and the total may "
    "be off by that much. Reconcile the account once rates refresh."
)


def _value_or_none(account: dict) -> Optional[float]:
    """``holdings_value``, or None when a holding can't be converted into the account's currency
    — a missing rate must never stop the lot write itself (QA 2026-11-10)."""
    from backend.core.errors import MissingFxRateError

    try:
        return holdings_value(account)
    except MissingFxRateError:
        logger.warning("account_value: no FX rate to value %s's holdings", account.get("id"))
        return None


def _keep_proceeds_home(change: dict, account: dict) -> dict:
    """A sale on a Brokerage total account with nowhere named for the cash leaves it at the
    brokerage — the same account — instead of booking no cash and sinking the total. Only when
    the cash can be booked there: a sale in a currency with no rate into the account's keeps the
    old behaviour (no cash leg), and the carve-out returns its value instead."""
    # A sale edit rebooks the sale from the edit's own fields, so it needs the same rule — else
    # amending a kept-at-the-brokerage sale drops its cash (UAT TRADE-13).
    if change.get("type") not in ("dispose", "dispose_amend"):
        return change
    if change.get("proceeds_account_id") or change.get("transfer_to_account_id"):
        return change
    if not _bookable_in(account, _proceeds_currency(change)):
        return change
    return {**change, "proceeds_account_id": account["id"]}


def _proceeds_currency(change: dict) -> Optional[str]:
    """The currency a disposal's cash arrives in — resolved the way ``lots.compute_disposal``
    does: the change's own, else the position's cost currency, else the base."""
    if change.get("proceeds_currency"):
        return change["proceeds_currency"]
    from backend.core.fx import base_currency
    from backend.db.position_store import PositionStore
    from backend.db.realized_event_store import RealizedEventStore

    pos_id = change.get("position_id")
    if not pos_id and change.get("event_id"):
        pos_id = (RealizedEventStore().find_by_id(change["event_id"]) or {}).get("position_id")
    pos = PositionStore().find_by_id(pos_id or "") or {}
    return pos.get("cost_currency") or base_currency()


def _bookable_in(account: dict, currency: Optional[str]) -> bool:
    from backend.core.fx import base_currency, known_rate
    from backend.services.fx_cache import load_fx_cache

    base = base_currency().upper()
    own = (account.get("currency") or base).upper()
    ccy = (currency or own).upper()
    rates = load_fx_cache()
    # The leg converts into the account's currency AND the ledger stamps a base value, so both
    # conversions must be known (a USD sale into a USD account still needs USD→base).
    into_account = ccy == own or known_rate(ccy, own, rates) is not None
    into_base = ccy == base or known_rate(ccy, base, rates) is not None
    return into_account and into_base


def _moves_money(change: dict) -> bool:
    from backend.db.lot_store import LotStore
    from backend.db.realized_event_store import RealizedEventStore
    from backend.services import lots as lots_service

    ctype = change.get("type")
    if ctype == "acquire":
        return lots_service._will_fund_acquire(change)
    if ctype in ("delete", "amend"):
        lot = LotStore().find_by_id(change.get("lot_id") or "") or {}
        return bool(lot.get("txn_id") or change.get("funding_account_id"))
    if ctype == "dispose":
        return bool(change.get("proceeds_account_id") or change.get("transfer_to_account_id"))
    if ctype == "dispose_undo":
        ev = RealizedEventStore().find_by_id(change.get("event_id") or "") or {}
        return bool(ev.get("txn_id") or ev.get("transfer_to_account_id"))
    return True  # dispose_amend: an undo and a rebook, each with its own cash leg


# ── switching source ───────────────────────────────────────────────────────────────────


def validate_source(value: object) -> str:
    if value not in SOURCES:
        raise ValidationError(
            "value_source must be 'total' (Brokerage total) or 'holdings' (Live holdings)"
        )
    return str(value)


def switch_effect(account: dict, new_source: str) -> dict:
    """What switching ``account`` to ``new_source`` will do, without writing: the booking a
    switch to Live holdings makes, and the account's value after it (native)."""
    holdings = holdings_value(account)
    untracked = round(ledger_balance(account["id"]), 2)
    total = round(holdings + untracked, 2)
    zero = 0.0
    if new_source == HOLDINGS and untracked != 0:
        small = untracked > 0 and total > 0 and untracked <= total * SMALL_REMAINDER
        zero = 0.0 if small else -untracked
    return {
        "holdings": holdings,
        "untracked": untracked,
        "total": total,
        "adjustment": round(zero, 2),
        "value_after": round(total + zero, 2),
    }


def check_switch(account: dict, new_source: object) -> str:
    """Refuse a switch this account can't make — before anything is written."""
    new_source = validate_source(new_source)
    if not applies(account):
        raise ValidationError("Only investment and retirement accounts have a value source.")
    if not enabled():
        raise ValidationError("Choosing how an account is valued isn't turned on.")
    return new_source


def preview_switch(account: dict, new_source: str) -> dict:
    """``switch_effect`` for the Edit account screen, refusing what ``apply_switch`` refuses."""
    new_source = validate_source(new_source)
    if not applies(account):
        raise ValidationError("Only investment and retirement accounts have a value source.")
    if not enabled():
        raise ValidationError("Choosing how an account is valued isn't turned on.")
    current = source_of(account)
    if current == new_source:
        # No switch: nothing booked, the value stays (switch_effect to TOTAL never books).
        return {**switch_effect(account, TOTAL), "from": current, "to": new_source}
    return {**switch_effect(account, new_source), "from": current, "to": new_source}


def plan_switch(account: dict, new_source: object) -> dict:
    """What a switch will write, read BEFORE the write's transaction opens (it values the
    holdings): ``check_switch``'s refusals plus ``switch_effect``."""
    new_source = check_switch(account, new_source)
    current = source_of(account)
    effect = switch_effect(account, new_source) if current != new_source else {"adjustment": 0.0}
    return {**effect, "from": current, "to": new_source}


def write_switch(account: dict, plan: dict, *, conn) -> dict:
    """Store the new source and book its one adjustment (Live holdings with a large untracked
    part only) on the caller's open transaction — ``accounts.update_account`` holds the
    accounts, transactions and txn_audit locks, so the edit, the source and the adjustment
    commit or roll back together (UAT SB-13, 2026-11-10)."""
    from backend.services import ledger as ledger_service

    ledger_service.accounts.update_by_id(account["id"], {"value_source": plan["to"]}, conn=conn)
    txn = None
    if plan.get("adjustment"):
        txn = book_adjustment(
            account, plan["adjustment"], "Untracked part cleared — Live holdings", conn=conn
        )
    return {**plan, "txn_id": (txn or {}).get("id")}


# ── reconcile: a Brokerage total account reconciles its TOTAL ──────────────────────────


def reconcile_target(account: dict, real_total: float) -> float:
    """The ledger balance that makes the account worth ``real_total`` (native): the total less
    the holdings tracked in it."""
    if not enabled() or not applies(account) or source_of(account) != TOTAL:
        raise ValidationError("Only a Brokerage total account reconciles its total.")
    return round(float(real_total) - holdings_value(account), 2)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
