"""Every phase-1 paper asset kind, one table each (PRD acceptance criterion 2): its quote, its
session, a market fill, a limit fill, a closed-market wait and its lifecycle events. Pure — no
I/O: each row is a quote, a clock and an order, and the adapter's decision."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.services.paper_trading import lifecycle
from backend.services.paper_trading.kinds import adapter
from backend.services.paper_trading.kinds.fund_nav import nav_date_for
from backend.services.paper_trading.kinds.manual import proxied_price
from backend.services.paper_trading.model import (
    DEFAULT_SETTINGS,
    Fill,
    PaperAsset,
    PaperQuote,
    Reject,
    Wait,
)

UTC = timezone.utc
# Wed 2026-11-18 15:00 UTC = 10:00 New York (markets open); Sat 2026-11-21 = weekend.
WED = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)
SAT = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
S = dict(DEFAULT_SETTINGS)


def q(
    last=100.0,
    *,
    bid=None,
    ask=None,
    state="REGULAR",
    is_open=True,
    age_s=5,
    basis="quote",
    navs=None,
    now=WED,
    ccy="USD",
):
    return PaperQuote(
        last=last,
        bid=bid,
        ask=ask,
        currency=ccy,
        as_of=now - timedelta(seconds=age_s),
        basis=basis,
        source="test",
        session_state=state,
        is_open=is_open,
        next_open=now + timedelta(hours=18),
        timezone="America/New_York",
        navs=navs or {},
    )


def asset(kind, **extra):
    return PaperAsset(
        key=f"inst:{kind}", kind=kind, symbol="ACME", name="Acme", currency="USD", extra=extra
    )


def order(side="buy", otype="market", **kw):
    return {"side": side, "order_type": otype, "quantity": 1, **kw}


def decide(kind, o, quote, now=WED, a=None):
    ad = adapter(kind)
    a = a or asset(kind)
    return ad.decide(o, quote, ad.session(a, quote, now), S, now)


# ── listed: stocks, ETFs, ETPs, REITs, ADRs ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "o, quote, expect",
    [
        (order(), q(ask=100.05, bid=99.95), ("fill", 100.05)),  # market buy at the ask
        (order("sell"), q(ask=100.05, bid=99.95), ("fill", 99.95)),  # market sell at the bid
        (order(), q(), ("fill", 100.05)),  # no book: last × (1 + 5 bps)
        (order(otype="limit", limit_price=99.0), q(), ("wait", "reaches 99.00")),
        (order(otype="limit", limit_price=101.0), q(), ("fill", 100.05)),  # gapped through: better
        (order(otype="limit", limit_price=100.02), q(), ("fill", 100.02)),  # at the limit
        (order("sell", "stop", stop_price=95.0), q(), ("wait", "reaches 95.00")),
        (order("sell", "stop", stop_price=101.0), q(), ("fill", 99.95)),  # triggered, at market
        (order(), q(state="CLOSED", is_open=False), ("wait", "Market closed")),
        (order(), q(age_s=21 * 60), ("wait", "No fresh price")),
        (order(), q(state="PRE", is_open=True), ("wait", "Market closed")),  # extended not allowed
    ],
)
def test_listed(o, quote, expect):
    d = decide("listed", o, quote)
    _check(d, expect)


def test_listed_extended_hours_when_allowed():
    a = asset("listed", extended_ok=True)
    assert isinstance(decide("listed", order(), q(state="POST"), a=a), Fill)


def test_listed_lifecycle_dividend_split_and_freeze():
    a = asset("listed")
    events = [
        {
            "id": "e1",
            "kind": "buy",
            "at": "2026-10-01T15:00:00+00:00",
            "asset_key": a.key,
            "quantity_delta": 10,
            "price": 100,
        }
    ]
    actions = {
        "dividends": [{"date": "2026-10-15", "amount": 0.5}],
        "splits": [{"date": "2026-11-02", "ratio": 4.0, "label": "4:1"}],
        "closes": [
            {"date": "2026-11-09", "close": 30.0},
            {"date": "2026-11-10", "close": 15.0},
            {"date": "2026-11-13", "close": 15.1},
        ],
    }
    drafts = lifecycle.drafts_for(a, actions, events, start="2026-10-01", today="2026-11-13")
    kinds = {d["kind"]: d for d in drafts}
    assert kinds["dividend"]["cash_delta"] == {"USD": 5.0}
    assert kinds["dividend"]["idem_key"] == "div:listed:2026-10-15"
    assert kinds["split"]["payload"]["ratio"] == 4.0
    assert "like a 2:1 split" in kinds["freeze"]["payload"]["reason"]  # -50%, unreported: frozen


def test_listed_lifecycle_freezes_a_holding_with_no_price_for_five_sessions():
    a = asset("listed")
    events = [
        {
            "id": "e1",
            "kind": "buy",
            "at": "2026-10-01T15:00:00+00:00",
            "asset_key": a.key,
            "quantity_delta": 1,
            "price": 1,
        }
    ]
    actions = {"dividends": [], "splits": [], "closes": [{"date": "2026-11-02", "close": 9.0}]}
    [freeze] = lifecycle.drafts_for(a, actions, events, start="2026-10-01", today="2026-11-13")
    assert freeze["kind"] == "freeze" and "delisted" in freeze["payload"]["reason"]


def test_listed_lifecycle_never_pays_what_was_not_held():
    a = asset("listed")
    events = [
        {
            "id": "e1",
            "kind": "buy",
            "at": "2026-10-20T15:00:00+00:00",
            "asset_key": a.key,
            "quantity_delta": 10,
            "price": 100,
        }
    ]
    actions = {"dividends": [{"date": "2026-10-15", "amount": 0.5}], "splits": [], "closes": []}
    assert lifecycle.drafts_for(a, actions, events, start="2026-10-01", today="2026-11-13") == []


# ── fund_nav: mutual funds priced once a day ───────────────────────────────────────────────


def test_fund_nav_date_follows_the_cutoff():
    assert nav_date_for(datetime(2026, 11, 18, 20, 59, tzinfo=UTC)) == date(
        2026, 11, 18
    )  # 15:59 NY
    assert nav_date_for(datetime(2026, 11, 18, 21, 1, tzinfo=UTC)) == date(2026, 11, 19)  # 16:01 NY
    assert nav_date_for(SAT) == date(2026, 11, 23)  # weekend → Monday


@pytest.mark.parametrize(
    "o, navs, expect",
    [
        (
            {"side": "buy", "nav_date": "2026-11-18"},
            {"2026-11-17": 40.0},
            ("wait", "tonight's NAV"),
        ),
        ({"side": "buy", "nav_date": "2026-11-18"}, {"2026-11-18": 41.0}, ("fill", 41.0)),
        ({"side": "sell", "nav_date": "2026-11-18"}, {"2026-11-19": 42.0}, ("fill", 42.0)),
        ({"side": "buy", "nav_date": "2026-11-19"}, {"2026-11-18": 41.0}, ("wait", "NAV")),
    ],
)
def test_fund_nav(o, navs, expect):
    d = decide("fund_nav", o, q(basis="nav", navs=navs))
    _check(d, expect)
    if isinstance(d, Fill):
        assert d.basis == "nav"


def test_fund_nav_limit_has_no_meaning_it_fills_at_nav():
    d = decide(
        "fund_nav",
        {"side": "buy", "nav_date": "2026-11-18", "order_type": "limit", "limit_price": 1.0},
        q(navs={"2026-11-18": 41.0}),
    )
    assert isinstance(d, Fill) and d.price == 41.0


def test_fund_nav_lifecycle_distribution():
    a = PaperAsset(key="inst:f", kind="fund_nav", symbol="ACMFX", name="F", currency="USD")
    events = [
        {
            "id": "e1",
            "kind": "buy",
            "at": "2026-10-01T20:00:00+00:00",
            "asset_key": a.key,
            "quantity_delta": 100,
            "price": 40,
        }
    ]
    actions = {"dividends": [{"date": "2026-11-05", "amount": 0.25}], "splits": [], "closes": []}
    [d] = lifecycle.drafts_for(a, actions, events, start="2026-10-01", today="2026-11-13")
    assert d["kind"] == "distribution" and d["cash_delta"] == {"USD": 25.0}


# ── crypto: around the clock ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("now", [WED, SAT, datetime(2026, 11, 22, 3, 0, tzinfo=UTC)])
def test_crypto_fills_at_any_hour(now):
    d = decide("crypto", order(), q(50.0, now=now, state="OPEN"), now=now)
    assert isinstance(d, Fill) and d.price == pytest.approx(50.025)


@pytest.mark.parametrize(
    "o, expect",
    [
        (order("sell"), ("fill", 49.975)),
        (order(otype="limit", limit_price=49.0), ("wait", "reaches 49.00")),
        (order(otype="limit", limit_price=51.0), ("fill", 50.025)),
    ],
)
def test_crypto_orders(o, expect):
    _check(decide("crypto", o, q(50.0, now=SAT), now=SAT), expect)


def test_crypto_quantity_has_eight_decimals():
    assert adapter("crypto").quantity_decimals == 8


def test_crypto_and_others_have_no_lifecycle():
    for kind in ("crypto", "metal", "fx", "index", "manual"):
        assert kind not in lifecycle.HAS_LIFECYCLE


# ── metal: spot plus a dealer premium ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "o, now, expect",
    [
        (order(), WED, ("fill", 103.0)),  # +3% premium
        (order("sell"), WED, ("fill", 99.0)),  # −1% discount
        (order(), SAT, ("wait", "Market closed")),  # spot is shut on Saturday
        (order(), datetime(2026, 11, 22, 23, 30, tzinfo=UTC), ("fill", 103.0)),  # Sun 18:30 NY
        (order(otype="limit", limit_price=102.0), WED, ("fill", 102.0)),
        (order(otype="limit", limit_price=90.0), WED, ("wait", "reaches 90.00")),
    ],
)
def test_metal(o, now, expect):
    _check(decide("metal", o, q(100.0, now=now), now=now), expect)


def test_metal_values_by_the_assets_unit():
    assert adapter("metal").value(31.1, q(80.0)) == pytest.approx(2488.0)


# ── fx: currencies at the live rate plus a spread ──────────────────────────────────────────


@pytest.mark.parametrize(
    "now, expect",
    [
        (WED, ("fill", 1.08108)),  # 1.08 + 0.1%
        (SAT, ("wait", "Market closed")),
        (datetime(2026, 11, 22, 22, 30, tzinfo=UTC), ("fill", 1.08108)),  # Sun 17:30 NY
    ],
)
def test_fx(now, expect):
    a = PaperAsset(key="ccy:EUR", kind="fx", symbol="EUR", name="Euro", currency="EUR")
    _check(decide("fx", order(), q(1.08, now=now), now=now, a=a), expect)


def test_fx_ignores_order_types():
    d = decide("fx", order(otype="limit", limit_price=0.5), q(1.08))
    assert isinstance(d, Fill)


# ── index: a synthetic tracker ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "o, quote, expect",
    [
        (order(), q(5000.0, basis="synthetic"), ("fill", 5000.0)),  # the level, no fee
        (order(otype="limit", limit_price=4900.0), q(5000.0), ("wait", "reaches 4,900.00")),
        (order(otype="limit", limit_price=5100.0), q(5000.0), ("fill", 5000.0)),
        (order(), q(5000.0, state="CLOSED", is_open=False), ("wait", "Market closed")),
    ],
)
def test_index(o, quote, expect):
    d = decide("index", o, quote)
    _check(d, expect)
    if isinstance(d, Fill):
        assert d.basis == "synthetic"


# ── manual: your price ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "o, now, expect",
    [
        ({"side": "buy", "price": 250_000.0}, WED, ("fill", 250_000.0)),
        ({"side": "sell", "price": 260_000.0}, SAT, ("fill", 260_000.0)),  # any time
        ({"side": "buy"}, WED, ("reject", "Enter the price")),
    ],
)
def test_manual(o, now, expect):
    d = decide("manual", o, None, now=now)
    _check(d, expect)
    if isinstance(d, Fill):
        assert d.basis == "yours"


def test_manual_value_follows_a_proxy_since_the_mark():
    assert proxied_price(100.0, 50.0, 55.0) == pytest.approx(110.0)
    assert proxied_price(100.0, None, 55.0) == 100.0


def _check(d, expect):
    kind, value = expect
    if kind == "fill":
        assert isinstance(d, Fill), d
        assert d.price == pytest.approx(value, rel=1e-6)
    elif kind == "wait":
        assert isinstance(d, Wait), d
        assert value in d.reason
    else:
        assert isinstance(d, Reject), d
        assert value in d.reason
