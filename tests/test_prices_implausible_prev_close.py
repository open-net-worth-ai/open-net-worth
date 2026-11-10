"""Regression: a previous close wildly far from the price is a broken feed, not a move.

Owner-reproduced 2026-10-28: Yahoo's quote for JTKWY (Just Eat Takeaway's OTC ADR, near-dead
after the Prosus takeover) carried previousClose 6.00372e-19 beside a price of 4.76. It passed
the finite/positive checks, so the Peers card read +792841771435043586048.00%, the header the
same, and the stats strip "Prev Close $0". Beyond 50x either way the prior close is dropped
(unknown) while the price itself is kept. Pure + offline: yfinance is monkeypatched.
"""

from backend.services import prices
from backend.services.prices import _fetch_one, _plausible_prev_close, _quote_to_price

JUNK_PREV = 6.00372e-19


class _FakeTicker:
    def __init__(self, symbol, fast=None, info=None):
        self.fast_info = fast or {}
        self.info = info or {}


def test_junk_prev_close_is_dropped_but_the_price_kept():
    assert _quote_to_price({"regularMarketPrice": 4.76, "previousClose": JUNK_PREV}) == (
        4.76,
        None,
    )


def test_fetch_one_drops_junk_prev_close(monkeypatch):
    monkeypatch.setattr(
        prices.yf,
        "Ticker",
        lambda s: _FakeTicker(s, fast={"lastPrice": 4.76, "previousClose": JUNK_PREV}),
    )
    assert _fetch_one("JTKWY") == ("JTKWY", 4.76, None)


def test_quote_stats_hide_junk_prev_close(monkeypatch):
    prices._stats_cache.clear()
    info = {"regularMarketPrice": 4.76, "previousClose": JUNK_PREV, "open": 4.5}
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: _FakeTicker(s, info=info))
    stats = prices.fetch_quote_stats("JTKWY")
    assert "prev_close" not in stats
    assert stats["open"] == 4.5


def test_real_extreme_days_keep_their_prev_close():
    # A biotech failing a trial (-95%) and a squeezed microcap (+1,000%) are real moves.
    assert _quote_to_price({"regularMarketPrice": 1.0, "previousClose": 20.0}) == (1.0, 20.0)
    assert _quote_to_price({"regularMarketPrice": 11.0, "previousClose": 1.0}) == (11.0, 1.0)


def test_threshold_is_50x_either_way():
    assert _plausible_prev_close(50.0, 1.0) == 1.0
    assert _plausible_prev_close(1.0, 50.0) == 50.0
    assert _plausible_prev_close(50.01, 1.0) is None
    assert _plausible_prev_close(1.0, 50.01) is None
    # A pence/pounds mix-up is 100x.
    assert _plausible_prev_close(1.20, 120.0) is None
    # Nothing to compare against: left as it was.
    assert _plausible_prev_close(None, 3.0) == 3.0
    assert _plausible_prev_close(3.0, None) is None
