"""Regression: a London listing quoted in pence is valued in pounds.

Reported 2026-11-22: Yahoo quotes most LSE shares in ``GBp`` (pence); ``.upper()`` turned that
into ``GBP``, so a 26.5p share was stored and valued as £26.50 — 100x too high. Every Yahoo
read now converts a minor-unit quote (GBp/GBX, ZAc, ILA) to its major currency where it
enters the app. A listing Yahoo already quotes in pounds (``GBP``) is left alone — the
suffix ``.L`` decides nothing. Offline: yfinance is faked; ACME.L is a synthetic ticker.
"""

from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from backend.core import quote_units
from backend.services import prices, yahoo_units


class _FakeTicker:
    def __init__(self, fast=None, info=None, frame=None, currency=None, dividends=None):
        self.fast_info = fast or {}
        self.info = info or {}
        self._frame = frame
        self.history_metadata = {"currency": currency} if currency else {}
        self.dividends = dividends

    def history(self, **_kwargs):
        return self._frame if self._frame is not None else pd.DataFrame()


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.to_datetime([f"2026-11-{d:02d}" for d in range(16, 16 + len(closes))], utc=True)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1000.0] * len(closes),
            "Stock Splits": [0.0] * len(closes),
        },
        index=idx,
    )


# ── core helper ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, major, factor",
    [
        ("GBp", "GBP", 0.01),
        ("GBX", "GBP", 0.01),
        ("ZAc", "ZAR", 0.01),
        ("ILA", "ILS", 0.01),
        ("GBP", "GBP", 1.0),
        ("cad", "CAD", 1.0),
        ("", "", 1.0),
        (None, "", 1.0),
    ],
)
def test_minor_unit_codes_map_to_their_major_currency(raw, major, factor):
    assert quote_units.major_currency(raw) == major
    assert quote_units.to_major_factor(raw) == factor
    assert quote_units.is_minor_unit(raw) == (factor != 1.0)


def test_quote_to_major_scales_prices_but_not_pound_denominated_fields():
    q = {
        "currency": "GBp",
        "regularMarketPrice": 26.5,
        "previousClose": 25.0,
        "fiftyTwoWeekHigh": 40.0,
        "regularMarketChangePercent": 6.0,
        "marketCap": 50_000_000,
        "dividendRate": 0.04,
        "regularMarketVolume": 1000,
    }
    out = yahoo_units.quote_to_major(q)
    assert out["currency"] == "GBP"
    assert out["regularMarketPrice"] == pytest.approx(0.265)
    assert out["previousClose"] == pytest.approx(0.25)
    assert out["fiftyTwoWeekHigh"] == pytest.approx(0.40)
    for untouched in (
        "regularMarketChangePercent",
        "marketCap",
        "dividendRate",
        "regularMarketVolume",
    ):
        assert out[untouched] == q[untouched]


def test_quote_already_in_pounds_is_returned_unchanged():
    q = {"currency": "GBP", "regularMarketPrice": 102.23}
    assert yahoo_units.quote_to_major(q) is q


# ── live quote paths ─────────────────────────────────────────────────────────


def test_fetch_one_converts_pence_to_pounds(monkeypatch):
    fast = {"currency": "GBp", "lastPrice": 26.5, "previousClose": 25.0}
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: _FakeTicker(fast=fast))
    assert prices._fetch_one("ACME.L") == ("ACME.L", 0.265, 0.25)


def test_fetch_one_leaves_a_pound_quoted_listing_alone(monkeypatch):
    fast = {"currency": "GBP", "lastPrice": 102.23, "previousClose": 105.71}
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: _FakeTicker(fast=fast))
    assert prices._fetch_one("ACMF.L") == ("ACMF.L", 102.23, 105.71)


def test_fetch_one_history_fallback_converts_pence(monkeypatch):
    class _NoFast(_FakeTicker):
        @property
        def fast_info(self):
            raise RuntimeError("fast_info down")

        @fast_info.setter
        def fast_info(self, _value):
            pass

    monkeypatch.setattr(
        prices.yf, "Ticker", lambda s: _NoFast(frame=_frame([25.0, 26.5]), currency="GBp")
    )
    assert prices._fetch_one("ACME.L") == ("ACME.L", 0.265, 0.25)


def test_batch_quote_converts_pence_to_pounds(monkeypatch):
    payload = {
        "quoteResponse": {
            "result": [
                {"symbol": "ACME.L", "currency": "GBp", "regularMarketPrice": 26.5},
                {"symbol": "ACMF.L", "currency": "GBP", "regularMarketPrice": 102.23},
            ]
        }
    }

    class _Resp:
        def json(self):
            return payload

    class _YfData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, _url, **_kw):
            return _Resp()

    fake_data = types.SimpleNamespace(YfData=_YfData)
    monkeypatch.setitem(sys.modules, "yfinance.data", fake_data)
    monkeypatch.setattr(sys.modules["yfinance"], "data", fake_data, raising=False)
    out = prices._yf_batch_quote(["ACME.L", "ACMF.L"])
    assert out["ACME.L"]["regularMarketPrice"] == pytest.approx(0.265)
    assert out["ACME.L"]["currency"] == "GBP"
    assert out["ACMF.L"]["regularMarketPrice"] == 102.23


# ── history paths ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ohlcv", [True, False])
def test_history_converts_pence_bars(monkeypatch, ohlcv):
    monkeypatch.setattr(
        prices.yf, "Ticker", lambda s: _FakeTicker(frame=_frame([25.0, 26.5]), currency="GBp")
    )
    points = prices._yf_history("ACME.L", "1M", ohlcv=ohlcv)
    assert [p["price"] for p in points] == [0.25, 0.265]
    if ohlcv:
        assert points[-1]["high"] == pytest.approx(0.275)
        assert points[-1]["volume"] == 1000.0


def test_history_of_a_pound_quoted_listing_is_unchanged(monkeypatch):
    monkeypatch.setattr(
        prices.yf, "Ticker", lambda s: _FakeTicker(frame=_frame([100.0, 102.0]), currency="GBP")
    )
    assert [p["price"] for p in prices._yf_history("ACMF.L", "1M", ohlcv=False)] == [100.0, 102.0]


# ── .info, dividends, corporate actions, instrument resolution ───────────────


def test_quote_stats_and_analyst_targets_convert_pence(monkeypatch):
    prices._stats_cache.clear()
    prices._analyst_cache.clear()
    info = {
        "currency": "GBp",
        "regularMarketPrice": 26.5,
        "previousClose": 25.0,
        "open": 25.5,
        "fiftyTwoWeekHigh": 40.0,
        "marketCap": 50_000_000,
        "targetMeanPrice": 30.0,
        "numberOfAnalystOpinions": 3,
        "recommendationKey": "buy",
    }
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: _FakeTicker(info=info))
    stats = prices.fetch_quote_stats("ACME.L")
    assert stats["prev_close"] == pytest.approx(0.25)
    assert stats["open"] == pytest.approx(0.255)
    assert stats["week52_high"] == pytest.approx(0.40)
    assert stats["market_cap"] == 50_000_000
    analyst = prices.fetch_analyst("ACME.L")
    assert analyst["target_mean"] == pytest.approx(0.30)
    assert analyst["current"] == pytest.approx(0.265)


def test_dividend_meta_converts_pence_dividend_events(monkeypatch):
    dividends = pd.Series(
        [1.98, 1.93], index=pd.to_datetime(["2026-06-04", "2026-11-19"], utc=True)
    )
    fake = _FakeTicker(info={"currency": "GBp", "dividendRate": 0.04}, dividends=dividends)
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: fake)
    meta = prices._fetch_dividend_meta("ACME.L")
    assert meta["dividend_rate_annual"] == 0.04  # Yahoo already reports this in pounds
    assert meta["last_dividend_amount"] == pytest.approx(0.0193)


def test_corporate_actions_convert_pence(monkeypatch):
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "GBp", "regularMarketPrice": 26.5},
                    "timestamp": [1795000000],
                    "indicators": {"quote": [{"close": [26.5], "volume": [10]}]},
                    "events": {"dividends": {"1795000000": {"date": 1795000000, "amount": 1.5}}},
                }
            ]
        }
    }

    class _Resp:
        def json(self):
            return payload

    class _YfData:
        def get(self, _url, **_kw):
            return _Resp()

    fake_data = types.SimpleNamespace(YfData=_YfData)
    monkeypatch.setitem(sys.modules, "yfinance.data", fake_data)
    monkeypatch.setattr(sys.modules["yfinance"], "data", fake_data, raising=False)
    monkeypatch.setattr(prices, "get_settings", lambda: types.SimpleNamespace(onw_qa_mode=False))
    prices._actions_cache.clear()
    actions = prices.fetch_corporate_actions("ACME.L")
    assert actions["closes"][0]["close"] == pytest.approx(0.265)
    assert actions["dividends"][0]["amount"] == pytest.approx(0.015)


def test_fetch_name_caches_the_major_currency(monkeypatch):
    monkeypatch.setattr(
        prices.yf, "Ticker", lambda s: _FakeTicker(info={"currency": "GBp", "longName": "Acme"})
    )
    monkeypatch.setattr(prices, "_CURRENCY_CACHE", {})
    monkeypatch.setattr(prices, "_NAMES_CACHE", {})
    prices._fetch_name("ACME.L")
    assert prices._CURRENCY_CACHE["ACME.L"] == "GBP"


@pytest.mark.parametrize("raw, major", [("GBp", "GBP"), ("ILA", "ILS"), ("ZAc", "ZAR")])
def test_resolve_yahoo_mints_a_minor_unit_listing_in_its_major_currency(monkeypatch, raw, major):
    import backend.services.instruments as inst_mod

    class _FakeYF:
        class Ticker:
            def __init__(self, _sym):
                self.fast_info = {"currency": raw, "lastPrice": 26.5, "quoteType": "EQUITY"}
                self.isin = ""

    monkeypatch.setitem(sys.modules, "yfinance", _FakeYF())
    monkeypatch.setattr(prices, "_fetch_name", lambda q: (q, "Acme plc", "LSE"))
    resolved = inst_mod._resolve_yahoo("ACME.L")
    assert resolved["quote_currency"] == major
