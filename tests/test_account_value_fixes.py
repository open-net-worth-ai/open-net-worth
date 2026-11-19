"""Regression tests for the bugs the PR #93 user-acceptance pass found (2026-11-10).

Each test names its UAT scenario and suspected-bug ids. Figures are made up (ACME, 8,000.00).
"""

from __future__ import annotations

import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.api.portfolio import router as portfolio_router
from backend.core.errors import ValidationError
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.services import account_value as av
from backend.services import account_value_cost as cost
from backend.services import account_value_sync as sync
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services import positions as positions_service
from backend.services.aggregation import yodlee
from tests import test_account_value as base
from tests import test_account_value_sync as sync_tests
from tests.test_account_value import _account, _add_holding, _balance, _instrument, _value

book = base.book
plaid = sync_tests.plaid
_linked = sync_tests._linked
_sync_state = sync_tests._sync_state


def _rows(description):
    return [t for t in TransactionStore().all() if t.get("description") == description]


@pytest.fixture
def client(book):
    app = FastAPI()
    app.include_router(ledger_router)
    app.include_router(portfolio_router)
    return TestClient(app)


# ── TYPED-05 / SB-01: removing a holding through the REST route keeps the total ────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_typed_05_rest_delete_position_keeps_the_total(client):
    acct = _account()
    added = _add_holding(acct["id"], _instrument())
    assert _value(acct["id"]) == 8000.0
    res = client.delete(f"/api/positions/{added['position_id']}")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert res.json()["value_carve_out"]["amount"] == 3020.0
    assert _value(acct["id"]) == 8000.0
    assert len(_rows("Holding returned to the total")) == 1


@pytest.mark.flags(allow_brokerage_total_valuation=False)
def test_typed_05_flag_off_rest_delete_is_unchanged(client):
    acct = _account()
    added = _add_holding(acct["id"], _instrument())
    res = client.delete(f"/api/positions/{added['position_id']}")
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert _value(acct["id"]) == 8000.0  # the old rule: 11,020 − the holding
    assert client.delete(f"/api/positions/{added['position_id']}").status_code == 404


# ── TYPED-12/13 / SB-06: a holding carved out before it had a price ────────────────────


def _set_price(inst, price):
    InstrumentStore().update_by_id(inst["id"], {"manual_price": price})


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_typed_12_unpriced_holding_carved_at_cost_is_trued_up_when_priced(book):
    acct = _account()
    inst = _instrument(price=None)
    _add_holding(acct["id"], inst, qty=5, cost=400.0)  # no price yet: carved at cost, 2,000
    assert _balance(acct["id"]) == 6000.0
    assert cost.settle() == []  # still unpriced: nothing to true up
    _set_price(inst, 604.0)
    assert _value(acct["id"]) == 9020.0  # before the true-up: the drift the bug left forever
    assert cost.settle() == [acct["id"]]
    assert _value(acct["id"]) == 8000.0
    assert _balance(acct["id"]) == 4980.0
    [row] = _rows("Holding priced — total kept")
    assert row["kind"] == "adjustment" and row["from_amount"] == 1020.0
    assert cost.settle() == []  # once only


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_typed_13_unpriced_holding_with_no_cost_is_carved_when_priced(book):
    acct = _account()
    inst = _instrument(price=None)
    positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 5}
    )
    assert _value(acct["id"]) == 8000.0  # valued at 0, nothing carved yet
    _set_price(inst, 604.0)
    cost.settle()
    assert _value(acct["id"]) == 8000.0 and _balance(acct["id"]) == 4980.0


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_typed_12_a_true_up_is_dropped_once_the_account_is_on_live_holdings(book):
    acct = _account()
    inst = _instrument(price=None)
    _add_holding(acct["id"], inst, qty=5, cost=400.0)
    ledger_service.accounts.update_by_id(acct["id"], {"value_source": "holdings"})
    _set_price(inst, 604.0)
    assert cost.settle() == []
    assert _rows("Holding priced — total kept") == []
    assert cost._Pending().get().get("pending") == {}


@pytest.mark.flags(allow_brokerage_total_valuation=False)
def test_typed_12_flag_off_nothing_is_noted_or_settled(book):
    acct = _account()
    _add_holding(acct["id"], _instrument(price=None), qty=5, cost=400.0)
    assert cost._Pending().get() == {}
    assert cost.settle() == []


# ── TRADE-17 / SB-03: a remove whose carve-out can't be booked says so ────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_trade_17_failed_remove_carve_out_is_reported_not_raised(book, monkeypatch):
    acct = _account()
    added = _add_holding(acct["id"], _instrument())

    def boom(*_a, **_k):
        raise ValidationError("ledger refused")

    monkeypatch.setattr(av, "book_adjustment", boom)
    out = positions_service.apply_position_change(
        {"type": "remove", "position_id": added["position_id"]}
    )
    assert out["ok"] is True
    assert (
        "3,020.00" in out["value_carve_out_error"] and "Reconcile" in out["value_carve_out_error"]
    )
    assert PositionStore().find_by_id(added["position_id"]) is None


# ── TRADE-18 / SB-02: moving a holding keeps both totals ──────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_trade_18_moving_a_holding_between_total_accounts_keeps_both_totals(book):
    a = _account(name="Old 401(k)")
    b = _account(name="Brokerage Z", type_="investment", total=5000.0)
    added = _add_holding(a["id"], _instrument())
    positions_service.update_position(added["position_id"], {"account_id": b["id"]})
    assert _value(a["id"]) == 8000.0 and _balance(a["id"]) == 8000.0
    assert _value(b["id"]) == 5000.0 and _balance(b["id"]) == 1980.0
    assert len(_rows("Holding moved — total kept")) == 2


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_trade_18_moving_into_a_live_holdings_account_counts_it_there(book):
    a = _account(name="Old 401(k)")
    b = _account(name="Live", type_="investment", total=1000.0)
    ledger_service.accounts.update_by_id(b["id"], {"value_source": "holdings"})
    added = _add_holding(a["id"], _instrument())
    positions_service.update_position(added["position_id"], {"account_id": b["id"]})
    assert _value(a["id"]) == 8000.0
    assert _value(b["id"]) == 4020.0  # holdings + cash, the Live holdings rule


@pytest.mark.flags(allow_brokerage_total_valuation=False)
def test_trade_18_flag_off_a_move_books_nothing(book):
    a = _account(name="Old 401(k)")
    b = _account(name="Brokerage Z", type_="investment", total=5000.0)
    added = _add_holding(a["id"], _instrument())
    positions_service.update_position(added["position_id"], {"account_id": b["id"]})
    assert _value(a["id"]) == 8000.0 and _value(b["id"]) == 8020.0
    assert _rows("Holding moved — total kept") == []


# ── SWITCH-12 / SB-13: a switch whose adjustment fails changes nothing ────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_switch_12_failed_adjustment_rolls_back_the_whole_edit(book, monkeypatch):
    acct = _account()
    _add_holding(acct["id"], _instrument())

    def boom(*_a, **_k):
        raise ValidationError("ledger refused")

    monkeypatch.setattr(av, "book_adjustment", boom)
    with pytest.raises(ValidationError):
        accounts_service.update_account(
            acct["id"], {"value_source": "holdings", "name": "Renamed 401(k)"}
        )
    stored = ledger_service.accounts.find_by_id(acct["id"])
    assert stored["value_source"] == "total" and stored["name"] == "Old 401(k)"
    assert _value(acct["id"]) == 8000.0


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_switch_06_negative_untracked_part_switches_to_the_holdings_value(book):
    """Not a bug (review of SB-12): on Live holdings the account is worth its holdings."""
    acct = _account(total=2900.0)
    _add_holding(acct["id"], _instrument())  # 3,020 tracked of a 2,900 total: untracked −120
    assert _balance(acct["id"]) == -120.0
    out = av.switch_effect(ledger_service.accounts.find_by_id(acct["id"]), "holdings")
    assert out["adjustment"] == 120.0 and out["value_after"] == 3020.0


# ── HOLD-09 / SB-04: a kept figure is asked about again after a recovery ──────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_hold_09_a_zero_after_a_recovery_is_asked_about_again(plaid):
    acct = _linked(total=5000.0)
    plaid.balances["pa-1"] = 5000.0
    sync.run()  # first reading: the account's own value, applied
    plaid.balances["pa-1"] = 0.0
    sync.run()
    sync.confirm_held(acct["id"], accept=False)  # Keep
    plaid.balances["pa-1"] = 5000.0
    sync.run()  # the feed recovers
    assert _sync_state(acct["id"])["status"] == "fresh"
    plaid.balances["pa-1"] = 0.0
    sync.run()  # a genuine 0, days later
    state = _sync_state(acct["id"])
    assert state["status"] == "held_back" and state["held_total"] == 0.0
    assert _value(acct["id"]) == 5000.0


# ── LINK-16 / SB-09: a negative reading ───────────────────────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_link_16_a_negative_first_reading_is_held(plaid):
    acct = _linked()
    plaid.balances["pa-1"] = -500.0
    sync.run()
    state = _sync_state(acct["id"])
    assert state["status"] == "held_back" and state["held_total"] == -500.0
    assert _value(acct["id"]) == 0.0
    sync.confirm_held(acct["id"], accept=True)
    assert _value(acct["id"]) == -500.0
    plaid.balances["pa-1"] = -600.0  # already negative: margin debt moving, applied
    sync.run()
    assert _value(acct["id"]) == -600.0 and _sync_state(acct["id"])["status"] == "fresh"


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_link_16_a_negative_reading_after_a_positive_total_is_held(plaid):
    acct = _linked()
    sync.run()  # 123,456.78
    plaid.balances["pa-1"] = -10.0
    sync.run()
    assert _sync_state(acct["id"])["status"] == "held_back"
    assert _value(acct["id"]) == sync_tests.REPORTED


# ── FEED-03/04 / SB-08, SB-05: no currency on either side fails closed ────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_feed_03_no_stored_and_no_reported_currency_is_never_booked(plaid):
    """The store refuses to blank a currency, so this is a row stored without one."""
    acct = _linked()
    row = dict(ledger_service.accounts.find_by_id(acct["id"]))
    row.pop("currency", None)
    reading = {
        "status": "ok",
        "accounts": {"pa-1": {"provider_account_id": "pa-1", "balance": 500.0, "currency": ""}},
        "complete": True,
    }
    sync._apply(row, reading)
    state = _sync_state(acct["id"])
    assert state["status"] == "currency_mismatch" and state.get("reported_currency") is None
    assert _rows("Total from Plaid") == []


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_feed_04_yodlee_inferred_currency_is_not_a_reported_one(plaid, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    common = yodlee._account_to_common({"id": 7, "accountName": "IRA", "balance": {"amount": 5}})
    assert common["currency"] == "USD" and common["currency_inferred"] is True
    named = yodlee._account_to_common({"id": 8, "balance": {"amount": 5, "currency": "usd"}})
    assert named["currency_inferred"] is False
    acct = _linked()
    plaid.balances = {}
    monkeypatch.setattr(
        type(plaid),
        "account_balances",
        lambda self: {
            "accounts": [
                {
                    "provider_account_id": "pa-1",
                    "balance": 500.0,
                    "currency": "USD",
                    "currency_inferred": True,
                }
            ],
            "complete": True,
        },
    )
    sync.run()
    assert _sync_state(acct["id"])["status"] == "currency_mismatch"
    assert _value(acct["id"]) == 0.0


# ── REC-16 / SB-16: NaN and Infinity are refused ──────────────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_rec_16_rest_reconcile_refuses_a_non_finite_total(book, bad):
    """Refused at the request model, before the route runs. (FastAPI's own 422 handler then
    fails to echo the NaN back as JSON — true of every float field — so the status is not
    pinned; what matters is that nothing reaches the ledger.)"""
    app = FastAPI()
    app.include_router(ledger_router)
    acct = _account()
    res = TestClient(app, raise_server_exceptions=False).post(
        f"/api/accounts/{acct['id']}/reconcile",
        content=f'{{"real_total": {bad}}}',
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code >= 400
    assert _value(acct["id"]) == 8000.0
    assert _rows("Balance adjustment — Old 401(k)") == []


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_rec_16_reconcile_tool_input_refuses_a_non_finite_total(value):
    from pydantic import ValidationError as InputError

    from backend.ai.tools.accounts import ReconcileAccountsInput, ReconcileItem

    with pytest.raises(InputError):
        ReconcileItem(account_id="acct_x", real_total=value)
    with pytest.raises(InputError):
        ReconcileAccountsInput(account_id="acct_x", real_total=value)


# ── TRADE-20 / SB-07 (not a bug): a vest raises the total and books nothing ───────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_trade_20_a_vest_into_a_brokerage_total_account_books_no_row(book, monkeypatch):
    from backend.services import equity as equity_svc

    monkeypatch.setattr(equity_svc, "base_currency", lambda: "USD")

    acct = _account(name="Equity plan", type_="investment", total=0.0)
    inst = _instrument()
    from backend.db.grant_store import GrantStore

    equity_svc.apply_equity_change(
        {
            "type": "grant_create",
            "instrument_id": inst["id"],
            "total_units": 50,
            "grant_date": "2025-01-01",
            "grant_type": "rsu",
            "tax_event": "at_vest",
            "account_id": acct["id"],
            "tranches": [
                {"vest_date": "2026-03-01", "units": 25, "kind": "time"},
                {"vest_date": "2027-03-01", "units": 25, "kind": "time"},
            ],
        }
    )
    grant = GrantStore().all()[0]
    assert av.source_of(ledger_service.accounts.find_by_id(acct["id"])) == "total"
    before = len(TransactionStore().all())
    equity_svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": grant["tranches"][0]["id"],
            "fmv_per_unit": 604.0,
            "fmv_currency": "USD",
            "vest_date": "2026-03-01",
        }
    )
    assert len(TransactionStore().all()) == before  # CLAUDE.md: a vest books NO transaction
    assert _value(acct["id"]) == 15100.0  # 25 × 604.00, on top of the 0 total
