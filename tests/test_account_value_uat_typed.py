"""UAT: "I typed my 401(k) total and later added holdings" (TYPED, issue #70).

Reference book: Main Checking 10,000.00 + Old 401(k) typed 8,000.00, ACME 604.00, BETA 50.25.
Net worth comes from the real service; cash on hand and Ready to Assign are compared against the
SAME book built with the flag off (this PR must not change budgeting).
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from backend.services import positions as positions_service
from backend.services import reports_rollup
from tests.account_value_uat_support import *  # noqa: F401,F403
from tests.account_value_uat_support import (
    _account,
    _add_holding,
    _value,
    assert_budget_untouched,
    balance,
    carve_rows,
    cash_excludes_investments,
    differential,
    figures,
    holdings,
    hydrated,
    lots_of,
    ref_book,
    rows,
    seed_history,
    set_price,
    signed,
    today,
)

ON = pytest.mark.flags(allow_brokerage_total_valuation=True)


def _add_today(account_id, inst, qty=5, cost=600.0):
    """A holding added with no acquisition date: the carve-out is dated today."""
    res = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": account_id,
            "quantity": qty,
            "cost_basis": cost,
            "cost_currency": "USD",
        }
    )
    assert res.get("ok"), res
    return res


@ON
def test_typed_01_the_first_holding_is_carved_out_and_budgeting_is_untouched(fresh_db):
    seen = {}

    def scenario():
        r = ref_book()
        seen.update(r)
        _add_today(r["k401"]["id"], r["acme"])

    off, on = differential(fresh_db, scenario)
    assert_budget_untouched(off, on)  # cash and Ready to Assign: identical with the flag off
    assert (on["cash"], on["rta"]) == (10000.0, 10000.0)
    assert on["nw"] == 18000.0 and off["nw"] == 21020.0  # the old double count, #70
    k401 = ledger_service.accounts.find_by_id(
        next(a["id"] for a in ledger_service.accounts.query_accounts() if a["name"] == "Old 401(k)")
    )
    assert (holdings(k401["id"]), balance(k401["id"]), _value(k401["id"])) == (
        3020.0,
        4980.0,
        8000.0,
    )
    assert hydrated(k401["id"])["value_breakdown"] == {
        "tracked_holdings": 3020.0,
        "untracked": 4980.0,
        "total": 8000.0,
        "currency": "USD",
    }
    (row,) = carve_rows(k401["id"])
    assert (row["kind"], row["description"], row["date"]) == (
        "adjustment",
        "Holding tracked from the total",
        today(),
    )
    assert signed(row, k401["id"]) == -3020.0 and row["cleared"] and row["category"] is None
    summary = reports_rollup.month_summary(today()[:7])
    assert summary["expense_total"] == 0 and summary["income_total"] == 0
    cash_excludes_investments()


@ON
def test_typed_03_a_price_move_changes_tracked_not_untracked_and_books_nothing(book):
    r = ref_book()
    k = r["k401"]["id"]
    _add_holding(k, r["acme"])
    _add_holding(k, r["beta"], qty=2, cost=50.25)
    assert (holdings(k), balance(k), _value(k)) == (3120.5, 4879.5, 8000.0)
    assert len(carve_rows(k)) == 2
    set_price(r["acme"], 620.0)
    assert (holdings(k), balance(k), _value(k)) == (3200.5, 4879.5, 8080.0)
    assert len(carve_rows(k)) == 2  # market moves are never booked
    assert figures()["nw"] == 18080.0


def _book_with_lot():
    r = ref_book()
    k = r["k401"]["id"]
    added = _add_holding(k, r["acme"])
    (lot,) = lots_of(added["position_id"])
    return r, k, added, lot


@ON
def test_typed_06_a_smaller_lot_returns_its_value_to_the_untracked_part(book):
    _, k, added, lot = _book_with_lot()
    out = lots_service.apply_lot_change({"type": "amend", "lot_id": lot["id"], "quantity": 4})
    assert out.get("ok"), out
    assert (holdings(k), balance(k), _value(k)) == (2416.0, 5584.0, 8000.0)
    last = carve_rows(k)[-1]
    assert last["description"] == "Holding returned to the total" and signed(last, k) == 604.0
    assert out["value_carve_out"]["amount"] == 604.0


@ON
def test_typed_07_a_cost_only_amend_moves_nothing(book):
    _, k, _, lot = _book_with_lot()
    out = lots_service.apply_lot_change({"type": "amend", "lot_id": lot["id"], "unit_cost": 590.0})
    assert out.get("ok"), out
    assert len(carve_rows(k)) == 1 and "value_carve_out" not in out
    assert _value(k) == 8000.0


@ON
def test_typed_08_deleting_the_lot_returns_its_value_and_leaves_the_empty_position(book):
    _, k, added, lot = _book_with_lot()
    out = lots_service.apply_lot_change({"type": "delete", "lot_id": lot["id"]})
    assert out.get("ok"), out
    assert signed(carve_rows(k)[-1], k) == 3020.0
    assert (holdings(k), balance(k), _value(k)) == (0.0, 8000.0, 8000.0)
    assert positions_service.get_position(added["position_id"]) is not None  # the empty shell


@ON
def test_typed_09_a_past_dated_holding_is_carved_on_its_own_date(book):
    r = ref_book()
    k = r["k401"]["id"]
    seed_history(r["acme"], 604.0, ["2026-09-01", "2026-10-14", "2026-10-15"])
    _add_holding(k, r["acme"])  # acquired 2026-10-01
    (row,) = carve_rows(k)
    assert row["date"] == "2026-10-01"
    assert ledger_service.net_worth_on("2026-09-30")["net_worth"] == 18000.0  # the day before
    for day in ("2026-10-01", "2026-10-15"):  # the carve-out's day, and after: never 21,020.00
        assert ledger_service.net_worth_on(day)["net_worth"] == 18000.0
    assert figures()["nw"] == 18000.0


@ON
def test_typed_10_a_holding_dated_before_the_opening_is_carved_on_the_opening_date(book):
    r = ref_book()
    k = r["k401"]["id"]
    seed_history(r["acme"], 604.0, ["2025-12-01", "2026-01-01", "2026-02-01"])
    res = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": r["acme"]["id"],
            "account_id": k,
            "quantity": 5,
            "cost_basis": 600.0,
            "cost_currency": "USD",
            "acquired_at": "2025-12-01",
        }
    )
    assert res.get("ok"), res
    (row,) = carve_rows(k)
    assert row["date"] == "2026-01-01"  # never before the account's tracking start
    assert _value(k) == 8000.0
    assert ledger_service.net_worth_on("2026-02-01")["net_worth"] == 18000.0


@ON
def test_typed_11_a_future_dated_holding_is_refused_and_books_nothing(book):
    """Owner question TYPED-11 answered by the product: a future acquired date is refused, so a
    holding and its carve-out can never straddle today."""
    r = ref_book()
    k = r["k401"]["id"]
    res = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": r["acme"]["id"],
            "account_id": k,
            "quantity": 5,
            "cost_basis": 600.0,
            "cost_currency": "USD",
            "acquired_at": "2099-12-01",
        }
    )
    assert "in the future" in res["error"] and not res.get("ok")
    assert not carve_rows(k) and _value(k) == 8000.0 and figures()["nw"] == 18000.0


@ON
def test_typed_20_a_zero_total_still_mints_a_zero_starting_balance_row(book):
    from backend.services import accounts as accounts_service

    acct = accounts_service.create_account(
        {"name": "Empty IRA", "type": "retirement", "currency": "USD", "initial_balance": 0}
    )
    assert ledger_service.accounts.find_by_id(acct["id"])["value_source"] == "total"
    (row,) = [t for t in rows(account_id=acct["id"]) if t["description"] == "Starting Balance"]
    assert row["date"] == today() and (row.get("to_amount") or 0) == 0
    assert _value(acct["id"]) == 0.0


@ON
def test_typed_21_a_negative_total_is_accepted_and_lowers_net_worth(book):
    ref_book()
    before = figures()
    acct = _account(name="Margin", type_="investment", total=-500.0)
    assert _value(acct["id"]) == -500.0
    after = figures()
    assert after["nw"] == before["nw"] - 500.0
    assert (after["cash"], after["rta"]) == (before["cash"], before["rta"])
