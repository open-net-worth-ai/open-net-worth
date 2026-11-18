"""Regressions for the paper-trading code review (2026-11-15): Start over is atomic and
race-safe, no value row is written while a holding is unpriced, a dividend after a split is
sized on the post-split quantity, a fund distribution can be reinvested, a foreign holding's
P&L includes the currency's move, an unknown-age or stale quote never fills, and the
portfolio's extended-hours setting is honoured."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.core.errors import ConflictError, ValidationError
from backend.db.paper_store import PaperPortfolioStore, PaperValueStore
from backend.services.paper_trading import lifecycle, qa_quotes, quotes, seed, service, views
from backend.services.paper_trading.kinds import adapter
from backend.services.paper_trading.model import DEFAULT_SETTINGS, PaperAsset, PaperQuote, Wait
from backend.services.paper_trading.service import apply_paper_change
from tests.paper_helpers import NOW


def _new(**kw):
    return apply_paper_change(
        {"op": "create", "name": "R", "cash": 10_000, "home_currency": "USD", **kw}
    )["portfolio"]


def _active():
    return [p for p in PaperPortfolioStore().all() if p["status"] == "active"]


# ── Start over ──────────────────────────────────────────────────────────────────────────────


def test_a_start_over_whose_opening_fails_leaves_the_old_run_active(paper_db, monkeypatch):
    pid = _new()["id"]

    def boom(*a, **k):
        raise ValidationError("You have no priced holdings to copy yet.")

    monkeypatch.setattr(seed, "opening", boom)
    with pytest.raises(ValidationError):
        apply_paper_change({"op": "start_over", "id": pid})
    assert [p["id"] for p in _active()] == [pid]


def test_two_start_overs_never_make_two_runs(paper_db, monkeypatch):
    pid = _new()["id"]
    real_prepare = service._prepare

    def racing(change):
        out = real_prepare(change)
        # Another start-over archives the run between our read and our write.
        PaperPortfolioStore().update_by_id(pid, {"status": "archived"})
        return out

    monkeypatch.setattr(service, "_prepare", racing)
    with pytest.raises(ConflictError):
        apply_paper_change({"op": "start_over", "id": pid})
    assert _active() == []  # the losing start-over inserted nothing


# ── value rows ──────────────────────────────────────────────────────────────────────────────


def test_no_value_row_is_written_while_a_holding_is_unpriced(paper_db, monkeypatch):
    pid = _new()["id"]
    apply_paper_change(
        {
            "op": "place",
            "portfolio_id": pid,
            "order": {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 10},
        }
    )
    assert service.snapshot_values(pid)["value_home"] == pytest.approx(10_000 - 1.0, abs=2)
    before = PaperValueStore().for_portfolio(pid)
    monkeypatch.setattr(quotes, "fetch", lambda *a, **k: {})  # the provider is down
    assert service.snapshot_values(pid) is None
    assert PaperValueStore().for_portfolio(pid) == before


def test_a_missing_quote_keeps_the_last_price_never_zero(paper_db, monkeypatch):
    """One missing quote once drew a portfolio at -71% (2026-11-19): a holding with no usable
    quote keeps its last good price, so the figure and the chart barely move, and it says so."""
    from backend.services.paper_trading import valuation

    pid = _new()["id"]
    order = {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 10}
    apply_paper_change({"op": "place", "portfolio_id": pid, "order": order})
    row, bk = service.portfolio(pid), service.derive(pid)
    good = valuation.value(row, bk, NOW)
    monkeypatch.setattr(quotes, "fetch", lambda *a, **k: {})  # the provider is down
    down = valuation.value(row, bk, NOW)
    assert down["value_home"] == pytest.approx(good["value_home"])
    [acme] = down["holdings"]
    assert acme["estimated"] == "last" and down["estimated"] == ["inst:inst_acme"]


def test_no_quote_since_a_restart_is_valued_at_cost(paper_db, monkeypatch):
    from backend.services.paper_trading import valuation

    pid = _new()["id"]
    order = {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 10}
    apply_paper_change({"op": "place", "portfolio_id": pid, "order": order})
    monkeypatch.setattr(valuation, "_LAST_PRICE", {})  # as just after a restart
    monkeypatch.setattr(quotes, "fetch", lambda *a, **k: {})
    val = valuation.value(service.portfolio(pid), service.derive(pid), NOW)
    [acme] = val["holdings"]
    assert acme["estimated"] == "cost"
    assert val["value_home"] == pytest.approx(10_000, abs=2)  # cash spent + the holding at cost


def test_a_new_run_has_its_opening_value_row(paper_db):
    pid = _new()["id"]
    [row] = PaperValueStore().for_portfolio(pid)
    assert row["value_home"] == 10_000 and row["cash_home"] == 10_000


# ── lifecycle ───────────────────────────────────────────────────────────────────────────────

ACME = PaperAsset(key="inst:x", kind="listed", symbol="X", name="X", currency="USD")
FUND = PaperAsset(key="inst:f", kind="fund_nav", symbol="F", name="F", currency="USD")


def _bought(key, qty, day="2026-10-01"):
    return [
        {
            "id": "b",
            "kind": "buy",
            "at": f"{day}T15:00:00+00:00",
            "asset_key": key,
            "quantity_delta": qty,
            "price": 10,
            "payload": {"currency": "USD"},
        }
    ]


def test_a_dividend_after_a_split_in_the_same_run_uses_the_post_split_quantity():
    actions = {
        "splits": [{"date": "2026-10-10", "ratio": 4.0, "label": "4:1"}],
        "dividends": [{"date": "2026-10-20", "amount": 0.25}],
        "closes": [],
    }
    drafts = lifecycle.drafts_for(
        ACME, actions, _bought(ACME.key, 10), start="2026-10-01", today="2026-11-01"
    )
    [div] = [d for d in drafts if d["kind"] == "dividend"]
    assert div["payload"]["quantity"] == 40 and div["cash_delta"] == {"USD": 10.0}


def test_a_fund_distribution_can_be_reinvested_at_that_days_nav():
    actions = {
        "splits": [],
        "dividends": [{"date": "2026-10-20", "amount": 0.5}],
        "closes": [{"date": "2026-10-20", "close": 25.0}],
    }
    drafts = lifecycle.drafts_for(
        FUND,
        actions,
        _bought(FUND.key, 100),
        start="2026-10-01",
        today="2026-10-21",
        distributions="reinvest",
    )
    dist, drip = drafts
    assert dist["kind"] == "distribution" and dist["cash_delta"] == {"USD": 50.0}
    assert (
        drip["kind"] == "buy"
        and drip["quantity_delta"] == 2.0
        and drip["cash_delta"] == {"USD": -50.0}
    )
    assert (
        lifecycle.drafts_for(
            FUND,
            actions,
            _bought(FUND.key, 100),
            start="2026-10-01",
            today="2026-10-21",
            distributions="cash",
        )[1:]
        == []
    )


# ── P&L in the home currency ────────────────────────────────────────────────────────────────


def test_a_foreign_holdings_pnl_includes_the_currency_move(paper_db):
    pid = _new()["id"]
    apply_paper_change(
        {
            "op": "place",
            "portfolio_id": pid,
            "order": {"asset_key": "inst:inst_acmej", "side": "buy", "quantity": 1},
        }
    )
    qa_quotes.set_state(prices={"9999.T": 2000.4}, fx={"JPYUSD": 0.0070})  # yen up, price flat
    [h] = views.page(pid)["holdings"]
    assert h["pnl_home"] > 0  # the price did not move; the yen did


# ── stale and unknown-age quotes ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["listed", "crypto"])
@pytest.mark.parametrize("as_of", [None, NOW - timedelta(minutes=25)])
def test_a_quote_of_unknown_or_old_age_never_fills(kind, as_of):
    ad = adapter(kind)
    a = PaperAsset(key="inst:x", kind=kind, symbol="X", name="X", currency="USD")
    q = PaperQuote(
        last=10.0,
        currency="USD",
        as_of=as_of,
        basis="quote",
        source="t",
        session_state="REGULAR",
        is_open=True,
    )
    d = ad.decide(
        {"side": "buy", "order_type": "market"}, q, ad.session(a, q, NOW), DEFAULT_SETTINGS, NOW
    )
    assert isinstance(d, Wait) and "No fresh price" in d.reason


# ── the extended-hours setting ──────────────────────────────────────────────────────────────


def test_the_portfolios_extended_hours_setting_allows_pre_market_fills(paper_db, monkeypatch):
    def pre_market(assets, now, marks=None):
        return {
            a.key: PaperQuote(
                last=100.0,
                currency="USD",
                as_of=now,
                basis="quote",
                source="t",
                session_state="PRE",
                is_open=True,
            )
            for a in assets
        }

    monkeypatch.setattr(quotes, "fetch", pre_market)
    off = _new()["id"]
    on = _new(settings={"extended_hours": True})["id"]
    order = {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 1}
    assert (
        apply_paper_change({"op": "place", "portfolio_id": off, "order": order})["order"]["status"]
        == "open"
    )
    assert (
        apply_paper_change({"op": "place", "portfolio_id": on, "order": order})["order"]["status"]
        == "filled"
    )


@pytest.mark.parametrize("side, stop", [("buy", 90.0), ("sell", 110.0)])
def test_a_stop_already_past_the_price_is_refused_at_placement(paper_db, side, stop):
    pid = _new()["id"]
    if side == "sell":
        apply_paper_change(
            {
                "op": "place",
                "portfolio_id": pid,
                "order": {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 1},
            }
        )
    with pytest.raises(ValidationError, match="stop goes"):
        apply_paper_change(
            {
                "op": "place",
                "portfolio_id": pid,
                "order": {
                    "asset_key": "inst:inst_acme",
                    "side": side,
                    "quantity": 1,
                    "order_type": "stop",
                    "stop_price": stop,
                },
            }
        )


def test_deleting_a_portfolio_removes_its_own_contracts(paper_db):
    from backend.db.paper_store import PaperContractStore

    pid = _new()["id"]
    apply_paper_change(
        {
            "op": "place",
            "portfolio_id": pid,
            "order": {
                "asset": {"kind": "manual", "name": "Flat", "currency": "USD"},
                "side": "buy",
                "quantity": 1,
                "price": 500.0,
            },
        }
    )
    assert len(PaperContractStore().all()) == 1
    apply_paper_change({"op": "delete", "id": pid})
    assert PaperContractStore().all() == []
