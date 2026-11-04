"""Tests for the direct single-provider price tools (2026-08-22, history pair 2026-08-23).

``yahoo_get_ticker_prices`` / ``coingecko_get_crypto_prices`` are deliberately narrower
than ``get_live_prices``: ONE named provider, no cross-provider fallback substitution, and
— unlike ``get_live_prices`` — no ``price_history`` recording (an ad-hoc direct lookup may
use a different provider than an instrument's own configured ``price_source``; writing it
in would splice a foreign provider's tick into that instrument's canonical series).

``yahoo_get_price_history`` / ``coingecko_get_price_history`` are the same contract for a
price CHART over time instead of a spot quote — both wrap ``services.prices.
fetch_history_result`` (already the read path behind asset-detail charting: it caches in an
in-memory TTL dict only, never the durable ``price_history`` table). The CoinGecko tool
additionally resolves its free-text input through ``_resolve_coingecko_coin``, the same
helper ``fetch_coingecko_quotes`` uses, and reports an honest ``state="unavailable"`` for an
identifier that doesn't resolve rather than guessing a different coin.

Two layers, mirroring the existing ``get_live_prices``/``fetch_live`` split:
  - dispatch tests drive ``execute_tool`` and assert the (json_text, proposed_change) shape
  - service tests drive ``fetch_yahoo_quotes``/``fetch_coingecko_quotes`` directly, with the
    underlying Yahoo/CoinGecko network calls monkeypatched out (offline, no `integration` mark)
"""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.services.prices as prices_module
from backend.ai.tools import execute_tool
from backend.db.price_history_store import PriceHistoryStore


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── dispatch: yahoo_get_ticker_prices ───────────────────────────────────────────


def test_yahoo_get_ticker_prices_dispatch(monkeypatch):
    captured = {}

    def _stub(tickers):
        captured["tickers"] = tickers
        return {
            "prices": {"MSFT": 123.45},
            "prev_prices": {"MSFT": 120.0},
            "providers": {"MSFT": "yahoo"},
            "states": {"MSFT": "live"},
            "as_of": {"MSFT": "2026-08-22T00:00:00+00:00"},
        }

    import backend.ai.tools as tools_root

    monkeypatch.setattr(tools_root, "fetch_yahoo_quotes", _stub)
    result, proposed = call("yahoo_get_ticker_prices", tickers=["MSFT"])
    assert captured["tickers"] == ["MSFT"]
    assert result["prices"] == {"MSFT": 123.45}
    assert result["states"] == {"MSFT": "live"}
    assert proposed is None


def test_yahoo_get_ticker_prices_in_registered_set():
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "yahoo_get_ticker_prices" in names
    assert "get_live_prices" in names  # untouched, still the general path


# ── dispatch: coingecko_get_crypto_prices ───────────────────────────────────────


def test_coingecko_get_crypto_prices_dispatch_defaults(monkeypatch):
    captured = {}

    def _stub(coins, vs_currency, *, include_facts=False):
        captured["args"] = (coins, vs_currency, include_facts)
        return {
            "prices": {"BTC-USD": 65000.0},
            "prev_prices": {},
            "providers": {"BTC-USD": "coingecko"},
            "states": {"BTC-USD": "live"},
            "as_of": {"BTC-USD": "2026-08-22T00:00:00+00:00"},
            "resolved_ids": {"BTC-USD": "bitcoin"},
        }

    import backend.ai.tools as tools_root

    monkeypatch.setattr(tools_root, "fetch_coingecko_quotes", _stub)
    result, proposed = call("coingecko_get_crypto_prices", coins=["BTC-USD"])
    # vs_currency/include_facts default when omitted from input
    assert captured["args"] == (["BTC-USD"], "usd", False)
    assert result["resolved_ids"] == {"BTC-USD": "bitcoin"}
    assert proposed is None


def test_coingecko_get_crypto_prices_dispatch_explicit_args(monkeypatch):
    captured = {}

    def _stub(coins, vs_currency, *, include_facts=False):
        captured["args"] = (coins, vs_currency, include_facts)
        return {"prices": {}, "prev_prices": {}, "providers": {}, "states": {}, "as_of": {}}

    import backend.ai.tools as tools_root

    monkeypatch.setattr(tools_root, "fetch_coingecko_quotes", _stub)
    call("coingecko_get_crypto_prices", coins=["solana"], vs_currency="eur", include_facts=True)
    assert captured["args"] == (["solana"], "eur", True)


def test_coingecko_get_crypto_prices_in_registered_set():
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "coingecko_get_crypto_prices" in names


# ── service: fetch_yahoo_quotes ─────────────────────────────────────────────────


def test_fetch_yahoo_quotes_empty_input_returns_empty_shape():
    result = prices_module.fetch_yahoo_quotes([])
    assert result == {
        "prices": {},
        "prev_prices": {},
        "providers": {},
        "states": {},
        "as_of": {},
        "currencies": {},
        "timestamp_verified": {},
        "extended": {},
    }


def test_fetch_yahoo_quotes_batch_hit(monkeypatch):
    monkeypatch.setattr(
        prices_module,
        "_yf_batch_quote",
        lambda symbols: {
            "MSFT": {
                "regularMarketPrice": 420.5,
                "regularMarketPreviousClose": 415.0,
                "regularMarketTime": 1_700_000_000,
            }
        },
    )
    monkeypatch.setattr(prices_module, "_cache_market_from_quotes", lambda batch: None)

    def _boom(sym):
        raise AssertionError("_fetch_one should not run when the batch already resolved it")

    monkeypatch.setattr(prices_module, "_fetch_one", _boom)

    result = prices_module.fetch_yahoo_quotes(["MSFT"])
    assert result["prices"]["MSFT"] == pytest.approx(420.5)
    assert result["prev_prices"]["MSFT"] == pytest.approx(415.0)
    assert result["providers"]["MSFT"] == "yahoo"
    assert result["states"]["MSFT"] == "live"
    assert result["as_of"]["MSFT"] is not None


def test_fetch_yahoo_quotes_falls_back_to_fetch_one_on_batch_miss(monkeypatch):
    monkeypatch.setattr(prices_module, "_yf_batch_quote", lambda symbols: {})
    monkeypatch.setattr(prices_module, "_fetch_one", lambda sym: (sym, 99.0, 98.0))

    result = prices_module.fetch_yahoo_quotes(["ACME"])
    assert result["prices"]["ACME"] == 99.0
    assert result["states"]["ACME"] == "live"


def test_fetch_yahoo_quotes_unavailable_when_every_path_fails(monkeypatch):
    monkeypatch.setattr(prices_module, "_yf_batch_quote", lambda symbols: {})
    monkeypatch.setattr(prices_module, "_fetch_one", lambda sym: (sym, None, None))

    result = prices_module.fetch_yahoo_quotes(["NOPE"])
    assert "NOPE" not in result["prices"]
    assert result["states"]["NOPE"] == "unavailable"
    assert result["providers"]["NOPE"] is None


def test_fetch_yahoo_quotes_never_records_into_price_history(monkeypatch):
    """Direct-provider lookups must not write price_history — see the module note above
    `fetch_yahoo_quotes` in services/prices.py. Fails loudly if that ever changes."""

    def _boom(*a, **k):
        raise AssertionError("fetch_yahoo_quotes must never write price_history")

    monkeypatch.setattr(PriceHistoryStore, "record_many", _boom)
    monkeypatch.setattr(
        prices_module,
        "_yf_batch_quote",
        lambda symbols: {
            "MSFT": {
                "regularMarketPrice": 420.5,
                "regularMarketPreviousClose": 415.0,
                "regularMarketTime": 1_700_000_000,
            }
        },
    )
    monkeypatch.setattr(prices_module, "_cache_market_from_quotes", lambda batch: None)
    prices_module.fetch_yahoo_quotes(["MSFT"])  # would raise if it ever recorded


# ── service: _resolve_coingecko_coin / fetch_coingecko_quotes ──────────────────


def test_resolve_coingecko_coin_known_ticker():
    coin_id, display = prices_module._resolve_coingecko_coin("BTC-USD")
    assert coin_id == "bitcoin"
    assert display == "BTC"


def test_resolve_coingecko_coin_search_fallback(monkeypatch):
    monkeypatch.setattr(
        prices_module,
        "search_coin",
        lambda q: {"id": "solana", "symbol": "SOL", "name": "Solana"},
    )
    coin_id, display = prices_module._resolve_coingecko_coin("solana")
    assert coin_id == "solana"
    assert display == "SOL"


def test_resolve_coingecko_coin_unresolved(monkeypatch):
    monkeypatch.setattr(prices_module, "search_coin", lambda q: {})
    coin_id, display = prices_module._resolve_coingecko_coin("not-a-real-coin")
    assert coin_id is None


def test_fetch_coingecko_quotes_empty_input_returns_empty_shape():
    result = prices_module.fetch_coingecko_quotes([])
    assert result == {
        "prices": {},
        "prev_prices": {},
        "providers": {},
        "states": {},
        "as_of": {},
        "resolved_ids": {},
    }


def test_fetch_coingecko_quotes_resolves_and_prices(monkeypatch):
    monkeypatch.setattr(
        prices_module,
        "_fetch_coingecko_by_id",
        lambda pairs, currency: (
            {"BTC-USD": 65000.0},
            {"BTC-USD": 64000.0},
            {"BTC-USD": "2026-08-22T00:00:00+00:00"},
        ),
    )
    result = prices_module.fetch_coingecko_quotes(["BTC-USD"])
    assert result["prices"]["BTC-USD"] == 65000.0
    assert result["providers"]["BTC-USD"] == "coingecko"
    assert result["states"]["BTC-USD"] == "live"
    assert result["resolved_ids"]["BTC-USD"] == "bitcoin"


def test_fetch_coingecko_quotes_unresolved_reports_unavailable(monkeypatch):
    monkeypatch.setattr(prices_module, "search_coin", lambda q: {})
    result = prices_module.fetch_coingecko_quotes(["not-a-real-coin"])
    assert result["states"]["not-a-real-coin"] == "unavailable"
    assert "not-a-real-coin" not in result["resolved_ids"]


def test_fetch_coingecko_quotes_include_facts(monkeypatch):
    monkeypatch.setattr(
        prices_module,
        "_fetch_coingecko_by_id",
        lambda pairs, currency: (
            {"BTC-USD": 65000.0},
            {},
            {"BTC-USD": "2026-08-22T00:00:00+00:00"},
        ),
    )
    monkeypatch.setattr(
        prices_module,
        "fetch_coin_facts",
        lambda coin_id: {"market_cap": 1_000_000_000, "market_cap_rank": 1},
    )
    result = prices_module.fetch_coingecko_quotes(["BTC-USD"], include_facts=True)
    assert result["facts"]["BTC-USD"]["market_cap_rank"] == 1


def test_fetch_coingecko_quotes_omits_facts_key_when_not_requested(monkeypatch):
    monkeypatch.setattr(
        prices_module,
        "_fetch_coingecko_by_id",
        lambda pairs, currency: (
            {"BTC-USD": 65000.0},
            {},
            {"BTC-USD": "2026-08-22T00:00:00+00:00"},
        ),
    )
    result = prices_module.fetch_coingecko_quotes(["BTC-USD"])
    assert "facts" not in result


def test_fetch_coingecko_quotes_never_records_into_price_history(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("fetch_coingecko_quotes must never write price_history")

    monkeypatch.setattr(PriceHistoryStore, "record_many", _boom)
    monkeypatch.setattr(
        prices_module,
        "_fetch_coingecko_by_id",
        lambda pairs, currency: (
            {"BTC-USD": 65000.0},
            {},
            {"BTC-USD": "2026-08-22T00:00:00+00:00"},
        ),
    )
    prices_module.fetch_coingecko_quotes(["BTC-USD"])  # would raise if it ever recorded


# ── dispatch: yahoo_get_price_history ───────────────────────────────────────────

_SAMPLE_HISTORY_RESULT = {
    "provider": "yahoo",
    "state": "live",
    "as_of": "2026-08-23",
    "degraded_reason": None,
    "points": [{"date": "2026-08-20", "close": 123.0}, {"date": "2026-08-23", "close": 125.5}],
}


def test_yahoo_get_price_history_dispatch(monkeypatch):
    captured = {}

    def _stub(price_source_id, timeframe, price_source="yahoo", vs="usd"):
        captured["args"] = (price_source_id, timeframe, price_source)
        return dict(_SAMPLE_HISTORY_RESULT)

    import backend.ai.tools as tools_root

    monkeypatch.setattr(tools_root, "fetch_history_result", _stub)
    result, proposed = call("yahoo_get_price_history", ticker="MSFT", timeframe="1M")
    # No CoinGecko fallback, no currency conversion — the raw ticker rides straight through.
    assert captured["args"] == ("MSFT", "1M", "yahoo")
    # The tool's output is exactly fetch_history_result's shape — no reshaping for Yahoo.
    assert result == _SAMPLE_HISTORY_RESULT
    assert proposed is None


def test_yahoo_get_price_history_unavailable_ticker_is_honest_not_a_crash(monkeypatch):
    def _stub(price_source_id, timeframe, price_source="yahoo", vs="usd"):
        return {
            "provider": None,
            "state": "unavailable",
            "as_of": None,
            "degraded_reason": "upstream_unavailable",
            "points": [],
        }

    import backend.ai.tools as tools_root

    monkeypatch.setattr(tools_root, "fetch_history_result", _stub)
    result, proposed = call("yahoo_get_price_history", ticker="NOPE", timeframe="1Y")
    assert result["state"] == "unavailable"
    assert result["points"] == []
    assert proposed is None


def test_yahoo_get_price_history_in_registered_set():
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "yahoo_get_price_history" in names


# ── dispatch: coingecko_get_price_history ───────────────────────────────────────


def test_coingecko_get_price_history_dispatch_resolves_known_ticker(monkeypatch):
    """'BTC-USD' resolves through the REAL known-ticker map (not mocked) — only the
    downstream fetch is stubbed, so this exercises the actual resolution path."""
    captured = {}

    def _stub(price_source_id, timeframe, price_source="yahoo", vs="usd"):
        captured["args"] = (price_source_id, timeframe, price_source, vs)
        return {
            "provider": "coingecko",
            "state": "live",
            "as_of": "2026-08-23",
            "degraded_reason": None,
            "points": [{"date": "2026-08-23", "close": 65000.0}],
        }

    import backend.ai.tools as tools_root

    monkeypatch.setattr(tools_root, "fetch_history_result", _stub)
    result, proposed = call("coingecko_get_price_history", coin="BTC-USD", timeframe="1Y")
    assert captured["args"] == ("bitcoin", "1Y", "coingecko", "usd")
    assert result["requested"] == "BTC-USD"
    assert result["resolved_id"] == "bitcoin"
    assert result["provider"] == "coingecko"
    assert result["points"] == [{"date": "2026-08-23", "close": 65000.0}]
    assert proposed is None


def test_coingecko_get_price_history_resolves_free_text_via_search(monkeypatch):
    """A name/symbol not in the known-ticker map resolves via CoinGecko's own /search —
    the same fallback ``fetch_coingecko_quotes`` uses. ``vs_currency`` also rides through."""
    captured = {}

    def _search_stub(q):
        captured["searched"] = q
        return {"id": "solana", "symbol": "SOL", "name": "Solana"}

    def _history_stub(price_source_id, timeframe, price_source="yahoo", vs="usd"):
        captured["history_args"] = (price_source_id, timeframe, price_source, vs)
        return {
            "provider": "coingecko",
            "state": "live",
            "as_of": "2026-08-23",
            "degraded_reason": None,
            "points": [{"date": "2026-08-23", "close": 150.0}],
        }

    import backend.ai.tools as tools_root

    monkeypatch.setattr(prices_module, "search_coin", _search_stub)
    monkeypatch.setattr(tools_root, "fetch_history_result", _history_stub)
    result, _ = call(
        "coingecko_get_price_history", coin="solana", timeframe="6M", vs_currency="eur"
    )
    assert captured["searched"] == "solana"
    assert captured["history_args"] == ("solana", "6M", "coingecko", "eur")
    assert result["requested"] == "solana"
    assert result["resolved_id"] == "solana"


def test_coingecko_get_price_history_unresolved_reports_unavailable_not_a_crash(monkeypatch):
    """An identifier CoinGecko can't resolve reports an honest unavailable result — it must
    NEVER substitute a different coin, and must never even attempt the history fetch."""

    def _search_stub(q):
        return {}

    def _boom(*a, **k):
        raise AssertionError("fetch_history_result must not run for an unresolved coin")

    import backend.ai.tools as tools_root

    monkeypatch.setattr(prices_module, "search_coin", _search_stub)
    monkeypatch.setattr(tools_root, "fetch_history_result", _boom)
    result, proposed = call("coingecko_get_price_history", coin="not-a-real-coin", timeframe="1M")
    assert result == {
        "requested": "not-a-real-coin",
        "resolved_id": None,
        "provider": None,
        "state": "unavailable",
        "as_of": None,
        "degraded_reason": "no_data",
        "points": [],
    }
    assert proposed is None


def test_coingecko_get_price_history_in_registered_set():
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "coingecko_get_price_history" in names


# ── service: fetch_history_result shape (what both history tools pass through) ───


def test_fetch_history_result_shape_yahoo(monkeypatch):
    """Real ``fetch_history_result`` (Yahoo branch), network calls stubbed — proves the
    shape ``yahoo_get_price_history`` returns unmodified matches what the service produces."""
    monkeypatch.setattr(
        prices_module,
        "_yf_history",
        lambda symbol, timeframe, **kwargs: [{"date": "2026-08-23", "close": 321.0}],
    )
    result = prices_module.fetch_history_result("ACME-HIST-TEST", "1M", "yahoo")
    assert set(result) == {"provider", "state", "as_of", "degraded_reason", "points"}
    assert result["provider"] == "yahoo"
    assert result["state"] == "live"
    assert result["as_of"] == "2026-08-23"
    assert result["points"] == [{"date": "2026-08-23", "close": 321.0}]


def test_fetch_history_result_shape_coingecko_unavailable(monkeypatch):
    """A resolved coin id with no upstream data comes back unavailable, never a crash —
    the exact shape ``coingecko_get_price_history`` merges ``requested``/``resolved_id`` onto."""
    from backend.services import crypto_market_data

    monkeypatch.setattr(
        crypto_market_data,
        "fetch_crypto_history",
        lambda *a, **k: {
            "provider": None,
            "state": "unavailable",
            "as_of": None,
            "degraded_reason": "upstream_unavailable",
            "points": [],
        },
    )
    result = prices_module.fetch_history_result(
        "definitely-not-a-real-coingecko-id", "1M", "coingecko"
    )
    assert set(result) == {"provider", "state", "as_of", "degraded_reason", "points"}
    assert result["state"] == "unavailable"
    assert result["points"] == []
