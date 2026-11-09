"""Brokerage total / Live holdings (ADR 2026-11-10) — synthetic data only.

Every scenario values the account the way every surface does — holdings + ledger balance — and
checks the number a user would see. The flag-off tests prove the old behaviour is untouched.
"""

from __future__ import annotations

import pytest

from backend.core import fx as core_fx
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.transaction_store import TransactionStore
from backend.services import account_value as av
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from backend.services import positions as positions_service

PRICE = 604.0  # ACME's manual price: 5 units = 3,020.00


@pytest.fixture
def book(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(core_fx, "base_currency", lambda: "USD")
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"USD": 1.0})
    yield tmp_path


def _instrument(symbol="ACME", price=PRICE):
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "asset_class": "equity",
            "price_source": "manual",
            "price_source_id": symbol,
            "manual_price": price,
            "quote_currency": "USD",
            "currency": "USD",
        }
    )


def _account(name="Old 401(k)", total=8000.0, type_="retirement", **extra):
    return accounts_service.create_account(
        {
            "name": name,
            "type": type_,
            "currency": "USD",
            "initial_balance": total,
            "initial_balance_date": "2026-01-01",  # before every trade below
            **extra,
        }
    )


def _add_holding(account_id, inst, qty=5, cost=600.0):
    res = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": account_id,
            "quantity": qty,
            "cost_basis": cost,
            "cost_currency": "USD",
            "acquired_at": "2026-10-01",
        }
    )
    assert res.get("ok"), res
    return res


def _shell(account_id, inst):
    """An empty holding for lots recorded next (a quantity-0 add)."""
    res = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": account_id, "quantity": 0}
    )
    assert res.get("ok"), res
    return res


def _balance(account_id):
    return round(av.ledger_balance(account_id), 2)


def _value(account_id):
    acct = ledger_service.accounts.find_by_id(account_id)
    return round(av.holdings_value(acct) + _balance(account_id), 2)


def _hydrated(account_id):
    return ledger_service.account_with_balance(ledger_service.accounts.find_by_id(account_id))


# ── flag off: the old code path ────────────────────────────────────────────────────────


@pytest.mark.flags(investment_value_source=False)
def test_flag_off_a_holding_still_counts_on_top_and_nothing_new_is_stored(book):
    acct = _account()
    _add_holding(acct["id"], _instrument())
    assert _value(acct["id"]) == 11020.0  # the old double count (#70), unchanged when off
    stored = ledger_service.accounts.find_by_id(acct["id"])
    assert "value_source" not in stored
    assert "value_source" not in _hydrated(acct["id"])


@pytest.mark.flags(investment_value_source=False)
def test_flag_off_value_source_is_refused(book):
    with pytest.raises(Exception, match="isn't turned on"):
        _account(value_source="holdings")


# ── default and carve-out ──────────────────────────────────────────────────────────────


@pytest.mark.flags(investment_value_source=False)
def test_flag_turned_off_hides_a_stamped_source_and_reading(book):
    """On → off: the stored fields stay (on again is safe) but the response is the old one."""
    acct = _account()
    ledger_service.accounts.update_by_id(
        acct["id"], {"value_source": "total", "value_sync": {"status": "fresh", "total": 1.0}}
    )
    shown = _hydrated(acct["id"])
    assert "value_source" not in shown and "value_sync" not in shown
    assert ledger_service.accounts.find_by_id(acct["id"])["value_source"] == "total"


@pytest.mark.flags(investment_value_source=True)
def test_new_investment_account_starts_as_brokerage_total(book):
    acct = _account(type_="investment")
    assert ledger_service.accounts.find_by_id(acct["id"])["value_source"] == "total"
    assert _hydrated(acct["id"])["value_source"] == "total"
    cash = _account(name="Checking", type_="cash", total=100.0)
    assert "value_source" not in _hydrated(cash["id"])


@pytest.mark.flags(investment_value_source=True)
def test_first_holding_is_carved_out_of_the_typed_total(book):
    """The owner's journey #1: typed $8,000, then 5 ACME → 3,020 tracked + 4,980 untracked."""
    acct = _account()
    _add_holding(acct["id"], _instrument())
    assert _balance(acct["id"]) == 4980.0
    assert _value(acct["id"]) == 8000.0
    txns = [
        t
        for t in TransactionStore().all()
        if t.get("description") == "Holding tracked from the total"
    ]
    assert len(txns) == 1 and txns[0]["kind"] == "adjustment" and txns[0]["cleared"]


@pytest.mark.flags(investment_value_source=True)
def test_more_holdings_and_removing_one_never_move_the_total(book):
    acct = _account()
    first = _add_holding(acct["id"], _instrument())
    _add_holding(acct["id"], _instrument("BETA", 100.0), qty=10, cost=90.0)
    assert _value(acct["id"]) == 8000.0
    assert _balance(acct["id"]) == 3980.0
    out = positions_service.apply_position_change(
        {"type": "remove", "position_id": first["position_id"]}
    )
    assert out["ok"] and out["value_carve_out"]["amount"] == 3020.0
    assert _value(acct["id"]) == 8000.0


@pytest.mark.flags(investment_value_source=True)
def test_an_existing_empty_account_does_not_flip_at_its_first_holding(book, monkeypatch):
    """Regression (prototype 2026-11-09): re-deciding the default at every read flipped the
    account to Live holdings the moment it held something, counting 11,020."""
    acct = _account()
    ledger_service.accounts.update_by_id(acct["id"], {"value_source": None})
    _add_holding(acct["id"], _instrument())
    assert ledger_service.accounts.find_by_id(acct["id"])["value_source"] == "total"
    assert _value(acct["id"]) == 8000.0


@pytest.mark.flags(investment_value_source=True)
def test_an_account_already_holding_something_defaults_to_live_holdings(book):
    acct = _account()
    inst = _instrument()
    ledger_service.accounts.update_by_id(acct["id"], {"value_source": "holdings"})
    _add_holding(acct["id"], inst)
    ledger_service.accounts.update_by_id(acct["id"], {"value_source": None})
    assert _hydrated(acct["id"])["value_source"] == "holdings"


@pytest.mark.flags(investment_value_source=True)
def test_a_purchase_paid_from_another_account_raises_the_total_by_its_cost(book):
    acct = _account(type_="investment")
    checking = _account(name="Checking", type_="cash", total=5000.0)
    shell = _shell(acct["id"], _instrument())
    lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": shell["position_id"],
            "quantity": 5,
            "unit_cost": PRICE,
            "cost_currency": "USD",
            "acquired_at": "2026-10-02",
            "funding_account_id": checking["id"],
        }
    )
    assert _value(acct["id"]) == 11020.0  # real money came in
    assert _balance(checking["id"]) == 1980.0
    assert not [t for t in TransactionStore().all() if "the total" in (t.get("description") or "")]


@pytest.mark.flags(investment_value_source=True)
def test_a_purchase_paid_from_the_account_itself_keeps_the_total(book):
    acct = _account(type_="investment")
    shell = _shell(acct["id"], _instrument())
    lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": shell["position_id"],
            "quantity": 5,
            "unit_cost": PRICE,
            "cost_currency": "USD",
            "acquired_at": "2026-10-02",
            "funding_account_id": acct["id"],
        }
    )
    assert _value(acct["id"]) == 8000.0


@pytest.mark.flags(investment_value_source=True)
def test_a_sale_with_no_proceeds_account_keeps_the_cash_at_the_brokerage(book):
    acct = _account(type_="investment")
    added = _add_holding(acct["id"], _instrument())
    out = lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": added["position_id"],
            "quantity": 5,
            "proceeds_per_unit": PRICE,
            "proceeds_currency": "USD",
            "disposed_at": "2026-10-03",
        }
    )
    assert out.get("ok"), out
    assert out["proceeds_account_id"] == acct["id"]
    assert _value(acct["id"]) == 8000.0


@pytest.mark.flags(investment_value_source=True)
def test_a_live_holdings_account_keeps_the_old_rule(book):
    acct = _account(value_source="holdings", total=500.0)
    _add_holding(acct["id"], _instrument())
    assert _balance(acct["id"]) == 500.0
    assert _value(acct["id"]) == 3520.0


# ── switching, reconcile, split institutions ───────────────────────────────────────────


@pytest.mark.flags(investment_value_source=True)
def test_switch_to_live_holdings_clears_a_large_untracked_part(book):
    acct = _account()
    _add_holding(acct["id"], _instrument())
    preview = av.preview_switch(ledger_service.accounts.find_by_id(acct["id"]), "holdings")
    assert preview["adjustment"] == -4980.0 and preview["value_after"] == 3020.0
    side: dict = {}
    accounts_service.update_account(acct["id"], {"value_source": "holdings"}, side_effects=side)
    assert side["value_switch"]["adjustment"] == -4980.0
    assert _value(acct["id"]) == 3020.0
    assert ledger_service.accounts.find_by_id(acct["id"])["value_source"] == "holdings"


@pytest.mark.flags(investment_value_source=True)
def test_switch_to_live_holdings_keeps_a_small_untracked_part_as_cash(book):
    acct = _account(total=3080.0)  # 3,020 tracked + 60 untracked (≤ 2%)
    _add_holding(acct["id"], _instrument())
    accounts_service.update_account(acct["id"], {"value_source": "holdings"})
    assert _value(acct["id"]) == 3080.0
    accounts_service.update_account(acct["id"], {"value_source": "total"})
    assert _value(acct["id"]) == 3080.0  # back to Brokerage total writes nothing


@pytest.mark.flags(investment_value_source=True)
def test_reconcile_a_brokerage_total_sets_the_untracked_part(book):
    acct = _account()
    _add_holding(acct["id"], _instrument())
    target = av.reconcile_target(ledger_service.accounts.find_by_id(acct["id"]), 9000.0)
    assert target == 5980.0
    ledger_service.reconcile(acct["id"], target, against="working")
    assert _value(acct["id"]) == 9000.0


@pytest.mark.flags(investment_value_source=True)
def test_a_linked_account_at_a_split_institution_defaults_to_live_holdings(book):
    link = [{"source": "plaid", "ref": "pa-1", "name": "Brokerage", "mask": "0001"}]
    main = _account(name="Acme Securities", type_="investment", institution="Acme Securities")
    usd = _account(name="Acme Securities USD", type_="investment", institution="Acme Securities")
    ledger_service.accounts.update_by_id(main["id"], {"value_source": None})
    accounts_service.update_account(main["id"], {"external_ids": link})
    view = _hydrated(main["id"])
    assert view["value_source"] == "holdings"
    assert view["value_siblings"] == [usd["name"]]


@pytest.mark.flags(investment_value_source=True)
def test_a_sale_with_no_exchange_rate_still_saves(book, monkeypatch):
    """QA 2026-11-10: keeping a sale's cash at the brokerage needs rates into the account's
    currency and the base. Without them the sale books as before (no cash leg) — the lot write
    never fails on the carve-out — and the value returns to the untracked part instead."""
    acct = _account(type_="investment")
    added = _add_holding(acct["id"], _instrument())
    monkeypatch.setattr(av, "_bookable_in", lambda account, currency: False)
    out = lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": added["position_id"],
            "quantity": 5,
            "proceeds_per_unit": PRICE,
            "disposed_at": "2026-10-03",
        }
    )
    assert out.get("ok"), out
    assert out["proceeds_account_id"] is None
    assert _value(acct["id"]) == 8000.0
