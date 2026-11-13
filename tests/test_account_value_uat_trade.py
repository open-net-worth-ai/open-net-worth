"""UAT: buying, selling, funding and undo on a Brokerage total account (TRADE).

Book: Main Checking 10,000.00 + Old 401(k) typed 8,000.00 + 5 ACME carved out of it (tracked
3,020.00 / untracked 4,980.00) unless the scenario says otherwise. Cash on hand and Ready to Assign
are compared with the same book built with the flag off.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from tests.account_value_uat_support import *  # noqa: F401,F403
from tests.account_value_uat_support import (
    _account,
    _add_holding,
    _instrument,
    _shell,
    _value,
    acquire,
    assert_budget_untouched,
    balance,
    carve_rows,
    differential,
    events_of,
    figures,
    holdings,
    hydrated,
    install_rates,
    lots_of,
    ref_book,
    signed,
)

ON = pytest.mark.flags(allow_brokerage_total_valuation=True)


def _sell(position_id, qty=2, price=604.0, **extra):
    out = lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": position_id,
            "quantity": qty,
            "proceeds_per_unit": price,
            "proceeds_currency": "USD",
            "disposed_at": "2026-10-03",
            **extra,
        }
    )
    assert out.get("ok"), out
    return out


@ON
def test_trade_01_a_purchase_paid_from_checking_raises_the_total_and_leaves_budgeting_alone(
    fresh_db,
):
    def scenario():
        r = ref_book()
        shell = _shell(r["k401"]["id"], r["acme"])
        acquire(shell["position_id"], funding=r["checking"]["id"])

    off, on = differential(fresh_db, scenario)
    assert_budget_untouched(off, on)
    assert on["cash"] == 6980.0 and on["nw"] == 18000.0
    accounts = {a["name"]: a["id"] for a in ledger_service.accounts.query_accounts()}
    k = accounts["Old 401(k)"]
    assert (holdings(k), balance(k), _value(k)) == (3020.0, 8000.0, 11020.0)
    assert balance(accounts["Main Checking"]) == 6980.0
    assert not carve_rows(k)  # the funding debit moved the money: no carve-out row


@ON
def test_trade_03_fees_are_in_the_cash_debit_and_not_in_the_market_value(book):
    r = ref_book()
    k = r["k401"]["id"]
    shell = _shell(k, r["acme"])
    acquire(shell["position_id"], unit_cost=600.0, fees=10.0, funding=r["checking"]["id"])
    assert balance(r["checking"]["id"]) == 6990.0  # 5 x 600 + 10
    assert (holdings(k), balance(k), _value(k)) == (3020.0, 8000.0, 11020.0)
    assert not carve_rows(k)


@ON
def test_trade_04_selling_at_market_keeps_the_proceeds_in_the_account(book):
    r = ref_book()
    k = r["k401"]["id"]
    added = _add_holding(k, r["acme"])
    _sell(added["position_id"], 2, 604.0)
    assert (holdings(k), balance(k), _value(k)) == (1812.0, 6188.0, 8000.0)
    (event,) = events_of(added["position_id"])
    assert event["realized_gain"] == 8.0  # 2 x (604 - 600)
    assert figures()["nw"] == 18000.0


@ON
def test_trade_05_a_sale_above_market_realises_the_difference_and_it_counts(book):
    r = ref_book()
    k = r["k401"]["id"]
    added = _add_holding(k, r["acme"])
    _sell(added["position_id"], 2, 610.0)
    assert (holdings(k), balance(k), _value(k)) == (1812.0, 6200.0, 8012.0)
    (event,) = events_of(added["position_id"])
    assert event["realized_gain"] == 20.0


@ON
def test_trade_06_proceeds_to_checking_leave_the_total_down_by_the_sale(fresh_db):
    def scenario():
        r = ref_book()
        added = _add_holding(r["k401"]["id"], r["acme"])
        _sell(added["position_id"], 2, 604.0, proceeds_account_id=r["checking"]["id"])

    off, on = differential(fresh_db, scenario)
    assert_budget_untouched(off, on)
    assert on["cash"] == 11208.0 and on["nw"] == 18000.0
    k = next(a["id"] for a in ledger_service.accounts.query_accounts() if a["name"] == "Old 401(k)")
    assert (holdings(k), balance(k), _value(k)) == (1812.0, 4980.0, 6792.0)


@ON
def test_trade_07_selling_everything_leaves_the_total_as_untracked(book):
    r = ref_book()
    k = r["k401"]["id"]
    added = _add_holding(k, r["acme"])
    _sell(added["position_id"], 5, 604.0)
    assert (holdings(k), balance(k), _value(k)) == (0.0, 8000.0, 8000.0)


@ON
def test_trade_08_undoing_a_sale_that_stayed_in_the_account_restores_the_split(book):
    r = ref_book()
    k = r["k401"]["id"]
    added = _add_holding(k, r["acme"])
    _sell(added["position_id"], 2, 604.0)
    (event,) = events_of(added["position_id"])
    out = lots_service.apply_lot_change({"type": "dispose_undo", "event_id": event["id"]})
    assert out.get("ok"), out
    assert (holdings(k), balance(k), _value(k)) == (3020.0, 4980.0, 8000.0)
    assert sum(lot["remaining_qty"] for lot in lots_of(added["position_id"])) == 5


@ON
def test_trade_09_undoing_a_sale_paid_to_checking_puts_the_cash_back(book):
    r = ref_book()
    k, checking = r["k401"]["id"], r["checking"]["id"]
    added = _add_holding(k, r["acme"])
    _sell(added["position_id"], 2, 604.0, proceeds_account_id=checking)
    assert balance(checking) == 11208.0
    (event,) = events_of(added["position_id"])
    lots_service.apply_lot_change({"type": "dispose_undo", "event_id": event["id"]})
    assert balance(checking) == 10000.0
    assert (holdings(k), balance(k), _value(k)) == (3020.0, 4980.0, 8000.0)


@ON
def test_trade_13_amending_a_sale_price_moves_the_total_by_the_price_difference(book):
    r = ref_book()
    k = r["k401"]["id"]
    added = _add_holding(k, r["acme"])
    _sell(added["position_id"], 2, 604.0)
    rows_before = len(carve_rows(k))
    (event,) = events_of(added["position_id"])
    out = lots_service.apply_lot_change(
        {
            "type": "dispose_amend",
            "event_id": event["id"],
            "quantity": 2,
            "proceeds_per_unit": 610.0,
            "proceeds_currency": "USD",
            "disposed_at": "2026-10-03",
        }
    )
    assert out.get("ok"), out
    assert len(carve_rows(k)) == rows_before  # no new carve row: each leg carries its own cash
    assert (holdings(k), balance(k), _value(k)) == (1812.0, 6200.0, 8012.0)


# ── no money moves ─────────────────────────────────────────────────────────────────────


@ON
def test_trade_14_a_replayed_acquire_books_no_second_carve_out(book):
    r = ref_book()
    k = r["k401"]["id"]
    shell = _shell(k, r["acme"])
    first = acquire(shell["position_id"], idempotency_key="order-1")
    again = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": shell["position_id"],
            "quantity": 5,
            "unit_cost": 604.0,
            "cost_currency": "USD",
            "acquired_at": "2026-10-02",
            "idempotency_key": "order-1",
        }
    )
    assert first["value_carve_out"]["amount"] == -3020.0
    assert again.get("idempotent") and "value_carve_out" not in again
    assert len(carve_rows(k)) == 1 and _value(k) == 8000.0


@ON
def test_trade_15_a_refused_acquire_books_nothing_and_still_stores_the_source(book):
    r = ref_book()
    k = r["k401"]["id"]
    shell = _shell(k, r["acme"])
    ledger_service.accounts.update_by_id(k, {"value_source": None})
    refused = lots_service.apply_lot_change(
        {"type": "acquire", "position_id": shell["position_id"], "quantity": -1, "unit_cost": 604.0}
    )
    assert refused.get("error") and "value_carve_out" not in refused
    assert not carve_rows(k) and _value(k) == 8000.0
    assert ledger_service.accounts.find_by_id(k)["value_source"] == "total"  # stored, harmless


@ON
@pytest.mark.parametrize("kind", ["drp", "transfer_in"])
def test_trade_21_reinvested_and_transferred_in_units_are_carved_like_any_unfunded_acquire(
    book, kind
):
    r = ref_book()
    k = r["k401"]["id"]
    shell = _shell(k, r["acme"])
    out = acquire(shell["position_id"], kind=kind)
    assert out["value_carve_out"]["amount"] == -3020.0
    assert (holdings(k), balance(k), _value(k)) == (3020.0, 4980.0, 8000.0)
    assert [signed(t, k) for t in carve_rows(k)] == [-3020.0]


# ── currencies ─────────────────────────────────────────────────────────────────────────


def _gbp_instrument():
    from backend.db.instrument_store import InstrumentStore

    return InstrumentStore().create(
        {
            "symbol": "BETA",
            "asset_class": "equity",
            "price_source": "manual",
            "price_source_id": "BETA-L",
            "manual_price": 100.0,
            "quote_currency": "GBP",
            "currency": "GBP",
        }
    )


def _no_rate_book():
    r = ref_book()
    return r["k401"]["id"], _gbp_instrument()


@ON
def test_trade_11_a_lot_with_no_rate_saves_says_the_total_is_off_and_books_nothing(book):
    k, gbp = _no_rate_book()
    shell = _shell(k, gbp)
    out = acquire(shell["position_id"], qty=5, unit_cost=120.0)
    assert "Reconcile" in out["value_carve_out_error"]
    assert "exchange rate" in out["value_carve_out_error"]
    assert "value_carve_out" not in out and not carve_rows(k) and balance(k) == 8000.0


@ON
def test_trade_11_a_position_add_with_no_rate_also_tells_the_caller(book):
    from backend.services import positions as positions_service

    k, gbp = _no_rate_book()
    res = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": gbp["id"],
            "account_id": k,
            "quantity": 5,
            "cost_basis": 120.0,
            "cost_currency": "USD",
            "acquired_at": "2026-10-01",
        }
    )
    assert res.get("ok"), res
    assert "Reconcile" in res["value_carve_out_error"]


@ON
def test_trade_12_once_the_rate_arrives_a_reconcile_removes_the_excess(book, monkeypatch, tmp_path):
    k, gbp = _no_rate_book()
    shell = _shell(k, gbp)
    acquire(shell["position_id"], qty=5, unit_cost=120.0)
    install_rates(monkeypatch, tmp_path, {"USD": 1.0, "GBP": 1.25})
    assert (holdings(k), balance(k), _value(k)) == (625.0, 8000.0, 8625.0)
    ledger_service.reconcile(k, 7375.0, against="working")  # real_total 8,000.00 - 625.00 tracked
    assert (holdings(k), balance(k), _value(k)) == (625.0, 7375.0, 8000.0)
    assert signed(carve_rows(k)[-1], k) == -625.0


@ON
def test_trade_22_a_euro_account_carves_in_euros(eur_book):
    k = _account(name="EUR 401(k)", type_="retirement", total=10000.0, currency="EUR")
    _add_holding(k["id"], _instrument())
    assert [signed(t, k["id"]) for t in carve_rows(k["id"])] == [-2745.45]  # 3,020.00 / 1.10
    assert (holdings(k["id"]), balance(k["id"]), _value(k["id"])) == (2745.45, 7254.55, 10000.0)
    assert hydrated(k["id"])["value_breakdown"] == {
        "tracked_holdings": 2745.45,
        "untracked": 7254.55,
        "total": 10000.0,
        "currency": "EUR",
    }
    # 7,254.55 EUR x 1.10 = 7,980.005 -> 7,980.01 as the account's base figure, + 3,020.00 USD.
    assert figures()["nw"] == 11000.01
