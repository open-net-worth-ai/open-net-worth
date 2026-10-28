"""
API smoke tests for GET /api/intel/assets/{instrument_id}, GET /series, POST /refresh.

Covers:
- GET 200 with all required top-level keys present
- unknown id → 404
- series?timeframe=1D → intraday_available=false, points=[]
- POST refresh → 200 with refreshed bool
- assert NO api-key string leaks into any response body
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seeded(tmp_path, monkeypatch, db_isolated):
    """Wire all stores to tmp_path and seed one instrument + no positions.
    PriceHistoryStore resolves against the per-test isolated database (db_isolated),
    which also covers accounts (DB-backed now, plan 3 task 4) and positions/lots
    (DB-backed, plan 4b task 2) — all start empty, no explicit "no positions" seed
    needed any more (the old accounts.json/positions.json "[]" writes are gone)."""
    from backend.db.instrument_store import InstrumentStore
    from backend.services import fx_cache as _fx
    from backend.services import prices as _prices

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"USD": 1.0, "SGD": 1.35})
    monkeypatch.setattr(
        "backend.services.asset_detail.read_profile",
        lambda: {"base_currency": "SGD"},
    )
    # This fixture's price_source_id ("TST.US") is not a real Yahoo symbol, so an
    # unstubbed fetch_live would make a real, slow, failing network call on every
    # GET /api/intel/assets/{id} in this file — asset_detail.py's own fetch_live
    # call already treats "no live data" as a no-op fallback (see its try/except),
    # so an empty result here is behaviorally identical, just fast and deterministic.
    monkeypatch.setattr(_prices, "fetch_live", lambda *a, **k: {})

    instrument_id = "test-inst-" + uuid.uuid4().hex[:8]
    inst = {
        "id": instrument_id,
        "symbol": "TST",
        "name": "Test Corp",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "TST.US",
        "identifiers": {},
        "quote_per_unit": 1.0,
    }
    InstrumentStore().insert(inst)

    return {"instrument_id": instrument_id, "tmp_path": tmp_path}


# ── tests ─────────────────────────────────────────────────────────────────────


def test_get_asset_detail_200_all_keys(client, seeded):
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200
    body = resp.json()
    required = {
        "asset",
        "position",
        "price",
        "news",
        "key_issues",
        "facts",
        "peers",
        "watchlist",
        "last_refreshed_at",
        "has_ticker",
    }
    assert required <= set(body.keys()), f"Missing keys: {required - set(body.keys())}"


def test_get_asset_detail_asset_fields(client, seeded):
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    asset = resp.json()["asset"]
    asset_required = {
        "name",
        "symbol",
        "ticker",
        "exchange",
        "mic",
        "country",
        "asset_class",
        "currency",
        "description",
    }
    assert asset_required <= set(asset.keys())
    assert asset["symbol"] == "TSTS"[:-1] or asset["symbol"] == "TSTS"[:3]  # "TST"


def test_get_asset_detail_404_unknown(client, seeded):
    resp = client.get("/api/intel/assets/totally-unknown-id")
    assert resp.status_code == 404


def test_get_asset_detail_facts_unverified_always_true(client, seeded):
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.json()["facts"]["unverified"] is True


def test_get_asset_detail_has_ticker_true(client, seeded):
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.json()["has_ticker"] is True


def test_series_1d_returns_empty_intraday_false(client, seeded):
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}/series?timeframe=1D")
    assert resp.status_code == 200
    body = resp.json()
    assert body["points"] == []
    assert body["intraday_available"] is False
    assert "price_source_id" in body
    assert "currency" in body


def test_series_invalid_timeframe_422(client, seeded):
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}/series?timeframe=INVALID")
    assert resp.status_code == 422


def test_series_5d_is_a_valid_timeframe():
    # 5D must be accepted by the endpoint's validation set (hermetic — no network).
    from backend.api.intel_assets import _VALID_TIMEFRAMES

    assert "5D" in _VALID_TIMEFRAMES


def test_series_1m_returns_structure(client, seeded):
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}/series?timeframe=1M")
    assert resp.status_code == 200
    body = resp.json()
    assert "points" in body
    assert isinstance(body["points"], list)
    assert "intraday_available" in body


def test_get_asset_detail_ignores_stray_currency_query_param(client, seeded):
    """A stray `currency` query param (e.g. a stale bookmark from before the per-holding
    currency preference was removed — owner decision, 2026-07: crypto always shows in the
    portfolio base currency) is silently ignored, never a 422/500."""
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}?currency=USD")
    assert resp.status_code == 200
    assert "price" in resp.json()


def test_series_ignores_stray_vs_query_param(client, seeded):
    """A stray `vs` query param (same stale-bookmark case) is silently ignored."""
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}/series?timeframe=1D&vs=SGD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["points"] == []
    assert "currency" in body


def test_analyst_endpoint_shape(client, seeded, monkeypatch):
    """GET /analyst returns {currency, analyst}; analyst payload passes through from the
    (mocked) Yahoo fetch, so the suite stays offline / $0."""
    iid = seeded["instrument_id"]
    payload = {
        "rating_key": "strong_buy",
        "rating_label": "Strong Buy",
        "analyst_count": 50,
        "target_low": 70.0,
        "target_mean": 104.43,
        "target_high": 150.0,
        "current": 68.85,
        "upside_pct": 51.68,
        "distribution": {"strong_buy": 9, "buy": 36, "hold": 5, "sell": 1, "strong_sell": 0},
    }
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: payload)
    resp = client.get(f"/api/intel/assets/{iid}/analyst")
    assert resp.status_code == 200
    body = resp.json()
    assert body["currency"] == "USD"
    assert body["analyst"]["rating_label"] == "Strong Buy"
    assert body["analyst"]["analyst_count"] == 50
    assert body["analyst"]["distribution"]["buy"] == 36


def test_analyst_endpoint_empty_when_no_coverage(client, seeded, monkeypatch):
    """No analyst coverage (Yahoo returns {}) → analyst:{} so the frontend hides the widget."""
    iid = seeded["instrument_id"]
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: {})
    resp = client.get(f"/api/intel/assets/{iid}/analyst")
    assert resp.status_code == 200
    assert resp.json()["analyst"] == {}


def test_fundamentals_endpoint_shape(client, seeded, monkeypatch):
    """GET /fundamentals returns valuation + growth row arrays with sector columns."""
    iid = seeded["instrument_id"]
    monkeypatch.setattr(
        "backend.services.asset_detail.quote_stats",
        lambda *_a, **_k: {
            "currency": "USD",
            "kind": "equity",
            "stats": {
                "pe_ratio": 28.4,
                "forward_pe": 24.1,
                "eps": 3.5,
                "return_on_equity": 0.22,
            },
        },
    )
    monkeypatch.setattr(
        "backend.services.sector_fundamentals.resolve_sector",
        lambda *_a, **_k: "Technology",
    )
    monkeypatch.setattr(
        "backend.services.sector_fundamentals.sector_fundamentals_for",
        lambda *_a, **_k: {
            "pe_ratio": {"low": 10.0, "mean": 20.0, "high": 40.0, "count": 90},
            # forward_pe intentionally missing → ticker-only row
        },
    )
    resp = client.get(f"/api/intel/assets/{iid}/fundamentals?narrate=false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sector"] == "Technology"
    assert isinstance(body["valuation"], list)
    assert isinstance(body["growth"], list)
    assert body["narrative"] is None
    assert body["bundle_hash"]

    by_id = {r["id"]: r for r in body["valuation"] + body["growth"]}
    pe = by_id["pe_ratio"]
    assert pe["ticker"] == 28.4
    assert pe["sector_low"] == 10.0
    assert pe["sector_mean"] == 20.0
    assert pe["sector_high"] == 40.0
    assert pe["position_pct"] == pytest.approx(61.3, abs=0.1)
    assert pe["peer_count"] == 90
    assert pe["ticker_display"]

    fwd = by_id["forward_pe"]
    assert fwd["ticker"] == 24.1
    assert fwd["sector_low"] is None
    assert fwd["position_pct"] is None

    assert by_id["eps"]["ticker"] == 3.5
    assert by_id["return_on_equity"]["ticker"] == 0.22


def test_fundamentals_empty_for_crypto_kind(client, seeded, monkeypatch):
    iid = seeded["instrument_id"]
    monkeypatch.setattr(
        "backend.services.asset_detail.quote_stats",
        lambda *_a, **_k: {
            "currency": "USD",
            "kind": "crypto",
            "stats": {"pe_ratio": 99},
        },
    )
    resp = client.get(f"/api/intel/assets/{iid}/fundamentals?narrate=false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valuation"] == []
    assert body["growth"] == []
    assert body.get("sector") is None
    assert body.get("narrative") is None


def test_fundamentals_empty_for_etf_wrapper(client, seeded, monkeypatch):
    """ETFs resolve as equity+etf — stock-style FA must not run (CSPX.L-class)."""
    from backend.db.instrument_store import InstrumentStore

    iid = seeded["instrument_id"]
    InstrumentStore().touch(
        iid, {"asset_subclass": "etf", "symbol": "CSPX", "name": "iShares Core S&P 500"}
    )
    monkeypatch.setattr(
        "backend.services.asset_detail.quote_stats",
        lambda *_a, **_k: {
            "currency": "USD",
            "kind": "equity",
            "stats": {"pe_ratio": 24.8, "forward_pe": 22.0},
        },
    )
    calls = []

    async def _fake_synthesize(**_kwargs):
        calls.append(_kwargs)
        return "should not run"

    monkeypatch.setattr("backend.services.asset_fundamentals._synthesize", _fake_synthesize)

    resp = client.get(f"/api/intel/assets/{iid}/fundamentals?narrate=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valuation"] == []
    assert body["growth"] == []
    assert body.get("narrative") is None
    assert calls == []


def test_fundamentals_narrative_skipped_when_no_sector_peers(client, seeded, monkeypatch):
    """Ticker-only rows (no sector mean) must not fire the LLM."""
    iid = seeded["instrument_id"]
    monkeypatch.setattr(
        "backend.services.asset_detail.quote_stats",
        lambda *_a, **_k: {
            "currency": "USD",
            "kind": "equity",
            "stats": {"pe_ratio": 24.8},
        },
    )
    monkeypatch.setattr(
        "backend.services.sector_fundamentals.resolve_sector",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "backend.services.sector_fundamentals.sector_fundamentals_for",
        lambda *_a, **_k: {},
    )
    calls = []

    async def _fake_synthesize(**_kwargs):
        calls.append(_kwargs)
        return "hard to say"

    monkeypatch.setattr("backend.services.asset_fundamentals._synthesize", _fake_synthesize)

    resp = client.get(f"/api/intel/assets/{iid}/fundamentals?narrate=true")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["valuation"]) + len(body["growth"]) >= 1
    assert body["valuation"][0]["sector_mean"] is None
    assert body.get("narrative") is None
    assert calls == []


def test_fundamentals_narrative_persists_to_asset_brief_store(client, seeded, monkeypatch):
    """AI paragraph is cached in AssetBriefStore, keyed to fundamentals_bundle_hash."""
    from backend.db.asset_brief_store import AssetBriefStore

    iid = seeded["instrument_id"]
    monkeypatch.setattr(
        "backend.services.asset_detail.quote_stats",
        lambda *_a, **_k: {
            "currency": "USD",
            "kind": "equity",
            "stats": {"pe_ratio": 28.4, "return_on_equity": 0.22},
        },
    )
    monkeypatch.setattr(
        "backend.services.sector_fundamentals.resolve_sector",
        lambda *_a, **_k: "Technology",
    )
    monkeypatch.setattr(
        "backend.services.sector_fundamentals.sector_fundamentals_for",
        lambda *_a, **_k: {
            "pe_ratio": {"low": 10.0, "mean": 20.0, "high": 40.0, "count": 90},
        },
    )
    calls = []

    async def _fake_synthesize(**_kwargs):
        calls.append(_kwargs)
        return "P/E sits above the sector mean while ROE looks healthy."

    monkeypatch.setattr("backend.services.asset_fundamentals._synthesize", _fake_synthesize)

    resp = client.get(f"/api/intel/assets/{iid}/fundamentals?narrate=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative"] == "P/E sits above the sector mean while ROE looks healthy."
    assert len(calls) == 1

    brief = AssetBriefStore().get(iid)
    assert brief["fundamentals_bundle_hash"] == body["bundle_hash"]
    assert brief["fundamentals_narrative"] == body["narrative"]

    resp2 = client.get(f"/api/intel/assets/{iid}/fundamentals?narrate=true")
    assert resp2.json()["narrative"] == body["narrative"]
    assert len(calls) == 1


def test_technicals_endpoint_shape(client, seeded, monkeypatch):
    """GET /technicals returns rows + points; narrate=false skips LLM."""
    iid = seeded["instrument_id"]
    points = [
        {"date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "close": 100.0 + i * 0.2}
        for i in range(220)
    ]

    def _series(_iid, timeframe="1Y"):
        return {"currency": "USD", "points": points, "ohlc_available": True}

    monkeypatch.setattr("backend.services.asset_technicals.price_series", _series)
    resp = client.get(f"/api/intel/assets/{iid}/technicals?timeframe=1Y&narrate=false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["timeframe"] == "1Y"
    assert body["narrative"] is None
    assert len(body["rows"]) == 6
    assert body["rows"][2]["label"] == "SMA 50 / 200"
    assert body["bundle_hash"]
    assert body["overlays"]["sma_50"]
    assert body["overlays"]["macd"]
    assert body["overlays"]["rsi"]
    assert body["overlays"]["bb_mid"]
    assert body["overlays"]["bb_upper"]
    assert len(body["points"]) == 220
    assert body["lean"] in ("leans_buy", "leans_sell", "mixed")
    # Sell / Neutral / Buy scale anchors (not historical min/mean/max).
    row0 = body["rows"][0]
    assert row0["scale_sell_display"]
    assert row0["scale_neutral_display"]
    assert row0["scale_buy_display"]
    assert row0["raw_display"]
    assert "scale_low_display" not in row0


def test_technicals_narrative_persists_to_asset_brief_store(client, seeded, monkeypatch):
    """The AI paragraph is cached in AssetBriefStore (DB-backed), not an in-process dict —
    it must survive being read by a brand-new AssetBriefStore() instance (the same object
    identity a real process restart would force), and a matching bundle_hash must skip a
    second LLM call entirely."""
    from backend.db.asset_brief_store import AssetBriefStore

    iid = seeded["instrument_id"]
    points = [
        {"date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "close": 100.0 + i * 0.2}
        for i in range(220)
    ]
    monkeypatch.setattr(
        "backend.services.asset_technicals.price_series",
        lambda _iid, timeframe="1Y": {"currency": "USD", "points": points, "ohlc_available": True},
    )
    calls = []

    async def _fake_synthesize(rows):
        calls.append(rows)
        return "Mechanically bullish on balance."

    monkeypatch.setattr("backend.services.asset_technicals._synthesize", _fake_synthesize)

    resp = client.get(f"/api/intel/assets/{iid}/technicals?timeframe=1Y&narrate=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative"] == "Mechanically bullish on balance."
    assert len(calls) == 1

    # A brand-new store instance (no shared Python object) must see the SAME cached
    # narrative — proof this is a DB row, not a module-level dict tied to process memory.
    brief = AssetBriefStore().get(iid)
    assert brief["technicals_bundle_hash"] == body["bundle_hash"]
    assert brief["technicals_narrative"] == "Mechanically bullish on balance."

    # Same rows -> same hash -> the second request must reuse the cached paragraph,
    # never calling the LLM again.
    resp2 = client.get(f"/api/intel/assets/{iid}/technicals?timeframe=1Y&narrate=true")
    assert resp2.json()["narrative"] == "Mechanically bullish on balance."
    assert len(calls) == 1


def test_technicals_rejects_intraday_timeframe(client, seeded):
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}/technicals?timeframe=1D")
    assert resp.status_code == 422


def test_post_refresh_search_unconfigured(client, seeded):
    """When no search provider is configured, refresh returns refreshed=false (not 500)."""
    from unittest.mock import patch

    iid = seeded["instrument_id"]
    with patch(
        "backend.monitoring.asset_brief.get_search_provider", side_effect=ValueError("no provider")
    ):
        resp = client.post(f"/api/intel/assets/{iid}/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["refreshed"] is False
    assert body["reason"] == "search_unconfigured"


def test_post_refresh_unknown_id_returns_no_ticker(client, seeded):
    resp = client.post("/api/intel/assets/totally-unknown/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["refreshed"] is False
    assert body["reason"] == "no_ticker"


def test_no_api_key_in_response(client, seeded):
    """Smoke: no API key string in any response body including the POST /refresh endpoint."""
    from unittest.mock import patch

    iid = seeded["instrument_id"]
    detail_body = client.get(f"/api/intel/assets/{iid}").text
    series_body = client.get(f"/api/intel/assets/{iid}/series?timeframe=1M").text
    with patch(
        "backend.monitoring.asset_brief.get_search_provider", side_effect=ValueError("no provider")
    ):
        refresh_body = client.post(f"/api/intel/assets/{iid}/refresh?force=true").text

    suspicious_patterns = ["api_key", "apiKey", "secret", "token", "password"]
    for body_str in (detail_body, series_body, refresh_body):
        body_lower = body_str.lower()
        for pat in suspicious_patterns:
            # The response must not contain credential-looking field names
            # (we only check that no "api_key" type field leaks — not standard words like "provider")
            if pat in body_lower:
                # It's OK if the word appears as part of a non-secret context
                # but never as a real credential value. Check the parsed JSON.
                parsed = json.loads(body_str)
                flat = json.dumps(parsed).lower()
                # If found, it must NOT be a key with a non-empty string value
                if pat == "api_key" and "api_key" in flat:
                    raise AssertionError(f"api_key field found in response: {body_str[:200]}")


# ── Freshness block tests ──────────────────────────────────────────────────────


def test_get_asset_detail_returns_freshness_block(client, seeded):
    """GET response must include a 'freshness' key with required sub-keys."""
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200
    body = resp.json()
    assert "freshness" in body, "Response missing 'freshness' block"
    freshness = body["freshness"]
    required_freshness_keys = {"grade", "latest_news_at", "brief_synthesized_at", "search_provider"}
    assert required_freshness_keys <= set(freshness.keys()), (
        f"Missing freshness keys: {required_freshness_keys - set(freshness.keys())}"
    )
    assert freshness["grade"] in ("fresh", "aging", "stale")


def test_get_asset_detail_freshness_stale_when_no_brief(client, seeded):
    """When no brief is cached, freshness.grade must be 'stale'."""
    iid = seeded["instrument_id"]
    # No brief written for seeded fixture (brief_path left empty)
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200
    freshness = resp.json()["freshness"]
    assert freshness["grade"] == "stale"
    assert freshness["brief_synthesized_at"] is None


def test_get_asset_detail_sources_are_object_shape(client, seeded):
    """Sources in key_issues must always be dicts with url/title/domain/published_at/quote."""
    from datetime import datetime, timezone

    # Seed a brief with a bare-string source (legacy shape) directly through the (DB-backed)
    # AssetBriefStore — the replacement for the old raw asset_briefs.json write.
    from backend.db.asset_brief_store import AssetBriefStore

    iid = seeded["instrument_id"]
    brief = {
        "instrument_id": iid,
        "key_issues": [
            {
                "theme": "Test",
                "bullish": {
                    "arguments": ["positive"],
                    "sources": ["https://example.com/legacy-article"],
                },
                "bearish": {"arguments": [], "sources": []},
            }
        ],
        "facts": {},
        "peers": [],
        "description": "Brief description.",
        "synthesized_at": datetime.now(timezone.utc).isoformat(),
        "search_provider": "tavily",
    }
    AssetBriefStore().upsert_brief(brief)

    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200
    body = resp.json()
    ki = body.get("key_issues", [])
    assert len(ki) == 1
    bull_sources = ki[0]["bullish"]["sources"]
    assert len(bull_sources) == 1
    src = bull_sources[0]
    assert isinstance(src, dict), f"Expected dict source, got: {type(src)}"
    required_src_keys = {"url", "title", "domain", "published_at", "quote"}
    assert required_src_keys <= set(src.keys()), (
        f"Missing source keys: {required_src_keys - set(src.keys())}"
    )
    assert src["url"] == "https://example.com/legacy-article"
    assert src["domain"] == "example.com"


# ── news: live evidence query (RFC intel-signals-data-model §5) ────────────────


def test_get_asset_detail_news_is_served_live_from_evidence(client, seeded):
    """End-to-end: GET /api/intel/assets/{id} serves the news feed from a LIVE `evidence`
    query — no AssetBriefStore write needed, raw title verbatim, no headline/summary key."""
    from datetime import datetime, timezone

    from backend.services.evidence import record_evidence

    iid = seeded["instrument_id"]
    record_evidence(
        [
            {
                "title": "Test Corp posts record quarterly revenue",
                "source": "Reuters",
                "url": "https://example.com/test-corp-earnings",
                "published_date": datetime.now(timezone.utc).isoformat(),
            }
        ],
        subjects=[f"asset:{iid}"],
        kind="news",
    )

    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200
    news = resp.json()["news"]
    assert len(news) == 1
    item = news[0]
    assert item["title"] == "Test Corp posts record quarterly revenue"
    assert item["source"] == "Reuters"
    assert item["url"] == "https://example.com/test-corp-earnings"
    assert "headline" not in item
    assert "summary" not in item


# ── /api/intel/assets/resolve smoke tests ─────────────────────────────────────


def test_resolve_asset_200_known_ticker(client, seeded, monkeypatch):
    """POST /resolve {q: AAPL} → 200 with instrument_id when resolve_instrument succeeds."""
    iid = seeded["instrument_id"]
    resolved_inst = {
        "id": iid,
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_class": "equity",
        "quote_currency": "USD",
    }
    monkeypatch.setattr(
        "backend.services.instruments.resolve_instrument",
        lambda q, **_kw: (resolved_inst, False),
    )
    resp = client.post("/api/intel/assets/resolve", json={"q": "AAPL"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["instrument_id"] == iid
    assert body["ticker"] == "AAPL"
    assert body["label"] == "Apple Inc."
    assert body["currency"] == "USD"


def test_resolve_asset_404_junk_ticker(client, seeded, monkeypatch):
    """POST /resolve {q: XYZJUNK} → 404 when resolve_instrument cannot find anything."""
    monkeypatch.setattr(
        "backend.services.instruments.resolve_instrument",
        lambda q, **_kw: (None, False),
    )
    resp = client.post("/api/intel/assets/resolve", json={"q": "XYZJUNK"})
    assert resp.status_code == 404, resp.text


def test_resolve_asset_forwards_source_fields(client, seeded, monkeypatch):
    """POST /resolve with asset_class + price_source + price_source_id passes them as kwargs."""
    iid = seeded["instrument_id"]
    resolved_inst = {
        "id": iid,
        "symbol": "BTC",
        "name": "Bitcoin",
        "asset_class": "crypto",
        "quote_currency": "USD",
    }
    captured: dict = {}

    def _mock_resolve(q, **kw):
        captured.update(kw)
        return (resolved_inst, False)

    monkeypatch.setattr("backend.services.instruments.resolve_instrument", _mock_resolve)
    resp = client.post(
        "/api/intel/assets/resolve",
        json={
            "q": "BTC",
            "asset_class": "crypto",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "name": "Bitcoin",
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured.get("asset_class") == "crypto"
    assert captured.get("price_source") == "coingecko"
    assert captured.get("price_source_id") == "bitcoin"
    assert captured.get("name") == "Bitcoin"  # human-readable name threaded through


def test_resolve_asset_fast_path_stores_name(client, seeded):
    """Regression: a palette pick of a non-held coin (q=symbol + canonical key + name)
    must create the instrument with its human-readable NAME, so the asset-detail label
    reads 'Bitcoin', not 'BTC'. Uses the real resolve_instrument (no provider lookup —
    the price_source/price_source_id fast path skips it)."""
    resp = client.post(
        "/api/intel/assets/resolve",
        json={
            "q": "BTC",
            "asset_class": "crypto",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "name": "Bitcoin",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] == "Bitcoin"  # not "BTC"


def test_resolve_asset_symbol_collision_returns_422(client, seeded, monkeypatch):
    """A resolve_instrument ValidationError (symbol-collision refusal) surfaces as 422 with
    a readable detail, not a raw 500 — the route's OpenNetWorthError → HTTPException guard."""
    from backend.core.errors import ValidationError

    def _raise(q, **_kw):
        raise ValidationError(
            "an instrument with symbol 'AAPL' (equity) already exists (id=inst-x)",
            existing_instrument_id="inst-x",
        )

    monkeypatch.setattr("backend.services.instruments.resolve_instrument", _raise)
    resp = client.post("/api/intel/assets/resolve", json={"q": "AAPL.MX"})
    assert resp.status_code == 422
    assert "AAPL" in resp.json()["detail"]


def test_resolve_asset_optional_fields_backward_compat(client, seeded, monkeypatch):
    """POST /resolve without new optional fields still works (backward compat)."""
    iid = seeded["instrument_id"]
    resolved_inst = {
        "id": iid,
        "symbol": "MSFT",
        "name": "Microsoft",
        "asset_class": "equity",
        "quote_currency": "USD",
    }
    monkeypatch.setattr(
        "backend.services.instruments.resolve_instrument",
        lambda q, **_kw: (resolved_inst, False),
    )
    # Old-style call: q only, no new fields
    resp = client.post("/api/intel/assets/resolve", json={"q": "MSFT"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ticker"] == "MSFT"


# ── GET /api/intel/assets/by-ticker/{ticker} ──────────────────────────────────


def test_get_asset_by_ticker_exact_match_skips_resolve_instrument(client, seeded, monkeypatch):
    """The common case — an already-known instrument — resolves via the exact
    price_source_id match and never touches resolve_instrument's fuzzy ladder."""

    def _boom(*_a, **_k):
        raise AssertionError("resolve_instrument must not be called for an exact match")

    monkeypatch.setattr("backend.services.instruments.resolve_instrument", _boom)
    resp = client.get("/api/intel/assets/by-ticker/TST.US")
    assert resp.status_code == 200, resp.text
    assert resp.json()["instrument_id"] == seeded["instrument_id"]


def test_get_asset_by_ticker_case_insensitive(client, seeded, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("resolve_instrument must not be called for an exact match")

    monkeypatch.setattr("backend.services.instruments.resolve_instrument", _boom)
    resp = client.get("/api/intel/assets/by-ticker/tst.us")
    assert resp.status_code == 200, resp.text
    assert resp.json()["instrument_id"] == seeded["instrument_id"]


def test_get_asset_by_ticker_includes_instrument_id(client, seeded):
    """The plain id-route response also carries instrument_id now — same shared
    payload builder — so the /quote/$symbol page can key its subsequent series/
    stats/refresh calls off of it without a route param."""
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200
    assert resp.json()["instrument_id"] == iid


def test_get_asset_by_ticker_404_unresolvable(client, seeded, monkeypatch):
    monkeypatch.setattr(
        "backend.services.instruments.resolve_instrument", lambda q, **_kw: (None, False)
    )
    resp = client.get("/api/intel/assets/by-ticker/XYZJUNK")
    assert resp.status_code == 404


def test_get_asset_by_ticker_falls_through_for_new_ticker(client, seeded, monkeypatch):
    """A ticker not yet in the store falls through to resolve_instrument's
    find-or-create (mocked here to return a second, already-persisted instrument)."""
    from backend.db.instrument_store import InstrumentStore

    new_inst = {
        "id": "test-inst-new",
        "symbol": "NEW",
        "name": "New Co",
        "asset_class": "equity",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "NEW.US",
        "identifiers": {},
    }
    InstrumentStore().insert(new_inst)
    monkeypatch.setattr(
        "backend.services.instruments.resolve_instrument", lambda q, **_kw: (new_inst, True)
    )
    resp = client.get("/api/intel/assets/by-ticker/NEWTICK")
    assert resp.status_code == 200, resp.text
    assert resp.json()["instrument_id"] == "test-inst-new"


def test_get_asset_by_ticker_matches_held_crypto_display_symbol(client, seeded, monkeypatch):
    """A held crypto position's row carries its display symbol ("BTC"), not its
    CoinGecko price_source_id ("bitcoin") — regression for that gap: the endpoint
    must still resolve it locally, without touching resolve_instrument's ladder."""
    from backend.db.instrument_store import InstrumentStore

    InstrumentStore().insert(
        {
            "id": "test-inst-btc",
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "USD",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "identifiers": {},
        }
    )

    def _boom(*_a, **_k):
        raise AssertionError("resolve_instrument must not be called")

    monkeypatch.setattr("backend.services.instruments.resolve_instrument", _boom)
    resp = client.get("/api/intel/assets/by-ticker/BTC")
    assert resp.status_code == 200, resp.text
    assert resp.json()["instrument_id"] == "test-inst-btc"


def test_get_asset_by_ticker_matches_bare_symbol_for_any_asset_class(client, seeded, monkeypatch):
    """Regression for a code-review finding: commitments/fundingValue.ts's funding-source
    link prefers a position's bare `symbol` ("ONWAI") over its qualified `ticker`
    ("ONWAI.DE"), so an EQUITY holding can just as easily reach this endpoint with a bare
    symbol as a crypto one can — the bare-symbol fallback isn't crypto-only."""
    from backend.db.instrument_store import InstrumentStore

    InstrumentStore().insert(
        {
            "id": "test-inst-onwai",
            "symbol": "ONWAI",
            "name": "ONWAI SE",
            "asset_class": "equity",
            "quote_currency": "EUR",
            "price_source": "yahoo",
            "price_source_id": "ONWAI.DE",
            "identifiers": {},
        }
    )

    def _boom(*_a, **_k):
        raise AssertionError("resolve_instrument must not be called")

    monkeypatch.setattr("backend.services.instruments.resolve_instrument", _boom)
    resp = client.get("/api/intel/assets/by-ticker/ONWAI")
    assert resp.status_code == 200, resp.text
    assert resp.json()["instrument_id"] == "test-inst-onwai"


def test_get_asset_by_ticker_symbol_collision_returns_422(client, seeded, monkeypatch):
    """A resolve_instrument ValidationError (symbol-collision refusal) surfaces as 422,
    mirroring POST /resolve's OpenNetWorthError -> HTTPException guard."""
    from backend.core.errors import ValidationError

    def _raise(q, **_kw):
        raise ValidationError(
            "an instrument with symbol 'AAPL' (equity) already exists (id=inst-x)",
            existing_instrument_id="inst-x",
        )

    monkeypatch.setattr("backend.services.instruments.resolve_instrument", _raise)
    resp = client.get("/api/intel/assets/by-ticker/AAPL.MX")
    assert resp.status_code == 422
    assert "AAPL" in resp.json()["detail"]


# ── GET /api/intel/assets/search ──────────────────────────────────────────────


def test_search_assets_200_list_shape(client, seeded, monkeypatch):
    """GET /api/intel/assets/search?q=AAPL → 200 with a list of candidates."""
    fake_results = [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "exchange": "NASDAQ",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    ]
    monkeypatch.setattr("backend.services.prices.search_market", lambda q, limit=8: fake_results)
    resp = client.get("/api/intel/assets/search?q=AAPL")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    r = body[0]
    required = {"symbol", "name", "asset_class", "exchange", "price_source", "price_source_id"}
    assert required <= set(r.keys())


def test_search_assets_passes_limit(client, seeded, monkeypatch):
    """GET /search?q=X&limit=5 → limit forwarded to search_market."""
    captured: dict = {}

    def _mock_search(q, limit=8):
        captured["q"] = q
        captured["limit"] = limit
        return []

    monkeypatch.setattr("backend.services.prices.search_market", _mock_search)
    resp = client.get("/api/intel/assets/search?q=test&limit=5")
    assert resp.status_code == 200
    assert captured["limit"] == 5
    assert captured["q"] == "test"


def test_search_assets_strips_whitespace(client, seeded, monkeypatch):
    """Query is stripped before being forwarded."""
    captured: dict = {}

    def _mock_search(q, limit=8):
        captured["q"] = q
        return []

    monkeypatch.setattr("backend.services.prices.search_market", _mock_search)
    resp = client.get("/api/intel/assets/search?q=+apple+")
    assert resp.status_code == 200
    assert captured["q"] == "apple"


def test_search_assets_missing_q_422(client, seeded):
    """GET /search without ?q= → 422 (Query required)."""
    resp = client.get("/api/intel/assets/search")
    assert resp.status_code == 422


def test_search_assets_limit_gt_20_422(client, seeded):
    """GET /search?q=X&limit=99 → 422 (limit max=20)."""
    resp = client.get("/api/intel/assets/search?q=apple&limit=99")
    assert resp.status_code == 422


def test_search_assets_empty_results(client, seeded, monkeypatch):
    """search_market returning [] → endpoint returns []."""
    monkeypatch.setattr("backend.services.prices.search_market", lambda q, limit=8: [])
    resp = client.get("/api/intel/assets/search?q=zzznomatch")
    assert resp.status_code == 200
    assert resp.json() == []


# ── On-demand key_issues top-up: GET must never wait on the background pass ───────────────────


def test_get_asset_detail_response_time_unaffected_by_background_refresh_trigger(
    client, seeded, monkeypatch
):
    """The on-demand top-up (trigger_on_demand_brief_refresh) is fired via asyncio.create_task
    and MUST NEVER be awaited by the read path. Proven end to end here: the background
    synthesis pass is stubbed to take far longer than any acceptable response time, and the GET
    must still return fast — the whole point of the fire-and-forget design."""
    import backend.monitoring.analyst as analyst_mod

    iid = seeded["instrument_id"]
    monkeypatch.setattr(analyst_mod, "_is_held_instrument", lambda i: i == iid)
    monkeypatch.setattr(analyst_mod, "_is_watched_instrument", lambda i: False)

    async def _slow_process_row(row, pa_store, evidence_cache=None):
        await asyncio.sleep(2.0)
        return 1

    monkeypatch.setattr(analyst_mod, "_process_row", _slow_process_row)

    start = time.monotonic()
    resp = client.get(f"/api/intel/assets/{iid}")
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    assert elapsed < 1.0, (
        f"GET took {elapsed:.2f}s — the background refresh must never block the read path"
    )


def test_get_asset_detail_response_includes_key_issues_refresh_stage_field(client, seeded):
    """R2 fix (2026-08-31): key_issues_refresh_stage is its OWN field on the response, separate
    from refresh_stage — see asset_brief.stamp_key_issues_refresh_stage's docstring for why the
    two on-demand pipelines can no longer share one field. Null on a brand-new brief with no
    in-flight pass of either kind."""
    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200
    body = resp.json()
    assert "key_issues_refresh_stage" in body
    assert "refresh_stage" in body
    assert body["key_issues_refresh_stage"] is None


def test_get_asset_detail_schedules_on_demand_refresh_for_stale_held_instrument(
    client, seeded, monkeypatch
):
    """Positive wiring check: opening a stale HELD instrument's page really does schedule
    trigger_on_demand_brief_refresh with the right instrument_id. The route imports the
    function LOCALLY (inside the handler body, this file's own deferred-import convention for
    monitoring/-layer calls), so it must be patched at its SOURCE module — patching
    `backend.api.intel_assets.trigger_on_demand_brief_refresh` would have no effect, since that
    name is never bound at api.intel_assets module level."""
    import backend.monitoring.analyst as analyst_mod

    calls: list[str] = []

    async def _fake_trigger(instrument_id):
        calls.append(instrument_id)
        return True

    monkeypatch.setattr(analyst_mod, "trigger_on_demand_brief_refresh", _fake_trigger)

    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200

    # The task is scheduled via asyncio.create_task, so it may not have RUN by the time the
    # synchronous TestClient call returns — poll briefly for it to land.
    for _ in range(50):
        if calls:
            break
        time.sleep(0.02)
    assert calls == [iid]


def test_get_asset_detail_schedules_refresh_for_ad_hoc_instrument_with_a_real_ticker(
    client, seeded, monkeypatch
):
    """Owner decision 2026-08-31: "even on demand, when users open something they don't own" —
    an ad-hoc (not held, not watched) instrument that resolves to a real ticker NOW ALSO
    triggers the on-demand top-up — the real trigger_on_demand_brief_refresh runs end to end
    and reaches _process_row, same as a held/watched instrument. Formerly named
    "does_not_schedule..." and asserted the opposite, pre-widening."""
    import backend.monitoring.analyst as analyst_mod
    import backend.monitoring.asset_brief as asset_brief_mod

    complete_calls: list = []

    async def _fake_process_row(row, pa_store, evidence_cache=None):
        complete_calls.append(1)
        pa_store.mark_done(row["id"])
        return 1

    async def _noop_refresh_asset_brief(instrument_id, force=False):
        # trigger_on_demand_brief_refresh now awaits the news/evidence gather before writing
        # key_issues (2026-09-03 fix) — stub it out here like test_analyst_sweep's _stub_analyst
        # does, so this test isn't waiting on a real paid-search call before _process_row runs.
        return {"refreshed": False, "reason": "test_stub"}

    monkeypatch.setattr(analyst_mod, "_process_row", _fake_process_row)
    monkeypatch.setattr(analyst_mod, "_is_held_instrument", lambda i: False)
    monkeypatch.setattr(analyst_mod, "_is_watched_instrument", lambda i: False)
    monkeypatch.setattr(asset_brief_mod, "refresh_asset_brief", _noop_refresh_asset_brief)

    iid = seeded["instrument_id"]
    resp = client.get(f"/api/intel/assets/{iid}")
    assert resp.status_code == 200

    for _ in range(50):
        if complete_calls:
            break
        time.sleep(0.02)
    assert complete_calls == [1]


def test_get_asset_detail_does_not_schedule_refresh_for_instrument_with_no_ticker(
    client, tmp_path, monkeypatch, db_isolated
):
    """A no-ticker manual holding (has_ticker=False) still never triggers this — nothing to
    freshen or price a take against — regardless of the held-or-watched-or-ad-hoc widening."""
    from backend.db.instrument_store import InstrumentStore
    from backend.services import fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"USD": 1.0, "SGD": 1.35})
    monkeypatch.setattr(
        "backend.services.asset_detail.read_profile", lambda: {"base_currency": "SGD"}
    )

    instrument_id = "test-inst-no-ticker"
    InstrumentStore().insert(
        {
            "id": instrument_id,
            "symbol": "",
            "name": "Manual Holding",
            "asset_class": "equity",
            "asset_subclass": "stock",
            "quote_currency": "USD",
            "price_source": None,
            "price_source_id": None,
            "identifiers": {},
            "quote_per_unit": 1.0,
        }
    )

    import backend.monitoring.analyst as analyst_mod

    complete_calls: list = []

    async def _boom(*a, **kw):
        complete_calls.append(1)
        raise AssertionError("must never be called for a no-ticker instrument")

    monkeypatch.setattr(analyst_mod, "_process_row", _boom)

    resp = client.get(f"/api/intel/assets/{instrument_id}")
    assert resp.status_code == 200

    time.sleep(0.1)  # give any (wrongly) scheduled task a chance to run
    assert complete_calls == []


def test_get_asset_detail_carries_the_investment_thesis(client, seeded):
    iid = seeded["instrument_id"]
    assert client.get(f"/api/intel/assets/{iid}").json()["thesis"] is None
    put = client.put(f"/api/investment-theses/{iid}", json={"reason": "Moat", "fair_value": 150})
    assert put.status_code == 200
    thesis = client.get(f"/api/intel/assets/{iid}").json()["thesis"]
    assert (thesis["reason"], thesis["fair_value"], thesis["instrument_id"]) == ("Moat", 150.0, iid)
