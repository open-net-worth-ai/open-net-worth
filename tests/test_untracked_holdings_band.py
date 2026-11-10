"""The Untracked holdings band (ADR 2026-11-10, amendment 2) — synthetic data only.

On a Brokerage total account the ledger balance is the part of the total not entered as
holdings: invested, in no known class. With ``allow_brokerage_total_valuation`` on it bands as
``untracked_holdings`` on every reader — net worth, the chart, the Assets cards, the allocation —
never as settlement or retirement cash, and the book-level figures (rebalance, concentration,
today's move) leave it out of their denominators and say how much. Flag off: every output as
before. Net worth and cash on hand never move either way.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import portfolio as portfolio_tools
from backend.ai.tools import rebalance as rebalance_tools
from backend.services import accounts as accounts_service
from backend.services import (
    investments_book,
    owned_assets,
    portfolio_analytics,
    portfolio_rows,
    untracked_holdings,
)
from backend.services import ledger as ledger_service
from backend.services import rebalance as rebalance_service
from tests import test_account_value as base
from tests.test_account_value import _account, _add_holding, _instrument

book = base.book  # the shared isolated-book fixture (USD base, ACME at 604.00)

BROKERAGE_TOTAL = 123456.78


@pytest.fixture
def offline(book, monkeypatch):
    """Last-known prices only — no provider round trip from the projection."""
    monkeypatch.setattr(portfolio_rows, "_refresh_prices", lambda rows: ({}, {}, {"USD": 1.0}))
    monkeypatch.setattr(
        rebalance_service, "_read_profile", lambda: {"target_allocation": {"equity": 1.0}}
    )
    monkeypatch.setattr(rebalance_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(portfolio_rows, "base_currency", lambda: "USD")
    return book


def _brokerage(total=BROKERAGE_TOTAL, **extra):
    return _account(name="ACME Brokerage", total=total, type_="investment", **extra)


def _classes():
    return ledger_service.net_worth("USD")["breakdown"]["account_classes"]


def _book():
    return investments_book.investments_book("USD", None, portfolio_rows.projected_rows("USD"))


def _resolve_net_worth():
    from backend.services.mentions.resolve import _resolve_net_worth as resolve

    return resolve("net_worth")


def _tool(fn, payload):
    text, _ = asyncio.run(fn(payload))
    return json.loads(text)


# ── a Brokerage total account with no holdings ──────────────────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_a_total_with_no_holdings_is_untracked_holdings_not_settlement_cash(offline):
    acct = _brokerage()
    nw = ledger_service.net_worth("USD")
    assert nw["breakdown"]["account_classes"] == {"untracked_holdings": BROKERAGE_TOTAL}
    assert nw["net_worth"] == BROKERAGE_TOTAL  # net worth unchanged
    assert ledger_service.net_worth_bands("USD")["classes"] == {
        "untracked_holdings": BROKERAGE_TOTAL
    }
    assert ledger_service.net_worth_bands("USD")["classes"].get("cash") is None  # not cash
    (row,) = [r for r in portfolio_rows.projected_rows("USD")["rows"] if r.get("is_account_row")]
    assert row["asset_class"] == "untracked_holdings"
    # The Overview allocation: one invested slice, never "Settlement cash".
    analytics = portfolio_analytics.portfolio_analytics("USD")
    assert [r["key"] for r in analytics["allocation"]["rows"]] == ["untracked_holdings"]
    assert analytics["attention"]["has_investments"] is True
    # The Investments book is empty, and says what it can't break down.
    b = _book()
    assert b["empty"] and b["grand"] == 0
    assert b["untracked_excluded"] == {
        "total": BROKERAGE_TOTAL,
        "accounts": [
            {
                "account_id": acct["id"],
                "name": "ACME Brokerage",
                "type": "investment",
                "value": BROKERAGE_TOTAL,
            }
        ],
        "pct_of_invested": 100.0,
    }


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_the_assets_investments_card_shows_it_as_untracked_holdings(offline):
    _brokerage()
    card = owned_assets.summary("USD")["classes"]["investments"]
    assert card["value"] == BROKERAGE_TOTAL
    assert card["rows"] == [
        {"key": "positions", "label": "Positions", "value": 0.0, "pct": 0.0},
        {"key": "untracked_holdings", "label": "Untracked holdings", "value": BROKERAGE_TOTAL},
    ]
    composition = owned_assets.summary("USD")["composition"]
    assert [(r["key"], r["value"]) for r in composition["rows"]] == [
        ("investments", BROKERAGE_TOTAL)
    ]


# ── a partly tracked account: $8,000 total, $3,020 of ACME → $4,980 untracked ──────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_a_partly_tracked_account_splits_into_holdings_and_untracked(offline):
    acct = _brokerage(total=8000.0)
    _add_holding(acct["id"], _instrument())
    nw = ledger_service.net_worth("USD")
    assert nw["net_worth"] == 8000.0
    assert nw["breakdown"]["account_classes"] == {"untracked_holdings": 4980.0}
    assert nw["breakdown"]["market_positions"] == 3020.0
    # The chart's account part (its market bands need price history this book doesn't have).
    assert ledger_service.net_worth_bands("USD")["classes"] == {"untracked_holdings": 4980.0}
    card = owned_assets.summary("USD")["classes"]["investments"]
    assert card["value"] == 8000.0
    assert [(r["key"], r["value"]) for r in card["rows"]] == [
        ("positions", 3020.0),
        ("untracked_holdings", 4980.0),
    ]


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_concentration_and_today_leave_the_untracked_part_out_and_say_so(offline):
    acct = _brokerage(total=8000.0)
    _add_holding(acct["id"], _instrument())
    projection = portfolio_rows.projected_rows("USD")
    (acme,) = [r for r in projection["rows"] if r.get("instrument_id")]
    acme["daily_pl"] = 30.2  # synthetic day move
    b = investments_book.investments_book("USD", None, projection)
    assert b["grand"] == 3020.0  # the broken-down book only
    assert b["concentration"][0]["name"] == "ACME" and b["concentration"][0]["pct"] == 100.0
    # Prior close 2,989.80 — not 7,969.80, which would dilute the move by the untracked part.
    assert b["today_pct"] == pytest.approx(30.2 / (3020.0 - 30.2) * 100, abs=1e-4)
    assert b["untracked_excluded"]["total"] == 4980.0
    assert b["untracked_excluded"]["pct_of_invested"] == 62.25  # 4,980 / 8,000
    # The Overview's drift is over the same broken-down book.
    drift = portfolio_analytics._drift(projection["rows"], {"equity": 1.0})
    assert drift["total"] == 3020.0 and [r["key"] for r in drift["rows"]] == ["equity"]


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_the_brief_calls_the_share_one_of_the_holdings_entered_and_names_the_rest(offline):
    from backend.services import portfolio_brief
    from backend.services import positions as positions_service

    acct = _brokerage(total=8000.0)
    _add_holding(acct["id"], _instrument())
    signal = portfolio_brief._concentration_signal(positions_service.hydrated(), "USD", 20.0)
    assert signal["narrative"] == (
        "ACME is 100% of the holdings you've entered, 80 percentage points over the 20% "
        "single-name guideline. $4,980.00 not broken into holdings isn't counted."
    )


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_rebalance_keeps_the_untracked_part_out_and_says_how_much(offline):
    acct = _brokerage(total=8000.0)
    _add_holding(acct["id"], _instrument())
    plan = rebalance_service.rebalance_plan()
    assert plan["current_total"] == 3020.0
    assert [a["asset_class"] for a in plan["allocations"]] == ["equity"]
    assert plan["allocations"][0]["current_pct"] == 100.0  # not 37.75% beside an unknown slice
    assert plan["trades"] == []
    assert plan["untracked_excluded"]["total"] == 4980.0
    assert plan["untracked_note"] == (
        "USD 4,980 at ACME Brokerage isn't broken into holdings — add its holdings to rebalance it."
    )
    assert _tool(rebalance_tools.get_rebalance_plan, {})["untracked_note"] == plan["untracked_note"]


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_lucius_get_portfolio_says_the_untracked_part_is_invested_not_cash(offline):
    acct = _brokerage(total=8000.0)
    _add_holding(acct["id"], _instrument())
    today = _tool(portfolio_tools.get_portfolio, {})["today"]
    assert today["untracked_excluded"]["total"] == 4980.0
    assert "invested" in today["untracked_note"] and "not cash" in today["untracked_note"]


# ── a retirement Brokerage total account ───────────────────────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_a_retirement_total_is_untracked_and_still_counts_as_retirement(offline):
    acct = _account(total=8000.0)  # "Old 401(k)", retirement
    _add_holding(acct["id"], _instrument())
    nw = ledger_service.net_worth("USD")
    bd = nw["breakdown"]
    assert bd["account_classes"] == {"untracked_holdings": 4980.0}
    assert "retirement" not in bd["account_classes"]  # not "retirement cash"
    assert bd["retirement_base"] == 8000.0  # 3,020 held + 4,980 untracked
    assert bd["retirement_untracked_base"] == 4980.0
    assert nw["net_worth_excl_retirement"] == 0.0  # the whole account is still subtracted
    summary = owned_assets.summary("USD")
    card = summary["classes"]["retirement"]
    assert card["value"] == 8000.0
    assert [(r["key"], r["value"]) for r in card["rows"]] == [
        ("positions", 3020.0),
        ("untracked_holdings", 4980.0),
    ]
    assert summary["classes"]["investments"]["value"] == 0.0  # not double counted there
    assert [(r["key"], r["value"]) for r in summary["composition"]["rows"]] == [
        ("retirement", 8000.0)
    ]
    assert _book()["untracked_excluded"]["accounts"][0]["type"] == "retirement"
    mention = _resolve_net_worth()
    assert mention["untracked_holdings"] == 4980.0 and mention["retirement"] == 8000.0


# ── a Live holdings account: unchanged ─────────────────────────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_a_live_holdings_account_keeps_its_balance_as_settlement_cash(offline):
    acct = _brokerage(total=500.0, value_source="holdings")
    _add_holding(acct["id"], _instrument())
    nw = ledger_service.net_worth("USD")
    assert nw["breakdown"]["account_classes"] == {"settlement_cash": 500.0}
    assert nw["net_worth"] == 3520.0
    assert "retirement_untracked_base" not in nw["breakdown"]
    b = _book()
    assert b["untracked_excluded"] == {"total": 0.0, "accounts": [], "pct_of_invested": 0.0}
    assert rebalance_service.rebalance_plan()["untracked_note"] is None
    assert "untracked_excluded" not in _tool(portfolio_tools.get_portfolio, {})["today"]


# ── the "total kept" rows are part of the untracked part ───────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_a_priced_true_up_stays_in_the_untracked_band(offline):
    from backend.db.instrument_store import InstrumentStore
    from backend.services import account_value_cost as cost

    acct = _brokerage(total=8000.0)
    inst = _instrument(price=None)
    _add_holding(acct["id"], inst, qty=5, cost=400.0)  # carved at cost: 6,000 untracked
    assert _classes() == {"untracked_holdings": 6000.0}
    InstrumentStore().update_by_id(inst["id"], {"manual_price": 604.0})
    assert cost.settle() == [acct["id"]]  # books "Holding priced — total kept"
    nw = ledger_service.net_worth("USD")
    assert nw["breakdown"]["account_classes"] == {"untracked_holdings": 4980.0}
    assert nw["net_worth"] == 8000.0


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_a_moved_holding_keeps_both_untracked_parts_in_the_band(offline):
    from backend.services import positions as positions_service

    a = _brokerage(total=8000.0)
    b = _account(name="ACME 401(k)", type_="retirement", total=5000.0)
    added = _add_holding(a["id"], _instrument())
    positions_service.update_position(added["position_id"], {"account_id": b["id"]})
    nw = ledger_service.net_worth("USD")  # each side books "Holding moved — total kept"
    assert nw["breakdown"]["account_classes"] == {"untracked_holdings": 9980.0}
    assert nw["breakdown"]["retirement_untracked_base"] == 1980.0
    assert nw["net_worth"] == 13000.0


# ── the chart: every point bands by the account's CURRENT source ───────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_history_bands_every_point_by_the_current_source_so_a_switch_does_not_jump(offline):
    acct = _brokerage(total=500.0, value_source="holdings")
    before = ledger_service.net_worth_history()
    assert before and all(p["classes"] == {"settlement_cash": 500.0} for p in before)
    accounts_service.update_account(acct["id"], {"value_source": "total"})  # no write
    after = ledger_service.net_worth_history()
    assert [p["date"] for p in after] == [p["date"] for p in before]
    assert all(p["classes"] == {"untracked_holdings": 500.0} for p in after)
    assert [p["net_worth"] for p in after] == [p["net_worth"] for p in before]
    past = after[0]["date"]
    if past != after[-1]["date"]:
        assert ledger_service.net_worth_on(past)["classes"] == {"untracked_holdings": 500.0}


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_account_ids_reads_a_stored_source_without_touching_holdings(offline, monkeypatch):
    total = _brokerage(total=1.0)
    live = _brokerage(total=1.0, value_source="holdings")
    cash = _account(name="Checking", type_="cash", total=1.0)
    from backend.services import account_value

    monkeypatch.setattr(account_value, "held_account_ids", lambda *a: pytest.fail("read"))
    rows = [ledger_service.accounts.find_by_id(a["id"]) for a in (total, live, cash)]
    assert untracked_holdings.account_ids(rows) == frozenset({total["id"]})


# ── flag off: every output as before ───────────────────────────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=False)
def test_flag_off_the_balance_is_still_settlement_cash_everywhere(offline):
    acct = _brokerage(total=8000.0)
    _add_holding(acct["id"], _instrument())
    retire = _account(total=100.0)
    nw = ledger_service.net_worth("USD")
    assert nw["breakdown"]["account_classes"] == {"settlement_cash": 8000.0, "retirement": 100.0}
    assert "retirement_untracked_base" not in nw["breakdown"]
    assert untracked_holdings.account_ids([acct, retire]) == frozenset()
    outputs = {
        "net_worth": nw,
        "bands": ledger_service.net_worth_bands("USD"),
        "history": ledger_service.net_worth_history(),
        "book": _book(),
        "rebalance": rebalance_service.rebalance_plan(),
        "assets": owned_assets.summary("USD"),
        "portfolio": _tool(portfolio_tools.get_portfolio, {}),
        "mention": _resolve_net_worth(),
    }
    for name, out in outputs.items():
        assert "untracked" not in json.dumps(out), name
    from backend.services import portfolio_brief
    from backend.services import positions as positions_service

    signal = portfolio_brief._concentration_signal(positions_service.hydrated(), "USD", 20.0)
    assert signal["narrative"] == (
        "ACME is 100% of your investments, 80 percentage points over the 20% single-name guideline."
    )
    card = outputs["assets"]["classes"]["investments"]
    assert [r["key"] for r in card["rows"]] == ["positions", "settlement_cash"]
    assert outputs["assets"]["classes"]["retirement"]["rows"] is None
