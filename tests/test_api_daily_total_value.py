"""
API smoke tests for the daily total-value router (sys-daily-series lane).

Mirrors test_returns_api.py in structure: one client patches the SERVICE to a canned
payload (pure endpoint-contract smoke); another drives the REAL service with stores
monkeypatched into tmp_path (empty data → 200, series:[], has_real_daily_history:false,
never 500).

Run:
    .venv/bin/python3 -m pytest tests/test_api_daily_total_value.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.daily_total_value import router

# ── Canned-service client (endpoint-contract smoke) ────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """Client whose calls hit a CANNED service payload — pure endpoint-contract smoke."""
    from backend.services import daily_total_value as dtv

    def fake_total_value_series(start, end, **kwargs):
        return {
            "base_currency": "SGD",
            "grain": "daily",
            "start": start,
            "end": end,
            "series": [
                {
                    "date": start,
                    "value_base": 100000.0,
                    "income_cum_base": 0.0,
                    "tr_index": 1.0,
                    "daily_return": None,
                    "classes": {"equity": 100000.0},
                }
            ],
            "data_quality": {
                "market_since": "2025-01-01",
                "inception": "2025-01-01",
                "value_points": 2,
                "sufficient": True,
                "has_real_daily_history": True,
                "fx_approximate": False,
                "has_unrecorded_risk": False,
                "tiers": {},
            },
        }

    def fake_sparkline_series(days=30, **kwargs):
        return {
            "base_currency": "SGD",
            "days": days,
            "series": [{"date": "2026-05-22", "value_base": 100000.0, "income_cum_base": 0.0}],
            "data_quality": {"has_real_daily_history": True, "value_points": 1},
        }

    monkeypatch.setattr(dtv, "total_value_series", fake_total_value_series)
    monkeypatch.setattr(dtv, "sparkline_series", fake_sparkline_series)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_total_value_default_period_200(client):
    r = client.get("/api/portfolio/total-value")
    assert r.status_code == 200
    body = r.json()
    assert {"base_currency", "grain", "start", "end", "series", "data_quality"} <= set(body)


@pytest.mark.parametrize("period", ["7D", "1M", "3M", "YTD", "1Y", "ALL"])
def test_total_value_each_period_200(client, period):
    r = client.get("/api/portfolio/total-value", params={"period": period})
    assert r.status_code == 200


def test_total_value_bad_period_422(client):
    r = client.get("/api/portfolio/total-value", params={"period": "10Y"})
    assert r.status_code == 422


def test_sparkline_default_200(client):
    r = client.get("/api/portfolio/sparkline")
    assert r.status_code == 200
    body = r.json()
    assert "series" in body
    assert "data_quality" in body


def test_sparkline_custom_days_200(client):
    r = client.get("/api/portfolio/sparkline", params={"days": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["days"] == 7


def test_sparkline_days_out_of_range_422(client):
    r = client.get("/api/portfolio/sparkline", params={"days": 0})
    assert r.status_code == 422
    r = client.get("/api/portfolio/sparkline", params={"days": 366})
    assert r.status_code == 422


# ── Real-service path (empty data → 200, never 500) ──────────────────────────


@pytest.fixture
def real_client(tmp_path, monkeypatch, db_isolated):
    """Real-service client with all stores + portfolio_history + FX isolated to tmp_path.
    PriceHistoryStore resolves against the per-test isolated database (db_isolated)."""
    from backend.db import price_history_store
    from backend.services import daily_total_value as dtv
    from backend.services import portfolio_history as ph

    # income_events is DB-backed now (plan 4a task 3) — db_isolated covers it.
    monkeypatch.setattr(dtv, "base_currency", lambda: "USD")
    monkeypatch.setattr(dtv, "_load_rates", lambda: {"USD": 1.0})

    # Empty primitive → no value points → has_real_daily_history=False.
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
    monkeypatch.setattr(price_history_store.PriceHistoryStore, "earliest_date", lambda self: None)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_real_total_value_empty_data_200(real_client):
    r = real_client.get("/api/portfolio/total-value")
    assert r.status_code == 200
    body = r.json()
    assert body["series"] == []
    assert body["data_quality"]["has_real_daily_history"] is False
    assert body["data_quality"]["sufficient"] is False


def test_real_sparkline_empty_data_200(real_client):
    r = real_client.get("/api/portfolio/sparkline")
    assert r.status_code == 200
    body = r.json()
    assert body["series"] == []
    assert body["data_quality"]["has_real_daily_history"] is False


# ── Import smoke ───────────────────────────────────────────────────────────────


def test_import_smoke():
    """backend.main imports without error — the new router is registered."""
    import backend.main  # noqa: F401

    assert True
