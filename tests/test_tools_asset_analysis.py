"""AI tool-dispatch tests for backend/ai/tools/asset_analysis.py.

``get_asset_technicals`` / ``get_asset_fundamentals`` hand Lucius the Analysis tab's own rows, so
the tests pin parity with the services the tab reads, that the projection stays compact (no chart
payload), that neither tool ever spends a model call, and the never-creates resolution refusal.

Isolation: db_isolated. Offline — the price series, quote stats and sector aggregates are mocked.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.ai.tools._write_classification import READ_ONLY_TOOLS
from backend.db.instrument_store import InstrumentStore
from backend.services.asset_fundamentals import compute_fundamentals
from backend.services.asset_technicals import compute_technicals

_INST_ID = "inst-acme"
_SECTOR_STATS = {
    "pe_ratio": {"low": 10.0, "mean": 20.0, "high": 40.0, "count": 12},
}
_STATS = {"pe_ratio": 25.0, "revenue_growth": 0.12}


def call(tool: str, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _inst(**kwargs) -> dict:
    return {
        "id": kwargs.get("id", _INST_ID),
        "symbol": kwargs.get("symbol", "ACME"),
        "name": "Acme Corp",
        "asset_class": kwargs.get("asset_class", "equity"),
        "asset_subclass": kwargs.get("asset_subclass", "stock"),
        "quote_currency": "USD",
        "price_source": kwargs.get("price_source", "yahoo"),
        "price_source_id": kwargs.get("price_source_id", "ACME"),
        "identifiers": {},
        "quote_per_unit": 1.0,
        "sector": kwargs.get("sector", "Technology"),
    }


def _series(*_args, **_kwargs) -> dict:
    start = date(2025, 1, 1)
    points = [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "close": 100.0 + i * 0.2 + (i % 7) * 0.3,
        }
        for i in range(260)
    ]
    return {"currency": "USD", "points": points, "ohlc_available": False}


@pytest.fixture
def seeded(db_isolated, monkeypatch):
    InstrumentStore().bulk_insert([_inst()])
    monkeypatch.setattr("backend.services.asset_technicals.price_series", _series)
    monkeypatch.setattr(
        "backend.services.asset_detail.quote_stats",
        lambda _id: {"currency": "USD", "kind": "equity", "stats": dict(_STATS)},
    )
    monkeypatch.setattr(
        "backend.services.sector_fundamentals.sector_fundamentals_for",
        lambda _sector: dict(_SECTOR_STATS),
    )

    async def _no_model_call(*_args, **_kwargs):
        raise AssertionError("a tool read must not spend a model call")

    monkeypatch.setattr("backend.services.asset_technicals._synthesize", _no_model_call)
    monkeypatch.setattr("backend.services.asset_fundamentals._synthesize", _no_model_call)


@pytest.mark.parametrize("tool", ["get_asset_technicals", "get_asset_fundamentals"])
def test_registered_as_a_read(tool):
    assert tool in {t["name"] for t in TOOL_SCHEMAS}
    assert tool in READ_ONLY_TOOLS


# ── resolution ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ["get_asset_technicals", "get_asset_fundamentals"])
def test_requires_a_reference(tool, db_isolated):
    res, proposed = call(tool)
    assert proposed is None
    assert "error" in res


@pytest.mark.parametrize("tool", ["get_asset_technicals", "get_asset_fundamentals"])
def test_unknown_ticker_is_refused_not_created(tool, db_isolated):
    before = len(InstrumentStore().all())
    res, _ = call(tool, ticker="NOSUCHTICKER")
    assert "error" in res
    assert "resolve_instruments" in res["error"]
    assert len(InstrumentStore().all()) == before


# ── technicals ─────────────────────────────────────────────────────────────────


def test_technicals_match_the_analysis_tab(seeded):
    res, proposed = call("get_asset_technicals", ticker="ACME")
    tab = compute_technicals(_INST_ID)

    assert proposed is None
    assert res["available"] is True
    assert res["instrument_id"] == _INST_ID
    assert res["lean"] == tab["lean"]
    assert [(r["id"], r["value"], r["score"]) for r in res["rows"]] == [
        (r["id"], r["raw_display"], r["score"]) for r in tab["rows"]
    ]
    assert {r["id"] for r in res["rows"]} == {
        "sma_50",
        "sma_200",
        "sma_50_200",
        "macd",
        "rsi",
        "bollinger",
    }


def test_technicals_state_the_date_of_the_last_bar(seeded):
    res, _ = call("get_asset_technicals", instrument_id=_INST_ID)
    assert res["as_of"] == _series()["points"][-1]["date"]


def test_technicals_drop_the_chart_payload(seeded):
    res, _ = call("get_asset_technicals", ticker="ACME")
    assert not {"points", "overlays", "narrative", "bundle_hash"} & set(res)
    assert all(set(r) == {"id", "label", "value", "score"} for r in res["rows"])


@pytest.mark.parametrize(
    "points",
    [[], [{"date": "2026-01-01T10:00:00+00:00", "close": 10.0}]],
    ids=["no_bars", "too_few_bars"],
)
def test_technicals_without_enough_history_say_so(db_isolated, monkeypatch, points):
    InstrumentStore().bulk_insert([_inst()])
    monkeypatch.setattr(
        "backend.services.asset_technicals.price_series",
        lambda *_a, **_k: {"currency": "USD", "points": points},
    )
    res, _ = call("get_asset_technicals", ticker="ACME")
    assert res["available"] is False
    assert res["rows"] == []
    assert res["as_of"] is None and res["lean"] is None
    assert res["reason"]


# ── fundamentals ───────────────────────────────────────────────────────────────


def test_fundamentals_match_the_analysis_tab(seeded):
    res, proposed = call("get_asset_fundamentals", ticker="ACME")
    tab = compute_fundamentals(_INST_ID)

    assert proposed is None
    assert res["available"] is True
    assert res["sector"] == "Technology"
    assert [(r["id"], r["value"]) for r in res["valuation"]] == [
        (r["id"], r["ticker_display"]) for r in tab["valuation"]
    ]
    assert [(r["id"], r["value"]) for r in res["growth"]] == [
        (r["id"], r["ticker_display"]) for r in tab["growth"]
    ]


def test_fundamentals_carry_the_sector_comparison_when_there_is_one(seeded):
    res, _ = call("get_asset_fundamentals", ticker="ACME")
    pe = next(r for r in res["valuation"] if r["id"] == "pe_ratio")
    growth = next(r for r in res["growth"] if r["id"] == "revenue_growth")

    assert pe["higher_is_better"] is False
    assert pe["sector"]["peer_count"] == 12
    assert set(pe["sector"]) == {"low", "mean", "high", "peer_count"}
    assert growth["sector"] is None


def test_fundamentals_drop_the_tab_only_fields(seeded):
    res, _ = call("get_asset_fundamentals", ticker="ACME")
    assert not {"narrative", "bundle_hash"} & set(res)
    assert all(
        set(r) == {"id", "label", "value", "higher_is_better", "sector"} for r in res["valuation"]
    )


def test_fundamentals_for_a_crypto_asset_say_there_are_none(db_isolated, monkeypatch):
    InstrumentStore().bulk_insert(
        [_inst(asset_class="crypto", price_source="coingecko", price_source_id="acmecoin")]
    )
    monkeypatch.setattr(
        "backend.services.asset_detail.quote_stats",
        lambda _id: {"currency": "USD", "kind": "crypto", "stats": {"market_cap": 1.0}},
    )
    res, _ = call("get_asset_fundamentals", instrument_id=_INST_ID)
    assert res["available"] is False
    assert res["valuation"] == [] and res["growth"] == []
    assert res["reason"]


def test_fundamentals_sanitize_the_sector(seeded, monkeypatch):
    monkeypatch.setattr(
        "backend.ai.tools.asset_analysis.compute_fundamentals",
        lambda _id: {
            "currency": "USD",
            "sector": "Tech\x00nology",
            "valuation": [],
            "growth": [],
        },
    )
    res, _ = call("get_asset_fundamentals", ticker="ACME")
    assert "\x00" not in res["sector"]
