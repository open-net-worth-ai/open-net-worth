"""
Phase-4 of the currency-genericity cleanup: the FX rate table pivots on the
user's base_currency (not a hardcoded SGD).

Asserts:
  - load_fx_cache() anchors rates[base] = 1.0 for any base, and falls back to {base: 1.0};
  - fetch_live() requests yfinance pairs as "<ccy><base>=X" and seeds fx = {base: 1.0},
    excluding the base itself (the pivot is 1.0, never fetched);
  - changing base_currency via PUT /api/profile clears the cached (stale-pivot) FX file.

All offline: yfinance + base_currency are monkeypatched.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def prices(monkeypatch, tmp_path):
    import backend.services.fx_cache as fx_cache_mod
    import backend.services.prices as prices_mod

    # FX-cache I/O moved to services.fx_cache (P1-A5); prices.load_fx_cache re-exports it.
    # Point the cache at a temp file (on the real I/O module) so we never touch live data.
    fx_path = tmp_path / "fx_cache.json"
    monkeypatch.setattr(fx_cache_mod, "_FX_CACHE_FILE", fx_path)
    # Expose the temp path on the prices module so tests can seed the cache via
    # `prices._FX_CACHE_FILE` (auto-removed by monkeypatch after each test).
    monkeypatch.setattr(prices_mod, "_FX_CACHE_FILE", fx_path, raising=False)
    # Keep these offline: disable the batched v7 quote (which would hit Yahoo) so every
    # rate flows through the monkeypatched per-ticker `_fetch_one` fallback these tests assert on.
    monkeypatch.setattr(prices_mod, "_yf_batch_quote", lambda syms: {}, raising=False)
    return prices_mod


def _set_base(monkeypatch, prices_mod, base: str):
    # Base currency reads from core.fx.base_currency, bound into two namespaces:
    #   services.fx_cache (load_fx_cache anchor) and services.prices._base_currency (fetch_live pivot).
    # Patch both module-bound names so every path sees the requested pivot.
    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "base_currency", lambda: base)
    monkeypatch.setattr(prices_mod, "_base_currency", lambda: base)


def test_load_fx_cache_anchors_base_fallback(prices, monkeypatch):
    _set_base(monkeypatch, prices, "EUR")
    # No file on disk → minimal fallback anchored on the base.
    assert prices.load_fx_cache() == {"EUR": 1.0}


def test_load_fx_cache_overrides_base_to_one(prices, monkeypatch):
    _set_base(monkeypatch, prices, "USD")
    prices._FX_CACHE_FILE.write_text(json.dumps({"rates": {"USD": 1.23, "VND": 0.00004}}))
    rates = prices.load_fx_cache()
    assert rates["USD"] == 1.0  # base anchored to 1.0 regardless of stored value
    assert rates["VND"] == 0.00004  # other rates preserved


def test_fetch_live_pivots_on_base(prices, monkeypatch):
    _set_base(monkeypatch, prices, "EUR")
    requested: list[str] = []

    def fake_fetch_one(symbol):
        requested.append(symbol)
        return (symbol, 1.1, 1.0)  # any non-None rate

    monkeypatch.setattr(prices, "_fetch_one", fake_fetch_one)
    monkeypatch.setattr(prices, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))

    result = prices.fetch_live([], ["USD", "VND", "EUR"])

    # Base is the pivot: seeded at 1.0, never requested; others quoted against it.
    assert result["base_currency"] == "EUR"
    assert result["fx"]["EUR"] == 1.0
    assert "USDEUR=X" in requested and "VNDEUR=X" in requested
    assert "EUREUR=X" not in requested


def test_fetch_live_falls_back_to_cached_rates_when_offline(prices, monkeypatch):
    """Regression (UI-audit HIGH #2): a fetch that can't reach the FX provider (offline /
    rate-limited) must FALL BACK to the last-known cached rates, not drop them — otherwise the
    response is fx={base:1.0} only and every non-base holding silently converts 1:1
    (mis-valued net worth + allocation). The file cache was already merge-preserved; the bug
    was that fetch_live rebuilt the RESPONSE fx from scratch."""
    _set_base(monkeypatch, prices, "SGD")
    # A prior successful fetch left these rates on disk (anchored on the SGD pivot).
    prices._FX_CACHE_FILE.write_text(json.dumps({"rates": {"SGD": 1.0, "USD": 1.29, "EUR": 1.48}}))
    # OFFLINE: every yfinance lookup returns no price/rate.
    monkeypatch.setattr(prices, "_fetch_one", lambda symbol: (symbol, None, None))
    monkeypatch.setattr(prices, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))

    result = prices.fetch_live(["AAPL"], ["USD", "EUR", "SGD"])

    fx = result["fx"]
    assert fx["SGD"] == 1.0  # the pivot
    assert fx["USD"] == 1.29  # survived from cache, not dropped
    assert fx["EUR"] == 1.48


def test_fetch_live_live_rate_overrides_cached(prices, monkeypatch):
    """A fresh live rate wins over the cached one (the cache is only the fallback)."""
    _set_base(monkeypatch, prices, "SGD")
    prices._FX_CACHE_FILE.write_text(json.dumps({"rates": {"SGD": 1.0, "USD": 1.29}}))

    # Quote BOTH legs coherently. `fetch_live` now also fetches the reciprocal pair to
    # sanity-check the direct one (the 2026-07-27 Yahoo VND incident), so a stub returning the
    # same number for every symbol would describe an impossible market (1.40 × 1.40 = 1.96)
    # and the guard would correctly reject it.
    def fake_fetch_one(symbol):
        return (symbol, 1 / 1.40, 1 / 1.39) if symbol.startswith("SGD") else (symbol, 1.40, 1.39)

    monkeypatch.setattr(prices, "_fetch_one", fake_fetch_one)
    monkeypatch.setattr(prices, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))

    result = prices.fetch_live([], ["USD", "SGD"])

    assert result["fx"]["USD"] == 1.40  # live overrides the stale 1.29


def test_base_currency_holding_always_converts_one_to_one(prices, monkeypatch):
    """A holding quoted IN the base currency must ALWAYS have a base value (rate 1.0) — even
    with an EMPTY fx cache and no live rates. to_base short-circuits currency==base before any
    rate lookup, so the base-currency common case can never be nulled/dropped by a missing FX
    rate (UI-audit HIGH #2)."""
    from backend.core.fx import to_base

    _set_base(monkeypatch, prices, "SGD")
    # Empty cache, fully offline → fx is just the pivot. The cache is a FILE any live
    # `save_fx_cache` caller may write, and in CI (xdist) the conftest's synthetic table
    # intermittently appeared in this test's temp file — most likely written by a thread an
    # earlier test left running — so the empty cache is held in memory here, not on disk.
    monkeypatch.setattr(prices, "load_fx_cache", lambda: {})
    monkeypatch.setattr(prices, "save_fx_cache", lambda fx: None)
    monkeypatch.setattr(prices, "_fetch_one", lambda symbol: (symbol, None, None))
    monkeypatch.setattr(prices, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))
    fx = prices.fetch_live([], ["SGD"])["fx"]

    assert fx == {"SGD": 1.0}
    # A 1,234.56 SGD holding under an SGD base values to itself, with no rate needed.
    assert to_base(1234.56, "SGD", "SGD", rates=fx) == 1234.56


def test_put_profile_clears_fx_cache_on_base_change(monkeypatch, tmp_path):
    import backend.api.profile as profile_mod
    import backend.paths as paths
    from backend.db.profile_store import ProfileStore

    # profile is DB-backed now (Wave 5, JSON->DB) — seed directly through ProfileStore.
    profile_store = ProfileStore()
    profile_store.set_raw({"base_currency": "SGD"})
    fx_path = tmp_path / "fx_cache.json"
    fx_path.write_text(json.dumps({"rates": {"SGD": 1.0, "USD": 0.74}}))

    monkeypatch.setattr(paths, "FX_CACHE_JSON", fx_path)

    profile_mod.put_profile(profile_mod.ProfileBody(base_currency="USD"))

    assert not fx_path.exists()  # stale pivot cleared
    assert profile_store.get_raw()["base_currency"] == "USD"
