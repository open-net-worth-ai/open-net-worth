"""The paper book and its engine: derivation matches the real book's lot math, cover refuses
what the portfolio can't pay for or doesn't hold (AC6), a day order expires (AC5), lifecycle
re-runs write nothing twice and a split leaves value unchanged (AC7), a buy abroad converts
cash at the rate plus the FX spread, and every fill records its quote (AC3)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.services import lot_math
from backend.services.paper_trading import book, engine, qa_quotes, service, views
from backend.services.paper_trading.service import apply_paper_change
from tests.paper_helpers import NOW


def _new(cash=10_000.0, **kw):
    return apply_paper_change(
        {"op": "create", "name": "T", "cash": cash, "home_currency": "USD", **kw}
    )["portfolio"]


def _place(pid, **order):
    return apply_paper_change({"op": "place", "portfolio_id": pid, "order": order})


# ── derivation ──────────────────────────────────────────────────────────────────────────────


def test_derivation_matches_the_real_lot_math():
    evs = [
        {"id": "a", "kind": "deposit", "at": "2026-01-01", "cash_delta": {"USD": 5000}},
        {
            "id": "b",
            "kind": "buy",
            "at": "2026-01-02",
            "asset_key": "inst:x",
            "quantity_delta": 10,
            "price": 100,
            "cash_delta": {"USD": -1001},
            "payload": {"currency": "USD", "commission": 1},
        },
        {
            "id": "c",
            "kind": "buy",
            "at": "2026-02-02",
            "asset_key": "inst:x",
            "quantity_delta": 10,
            "price": 120,
            "cash_delta": {"USD": -1200},
            "payload": {"currency": "USD"},
        },
        {
            "id": "d",
            "kind": "sell",
            "at": "2026-03-02",
            "asset_key": "inst:x",
            "quantity_delta": -15,
            "price": 130,
            "cash_delta": {"USD": 1950},
            "payload": {"currency": "USD"},
        },
    ]
    b = book.derive(evs)
    lots = [
        {
            "id": "b",
            "acquired_at": "2026-01-02",
            "original_qty": 10,
            "remaining_qty": 10,
            "unit_cost": 100.0,
            "fees": 1.0,
            "cost_currency": "USD",
        },
        {
            "id": "c",
            "acquired_at": "2026-02-02",
            "original_qty": 10,
            "remaining_qty": 10,
            "unit_cost": 120.0,
            "fees": 0.0,
            "cost_currency": "USD",
        },
    ]
    real = lot_math.compute_disposal(
        {"id": "p"},
        lots,
        {
            "quantity": 15,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "disposed_at": "2026-03-02",
        },
        rates={"USD": 1.0},
        base="USD",
    )
    assert b.realized[0]["gain"] == real["realized_gain"]
    assert b.quantity("inst:x") == 5
    assert b.cash == {"USD": 5000 - 1001 - 1200 + 1950}


def test_a_split_scales_only_lots_bought_before_it():
    evs = [
        {
            "id": "b",
            "kind": "buy",
            "at": "2026-01-02",
            "asset_key": "k",
            "quantity_delta": 10,
            "price": 100,
            "payload": {"currency": "USD"},
        },
        {
            "id": "s",
            "kind": "split",
            "at": "2026-02-01",
            "asset_key": "k",
            "payload": {"ratio": 4, "before": "2026-02-01"},
        },
        {
            "id": "c",
            "kind": "buy",
            "at": "2026-02-03",
            "asset_key": "k",
            "quantity_delta": 1,
            "price": 26,
            "payload": {"currency": "USD"},
        },
    ]
    b = book.derive(evs)
    assert b.quantity("k") == 41
    assert b.cost("k") == pytest.approx(1000 + 26)  # value of the old lots unchanged by the split


# ── cover (AC6) ────────────────────────────────────────────────────────────────────────────


def test_a_buy_over_free_cash_is_refused_with_the_amount_available(paper_db):
    pid = _new(1000.0)["id"]
    out = _place(pid, asset_key="inst:inst_acme", side="buy", quantity=20)
    assert out["order"]["status"] == "rejected"
    assert out["order"]["available"] == 1000.0
    assert "1,000.00 USD available" in out["order"]["state_reason"]


def test_a_sell_over_what_is_held_is_refused(paper_db):
    pid = _new()["id"]
    _place(pid, asset_key="inst:inst_acme", side="buy", quantity=5)
    out = _place(pid, asset_key="inst:inst_acme", side="sell", quantity=6)
    assert out["order"]["status"] == "rejected" and out["order"]["available"] == 5


def test_an_open_order_reserves_cash(paper_db):
    pid = _new(1500.0)["id"]
    first = _place(
        pid, asset_key="inst:inst_acme", side="buy", quantity=10, order_type="limit", limit_price=90
    )
    assert first["order"]["status"] == "open" and first["order"]["reserve"] == {"USD": 900.0}
    second = _place(pid, asset_key="inst:inst_acme", side="buy", quantity=7)
    assert second["order"]["status"] == "rejected" and second["order"]["available"] == 600.0


def test_a_buy_abroad_converts_at_the_rate_plus_the_spread(paper_db):
    pid = _new()["id"]
    out = _place(pid, asset_key="inst:inst_acmej", side="buy", quantity=1)
    assert out["order"]["status"] == "filled"
    evs = service.events(pid)
    convert = next(e for e in evs if e["kind"] == "convert")
    rate = 0.0067 * 1.001
    assert convert["price"] == pytest.approx(rate)
    b = service.derive(pid)
    assert b.cash["JPY"] == pytest.approx(0.0, abs=1e-6)
    assert b.cash["USD"] == pytest.approx(10_000 - 2000.4 * rate, rel=1e-6)


def test_without_auto_convert_a_buy_abroad_needs_that_currency(paper_db):
    pid = _new(settings={"auto_convert": False})["id"]
    out = _place(pid, asset_key="inst:inst_acmej", side="buy", quantity=1)
    assert out["order"]["status"] == "rejected" and "JPY" in out["order"]["state_reason"]


def test_a_currency_conversion(paper_db):
    pid = _new()["id"]
    out = _place(pid, asset_key="ccy:EUR", side="buy", notional=1080)
    assert out["order"]["status"] == "filled"
    b = service.derive(pid)
    assert b.cash["EUR"] == pytest.approx(1080 / (1.08 * 1.001), rel=1e-6)


# ── fills record their quote (AC3) ─────────────────────────────────────────────────────────


def test_a_market_fill_records_price_source_age_session_and_basis(paper_db):
    pid = _new()["id"]
    out = _place(pid, asset_key="inst:inst_acme", side="buy", quantity=2, note="Why not")
    fill = out["order"]["fill"]
    assert {"price", "source", "quote_as_of", "session", "basis"} <= set(fill)
    assert fill["session"] == "REGULAR" and fill["basis"] == "quote"
    assert out["toast"] == "Paper buy filled: 2 ACME at 100.02"
    [buy] = [e for e in service.events(pid) if e["kind"] == "buy"]
    assert buy["payload"]["note"] == "Why not"


@pytest.mark.parametrize(
    "asset_key,basis", [("inst:inst_idx", "synthetic"), ("inst:inst_coin", "quote")]
)
def test_the_fill_basis_follows_the_kind(paper_db, asset_key, basis):
    pid = _new()["id"]
    out = _place(pid, asset_key=asset_key, side="buy", quantity=1)
    assert out["order"]["fill"]["basis"] == basis


def test_a_manual_asset_trades_at_your_price_and_is_labelled(paper_db):
    pid = _new()["id"]
    out = apply_paper_change(
        {
            "op": "place",
            "portfolio_id": pid,
            "order": {
                "asset": {"kind": "manual", "name": "Pre-IPO Acme", "currency": "USD"},
                "side": "buy",
                "quantity": 10,
                "price": 50.0,
            },
        }
    )
    assert out["order"]["fill"]["basis_label"] == "Your price"
    page = views.page(pid)
    [h] = page["holdings"]
    assert h["basis_label"] == "Your price" and h["value_home"] == 500.0


# ── the engine (AC4, AC5) ──────────────────────────────────────────────────────────────────


def test_a_limit_waits_with_its_reason_then_fills_when_reached(paper_db):
    pid = _new()["id"]
    out = _place(
        pid,
        asset_key="inst:inst_acme",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=95,
        tif="gtc",
    )
    assert out["order"]["state_reason"].startswith("Waits until the price reaches 95.00")
    assert engine.tick()["filled"] == 0
    qa_quotes.set_state(prices={"ACME": 94.0})
    assert engine.tick()["filled"] == 1
    [o] = service.portfolio_orders(pid)
    assert o["fill"]["price"] == pytest.approx(94.0 * 1.0002)


def test_a_day_order_expires_at_its_session_close(paper_db):
    pid = _new()["id"]
    _place(
        pid, asset_key="inst:inst_acme", side="buy", quantity=1, order_type="limit", limit_price=50
    )
    later = NOW + timedelta(days=2)
    assert engine.tick(later)["expired"] == 1
    [o] = service.portfolio_orders(pid)
    assert o["status"] == "expired" and o["reserve"] == {}


def test_a_fund_order_waits_for_its_nav(paper_db):
    pid = _new()["id"]
    qa_quotes.set_state("closed")
    out = _place(pid, asset_key="inst:inst_fund", side="buy", notional=400)
    assert out["order"]["status"] == "open" and "NAV" in out["order"]["state_reason"]


def test_a_crypto_order_fills_while_markets_are_closed(paper_db):
    pid = _new()["id"]
    qa_quotes.set_state("closed")
    assert (
        _place(pid, asset_key="inst:inst_coin", side="buy", quantity=1)["order"]["status"]
        == "filled"
    )


def test_cancel(paper_db):
    pid = _new()["id"]
    qa_quotes.set_state("closed")
    o = _place(pid, asset_key="inst:inst_acme", side="buy", quantity=1)["order"]
    out = apply_paper_change({"op": "cancel", "order_id": o["id"]})
    assert out["order"]["status"] == "cancelled"
    with pytest.raises(Exception):
        apply_paper_change({"op": "cancel", "order_id": o["id"]})


def test_lifecycle_rerun_writes_each_event_once(paper_db):
    pid = _new()["id"]
    _place(pid, asset_key="inst:inst_acme", side="buy", quantity=10)
    drafts = [
        {
            "kind": "dividend",
            "at": "2026-12-15T00:00:00+00:00",
            "asset_key": "inst:inst_acme",
            "cash_delta": {"USD": 5.0},
            "idem_key": "div:inst_acme:2026-12-15",
        }
    ]
    first = apply_paper_change({"op": "lifecycle", "portfolio_id": pid, "events": drafts})
    again = apply_paper_change({"op": "lifecycle", "portfolio_id": pid, "events": drafts})
    assert len(first["written"]) == 1 and again["written"] == []


def test_a_frozen_holding_refuses_trades(paper_db):
    pid = _new()["id"]
    _place(pid, asset_key="inst:inst_acme", side="buy", quantity=1)
    apply_paper_change(
        {
            "op": "lifecycle",
            "portfolio_id": pid,
            "events": [
                {
                    "kind": "freeze",
                    "asset_key": "inst:inst_acme",
                    "payload": {"reason": "Spin-off"},
                    "idem_key": "freeze:x",
                }
            ],
        }
    )
    out = _place(pid, asset_key="inst:inst_acme", side="sell", quantity=1)
    assert out["order"]["status"] == "rejected" and "Spin-off" in out["order"]["state_reason"]
    assert views.page(pid)["holdings"][0]["frozen"] == "Spin-off"


def test_archived_portfolios_refuse_orders_and_keep_history(paper_db):
    pid = _new()["id"]
    _place(pid, asset_key="inst:inst_acme", side="buy", quantity=1)
    apply_paper_change({"op": "archive", "id": pid})
    with pytest.raises(Exception, match="archived"):
        _place(pid, asset_key="inst:inst_acme", side="buy", quantity=1)
    assert len(service.events(pid)) == 2


def test_active_portfolios_are_capped(paper_db, monkeypatch):
    monkeypatch.setattr(service, "MAX_ACTIVE_PORTFOLIOS", 2)
    _new(), _new()
    with pytest.raises(Exception, match="up to 2"):
        _new()


def test_delete_removes_everything_of_that_portfolio_only(paper_db):
    keep, gone = _new()["id"], _new()["id"]
    _place(gone, asset_key="inst:inst_acme", side="buy", quantity=1)
    apply_paper_change({"op": "delete", "id": gone})
    assert service.events(gone) == [] and service.portfolio_orders(gone) == []
    assert len(service.events(keep)) == 1
