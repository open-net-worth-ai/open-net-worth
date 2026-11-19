"""
API smoke tests for the period-returns read router (P2-INV3).

Offline + isolated, mirroring tests/test_lots_api.py: a minimal FastAPI app mounts only
the returns router. One client patches the SERVICE entrypoint to a canned payload (pure
endpoint contract: status + shape + enum validation), another drives the REAL service with
the stores + history monkeypatched into tmp_path (empty data → 200 with nulls, never 500,
mirroring /api/networth/history).

Run:
    .venv/bin/python3 -m pytest tests/test_returns_api.py -q
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.returns import router


@pytest.fixture
def client(monkeypatch):
    """Client whose route calls a CANNED service payload (the endpoint-contract smoke).

    Patches portfolio_returns_with_benchmark (the actual route entrypoint) so no store,
    portfolio_history, or FX path is exercised — this is a pure endpoint-contract test.
    """
    from backend.services import portfolio_history as ph
    from backend.services import returns as returns_service

    captured = {}

    def fake_portfolio_returns_with_benchmark(period="1Y", benchmark=None, today=None, band=None):
        captured["period"] = period
        captured["band"] = band
        return {
            "period": period,
            "start": "2025-06-05",
            "end": "2026-06-05",
            "effective_start": "2025-06-05",
            "label": "Last 1 year",
            "annualized": True,
            "base_currency": "SGD",
            "twr": {"period_return": 0.142, "annualized": 0.142},
            "mwr": {"xirr": 0.118},
            "period_gain_base": 12450.0,
            "start_value": 88000.0,
            "end_value": 100450.0,
            "series": [],
            "data_quality": {
                "market_since": "2025-09-01",
                "inception": "2025-01-01",
                "effective_start": "2025-06-05",
                "value_points": 9,
                "sufficient": True,
                "has_unknown_cost": False,
                "has_unrecorded_risk": False,
                "fx_approximate": False,
                "tiers": {},
            },
        }

    monkeypatch.setattr(
        returns_service,
        "portfolio_returns_with_benchmark",
        fake_portfolio_returns_with_benchmark,
    )
    # Mirror real_client: isolate the portfolio_history primitive so no live data is read
    # even if the mock is bypassed or the service is called through another path.
    empty_ph = {
        "series": [],
        "data_quality": {
            "market_since": None,
            "inception": None,
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }
    monkeypatch.setattr(ph, "portfolio_value_series", lambda *a, **k: empty_ph)
    monkeypatch.setattr(ph, "inception_date", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), captured


def test_returns_default_period_is_1y(client):
    c, captured = client
    r = c.get("/api/returns")
    assert r.status_code == 200
    assert captured["period"] == "1Y"
    body = r.json()
    assert {
        "period",
        "start",
        "end",
        "base_currency",
        "twr",
        "mwr",
        "period_gain_base",
        "start_value",
        "end_value",
        "data_quality",
    } <= set(body)


@pytest.mark.parametrize("period", ["7D", "1M", "3M", "YTD", "1Y", "ALL"])
def test_returns_each_period_enum_200(client, period):
    c, captured = client
    r = c.get("/api/returns", params={"period": period})
    assert r.status_code == 200
    assert captured["period"] == period
    assert r.json()["period"] == period


def test_returns_bad_period_422(client):
    c, _ = client
    r = c.get("/api/returns", params={"period": "5Y"})
    assert r.status_code == 422


# ── Real-service path (empty data → 200 with nulls, never 500) ───────────────────


@pytest.fixture
def real_client(tmp_path, monkeypatch):
    """Client over the REAL service with all stores + portfolio_history + FX isolated.

    Phase 4: portfolio_returns now sources its value series from
    portfolio_history.portfolio_value_series (not ledger_service.net_worth_history).
    We patch the primitive directly so the empty-data path yields no series points.
    """
    from backend.db import price_history_store
    from backend.services import portfolio_history as ph
    from backend.services import returns as returns_service

    monkeypatch.setattr(returns_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(returns_service, "_load_rates_safe", lambda loader: {"USD": 1.0})
    # Empty primitive → no value points → sufficient=False.
    empty_ph = {
        "series": [],
        "data_quality": {
            "market_since": None,
            "inception": None,
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }
    monkeypatch.setattr(ph, "portfolio_value_series", lambda *a, **k: empty_ph)
    monkeypatch.setattr(ph, "inception_date", lambda *a, **k: None)
    monkeypatch.setattr(price_history_store.PriceHistoryStore, "earliest_date", lambda self: None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_returns_empty_data_200_with_nulls(real_client):
    r = real_client.get("/api/returns", params={"period": "1Y"})
    assert r.status_code == 200
    body = r.json()
    assert body["twr"] is None
    assert body["mwr"] is None
    assert body["data_quality"]["sufficient"] is False


# ── Phase 4: 7D period, payload shape, effective_start, series ───────────────────


def test_returns_7d_period_accepted_200(real_client):
    # Phase 4: 7D is now a valid period enum value → 200 with the standard payload shape.
    r = real_client.get("/api/returns", params={"period": "7D"})
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "7D"
    # Even with no history the payload must contain the new Phase 4 fields.
    assert "effective_start" in body
    assert "label" in body
    assert "annualized" in body
    assert "series" in body
    # 7D is a 7-day window → far below 360d → never annualized.
    assert body["annualized"] is False
    # Empty data → sufficient=False, twr/mwr=None.
    assert body["data_quality"]["sufficient"] is False
    # data_quality carries the full Phase 4 keys.
    dq = body["data_quality"]
    assert "inception" in dq
    assert "effective_start" in dq
    assert "has_unrecorded_risk" in dq
    assert "fx_approximate" in dq
    assert "tiers" in dq


# ── Benchmark comparison (?benchmark=) — P2-INV4 ─────────────────────────────────


@pytest.fixture
def bench_client(tmp_path, monkeypatch, db_isolated):
    """Real-service client with a seeded benchmark instrument + price_history, so
    GET /api/returns?benchmark= flows through the real comparison path (offline).
    PriceHistoryStore resolves against the per-test isolated database (db_isolated).

    Phase 4: portfolio side is injected via portfolio_history.portfolio_value_series instead
    of ledger_service.net_worth_history.

    The service clock is PINNED to 2026-06-05 (the day this fixture's absolute price dates
    were authored around): the route has no `today` param, so an un-pinned date.today()
    lets the 1M window drift past the seeded 2026-06-05 price and the benchmark series
    goes flat → TWR 0.0 (went red on 2026-07-05).
    """
    from backend.db.instrument_store import InstrumentStore
    from backend.services import portfolio_history as ph
    from backend.services import returns as returns_service

    monkeypatch.setattr(returns_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(returns_service, "_load_rates_safe", lambda loader: {"USD": 1.0})
    # Portfolio side: two in-window value points so portfolio TWR is sufficient (+20%).
    # Phase 4: inject via portfolio_history.portfolio_value_series.
    ph_result = {
        "series": [
            {"date": "2026-05-05", "value_base": 100.0, "classes": {"equity": 100.0}},
            {"date": "2026-06-05", "value_base": 120.0, "classes": {"equity": 120.0}},
        ],
        "data_quality": {
            "market_since": "2025-01-01",
            "inception": "2026-05-05",
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }
    monkeypatch.setattr(ph, "portfolio_value_series", lambda *a, **k: ph_result)
    monkeypatch.setattr(ph, "inception_date", lambda *a, **k: "2026-05-05")

    real_with_benchmark = returns_service.portfolio_returns_with_benchmark

    def pinned_with_benchmark(period="1Y", benchmark=None, today=None, band=None):
        return real_with_benchmark(period, benchmark=benchmark, today=date(2026, 6, 5), band=band)

    monkeypatch.setattr(returns_service, "portfolio_returns_with_benchmark", pinned_with_benchmark)
    InstrumentStore().bulk_insert(
        [
            {
                "id": "I-SPY",
                "symbol": "SPY",
                "name": "SPDR S&P 500",
                "price_source": "yahoo",
                "price_source_id": "SPY",
            }
        ]
    )
    from backend.db.engine import get_engine
    from backend.db.price_history_io import import_price_history

    with get_engine().begin() as conn:
        import_price_history(
            conn,
            {
                "SPY": [
                    {"date": "2025-01-01", "price": 80.0},
                    {"date": "2026-05-05", "price": 100.0},
                    {"date": "2026-06-05", "price": 110.0},  # benchmark +10%
                ]
            },
        )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_returns_with_benchmark_includes_comparison(bench_client):
    r = bench_client.get("/api/returns", params={"period": "1M", "benchmark": "I-SPY"})
    assert r.status_code == 200
    body = r.json()
    comp = body["comparison"]
    assert comp is not None
    assert comp["benchmark"]["symbol"] == "SPY"
    assert comp["benchmark_twr"] == pytest.approx(0.10)
    assert comp["portfolio_twr"] == pytest.approx(0.20)
    assert comp["excess_return"] == pytest.approx(0.10)  # 0.20 - 0.10


def test_returns_with_benchmark_by_symbol(bench_client):
    r = bench_client.get("/api/returns", params={"period": "1M", "benchmark": "spy"})
    assert r.status_code == 200
    assert r.json()["comparison"]["benchmark"]["instrument_id"] == "I-SPY"


def test_returns_unresolvable_benchmark_yields_null_comparison(bench_client):
    r = bench_client.get("/api/returns", params={"period": "1M", "benchmark": "NOPE"})
    assert r.status_code == 200
    assert r.json()["comparison"] is None  # never 500, never fabricated


def test_returns_no_benchmark_omits_comparison(bench_client):
    r = bench_client.get("/api/returns", params={"period": "1M"})
    assert r.status_code == 200
    assert "comparison" not in r.json()


# ── Risk metrics (sys-risk-metrics-engine) ────────────────────────────────────────


def test_get_benchmarks_200(real_client):
    """GET /api/returns/benchmarks → 200, exactly 6 roster entries."""
    r = real_client.get("/api/returns/benchmarks")
    assert r.status_code == 200
    body = r.json()
    assert "benchmarks" in body
    entries = body["benchmarks"]
    assert len(entries) == 6
    keys = {e["key"] for e in entries}
    assert keys == {"SPY", "VWRA", "QQQ", "ACWI", "BTC", ""}
    for e in entries:
        assert "key" in e
        assert "label" in e
        assert "symbol" in e


def test_get_benchmarks_includes_none_option(real_client):
    """The empty-key 'none' option must be present."""
    r = real_client.get("/api/returns/benchmarks")
    entries = r.json()["benchmarks"]
    none_entry = next((e for e in entries if e["key"] == ""), None)
    assert none_entry is not None


def test_returns_risk_false_omits_risk_key(real_client):
    """No risk param (or risk=false) → `risk` key absent — existing callers unaffected."""
    r = real_client.get("/api/returns", params={"period": "1Y"})
    assert r.status_code == 200
    assert "risk" not in r.json()

    r2 = real_client.get("/api/returns", params={"period": "1Y", "risk": "false"})
    assert r2.status_code == 200
    assert "risk" not in r2.json()


def test_returns_risk_true_empty_book_200_with_null(real_client):
    """risk=true on empty book → 200, risk key present (null or null-filled), never 500."""
    r = real_client.get("/api/returns", params={"period": "1Y", "risk": "true"})
    assert r.status_code == 200
    body = r.json()
    assert "risk" in body
    # Empty data → risk is None or a null-filled block — never 500.
    # Either None (exception caught) or a dict with null metrics is acceptable.
    if body["risk"] is not None:
        risk_block = body["risk"]
        assert "data_quality" in risk_block
        assert risk_block["data_quality"]["sufficient"] is False


def test_returns_risk_true_shape(client):
    """risk=true → risk block has all required fields when service is mocked."""
    from backend.services import risk_metrics

    c, captured = client

    def fake_portfolio_risk(period="1Y", benchmark=None, **kwargs):
        return {
            "period": period,
            "volatility": 0.184,
            "beta_vs_spy": 1.07,
            "sharpe": 0.92,
            "max_drawdown": -0.231,
            "summary": "solid return for the risk taken · swings ~7% more than the market · worst dip ~−23%",
            "benchmark": {
                "key": "SPY",
                "symbol": "SPY",
                "instrument_id": "I-SPY",
                "resolved": True,
            },
            "annualization_factor": 365,
            "risk_free_rate": 0.035,
            "data_quality": {
                "value_points": 252,
                "sufficient": True,
                "spy_anchored": True,
                "benchmark_resolved": True,
                "fx_approximate": False,
                "has_unrecorded_risk": False,
                "income_overlay": True,
            },
        }

    original = risk_metrics.portfolio_risk
    risk_metrics.portfolio_risk = fake_portfolio_risk
    try:
        r = c.get("/api/returns", params={"period": "1Y", "risk": "true"})
    finally:
        risk_metrics.portfolio_risk = original

    assert r.status_code == 200
    body = r.json()
    assert "risk" in body
    risk = body["risk"]
    assert risk is not None
    assert "volatility" in risk
    assert "beta_vs_spy" in risk
    assert "sharpe" in risk
    assert "max_drawdown" in risk
    assert "summary" in risk
    assert "benchmark" in risk
    assert "data_quality" in risk
    dq = risk["data_quality"]
    assert "value_points" in dq
    assert "sufficient" in dq
    assert "spy_anchored" in dq
    assert "benchmark_resolved" in dq


def test_returns_roster_key_resolves_for_benchmark(bench_client):
    """Passing a roster key ('SPY') as benchmark should resolve to the SPY instrument."""
    r = bench_client.get("/api/returns", params={"period": "1M", "benchmark": "SPY"})
    assert r.status_code == 200
    body = r.json()
    # The comparison should resolve SPY (we have SPY in bench_client fixture instruments).
    assert "comparison" in body
    assert body["comparison"] is not None
    assert body["comparison"]["benchmark"]["symbol"] == "SPY"


def test_returns_risk_true_bad_period_422(real_client):
    """Bad period with risk=true still → 422 (enum validation)."""
    r = real_client.get("/api/returns", params={"period": "5Y", "risk": "true"})
    assert r.status_code == 422
