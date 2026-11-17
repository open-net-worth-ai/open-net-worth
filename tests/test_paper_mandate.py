"""A paper portfolio's mandate: its limits are checked before a BUY is placed (refused unless
the owner chose to break it; never for an Automation), a sell is never checked, the page reports
each rule against the book as it stands, and Edit / Start over keep it. ACME is 100 USD."""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError, ValidationError
from backend.db.paper_store import PaperOrderStore
from backend.services.paper_trading import mandate, qa_quotes, views
from backend.services.paper_trading.facade import place, preview
from backend.services.paper_trading.service import apply_paper_change


def _new(m: dict | None = None):
    return apply_paper_change(
        {"op": "create", "name": "M", "cash": 10_000, "home_currency": "USD", "mandate": m}
    )["portfolio"]


def _buy(pid, qty, key="inst:inst_acme", origin="owner", **kw):
    return place(pid, {"asset_key": key, "side": "buy", "quantity": qty, **kw}, origin=origin)


# ── clean ───────────────────────────────────────────────────────────────────────────────────


def test_clean_keeps_what_is_set_and_reads_a_typed_list():
    got = mandate.clean(
        {"goal": " Beat SPY ", "max_position_pct": "20", "min_cash_pct": "", "universe": "acme, x"}
    )
    assert got == {"goal": "Beat SPY", "max_position_pct": 20.0, "universe": ["ACME", "X"]}
    assert mandate.clean({"goal": "  "}) is None


@pytest.mark.parametrize(
    "bad", [{"max_position_pct": 0}, {"max_position_pct": 150}, {"max_positions": 2.5}]
)
def test_clean_refuses_a_limit_out_of_range(bad):
    with pytest.raises(ValidationError):
        mandate.clean(bad)


# ── pre-trade check ─────────────────────────────────────────────────────────────────────────


def test_a_buy_over_the_cap_is_refused_and_previewed(paper_db):
    pid = _new({"max_position_pct": 20})["id"]
    pv = preview(pid, {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 30})
    assert [b["rule"] for b in pv["mandate_breaches"]] == ["max_position_pct"]
    with pytest.raises(ConflictError, match="30% of the portfolio; the mandate allows 20%"):
        _buy(pid, 30)
    assert PaperOrderStore().for_portfolio(pid) == []
    assert (
        preview(pid, {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 10})[
            "mandate_breaches"
        ]
        == []
    )


def test_the_owner_can_break_it_and_the_order_says_so(paper_db):
    pid = _new({"max_position_pct": 20})["id"]
    out = _buy(pid, 30, override_mandate=True)
    assert out["order"]["status"] == "filled"
    assert out["order"]["mandate_breaches"] == [
        "ACME would be 30% of the portfolio; the mandate allows 20%."
    ]


def test_an_automation_can_never_break_it(paper_db):
    pid = _new({"max_position_pct": 20})["id"]
    with pytest.raises(ConflictError, match="mandate"):
        _buy(pid, 30, origin="automation:aut_1", override_mandate=True)


def test_cash_floor_holdings_count_and_universe(paper_db):
    pid = _new({"min_cash_pct": 50, "max_positions": 1, "universe": ["ACME"]})["id"]
    _buy(pid, 10)
    pv = preview(pid, {"asset_key": "inst:inst_coin", "side": "buy", "notional": 6_000})
    assert {b["rule"] for b in pv["mandate_breaches"]} == {
        "universe",
        "min_cash_pct",
        "max_positions",
    }


def test_a_sell_is_never_checked(paper_db):
    pid = _new()["id"]
    _buy(pid, 30)
    apply_paper_change({"op": "update", "id": pid, "mandate": {"max_position_pct": 10}})
    out = place(pid, {"asset_key": "inst:inst_acme", "side": "sell", "quantity": 5})
    assert out["order"]["status"] == "filled"


# ── status, edit, start over ────────────────────────────────────────────────────────────────


def test_the_page_reports_each_rule_against_the_book(paper_db):
    pid = _new({"goal": "Test ACME", "max_position_pct": 20, "stop_loss_pct": 10})["id"]
    _buy(pid, 30, override_mandate=True)
    qa_quotes.set_state(prices={"ACME": 80.0})
    st = views.page(pid)["mandate"]
    assert st["goal"] == "Test ACME"
    by = {r["rule"]: r for r in st["rules"]}
    assert not by["max_position_pct"]["ok"]
    assert by["stop_loss_pct"]["detail"].startswith("Past it: ACME -20%")
    assert st["broken"] == 2


def test_edit_replaces_or_clears_it_and_start_over_keeps_it(paper_db):
    pid = _new({"max_position_pct": 20})["id"]
    row = apply_paper_change({"op": "update", "id": pid, "mandate": {"min_cash_pct": 5}})[
        "portfolio"
    ]
    assert row["mandate"] == {"min_cash_pct": 5.0}
    apply_paper_change({"op": "update", "id": pid, "name": "Renamed"})
    nxt = apply_paper_change({"op": "start_over", "id": pid})["portfolio"]
    assert nxt["mandate"] == {"min_cash_pct": 5.0}
    cleared = apply_paper_change({"op": "update", "id": nxt["id"], "mandate": None})["portfolio"]
    assert cleared["mandate"] is None and views.page(nxt["id"])["mandate"] is None
