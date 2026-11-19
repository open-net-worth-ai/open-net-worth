"""A paper order allowed outside the regular session fills against the pre/post-market print,
timed by that print — not the last regular close, whose age made every pre-market order wait
(owner, 2026-11-16: eight buys sat at "No fresh price since 21:00" with extended hours on)."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services import prices
from backend.services.paper_trading import quotes
from backend.services.paper_trading.model import PaperAsset

NOW = datetime(2026, 11, 16, 13, 40, 30, tzinfo=timezone.utc)  # 08:40 New York, pre-market
ACME = PaperAsset(key="inst:acme", kind="listed", symbol="ACME", name="Acme", currency="USD")


def _snap(state: str, extended: dict | None) -> dict:
    snap = {
        "price": 100.0,
        "bid": 99.9,
        "ask": 100.1,
        "currency": "USD",
        "market_state": state,
        "quote_time": "2026-11-13T21:00:00+00:00",  # Friday's regular close
    }
    if extended:
        snap["extended"] = extended
    return snap


def _quote(monkeypatch, state: str, extended: dict | None, is_open: bool = True):
    monkeypatch.setattr(prices, "fetch_quote_snapshots", lambda s: {"ACME": _snap(state, extended)})
    monkeypatch.setattr(
        prices, "market_status", lambda s, **k: {"ACME": {"state": state, "is_open": is_open}}
    )
    return quotes._exchange_quotes([ACME], NOW)[ACME.key]


PRE = {"session": "pre", "price": 97.5, "as_of": "2026-11-16T13:40:11+00:00"}


def test_pre_market_quote_is_the_pre_market_print_at_its_own_time(monkeypatch):
    q = _quote(monkeypatch, "PRE", PRE)
    assert q.last == 97.5
    assert q.as_of == datetime(2026, 11, 16, 13, 40, 11, tzinfo=timezone.utc)
    # The regular session's bid/ask are not the pre-market book.
    assert q.bid is None and q.ask is None


def test_regular_session_ignores_a_leftover_extended_print(monkeypatch):
    q = _quote(monkeypatch, "REGULAR", PRE)
    assert q.last == 100.0 and q.bid == 99.9 and q.ask == 100.1
    assert q.as_of == datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)


def test_no_print_keeps_the_regular_close_and_its_age(monkeypatch):
    q = _quote(monkeypatch, "PRE", None)
    assert q.last == 100.0
    assert q.as_of == datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)


def test_an_untimed_print_is_never_treated_as_fresh(monkeypatch):
    q = _quote(monkeypatch, "POST", {"session": "post", "price": 101.0, "as_of": None})
    assert q.last == 100.0


def test_the_snapshot_carries_the_extended_print_and_drops_a_phantom_copy():
    base = {
        "regularMarketPrice": 100.0,
        "previousClose": 99.0,
        "regularMarketTime": 1763067600,
        "marketState": "PRE",
    }
    real = prices._snapshot_from_quote(
        {**base, "preMarketPrice": 97.5, "preMarketTime": 1763300411}
    )
    assert real["extended"]["price"] == 97.5
    phantom = prices._snapshot_from_quote(
        {**base, "preMarketPrice": 100.0, "preMarketTime": 1763300411}
    )
    assert "extended" not in phantom
