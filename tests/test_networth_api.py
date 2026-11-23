"""
API smoke tests for the period-scoped net-worth-history router.

Offline + isolated, mirroring tests/test_returns_api.py: a minimal FastAPI app mounts only
the networth_api router and patches `ledger.net_worth_history` to a canned series, so the
test exercises the route's period-window filter (shape + period echo + 1M/3M/YTD/1Y/ALL +
empty book → [] never 500) without touching live data.

Run:
    .venv/bin/python3 -m pytest tests/test_networth_api.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.networth_api import router


def _canned_history():
    # One point per year-end across several years, plus a recent point — enough to make
    # the period window meaningfully cut the older points.
    return [
        {
            "date": d,
            "classes": {"cash": 100.0},
            "liabilities": 0.0,
            "accounts": 100.0,
            "net_worth": 100.0,
            "market_tracked": False,
            "fx_approximate": False,
        }
        for d in ("2022-12-31", "2024-12-31", "2026-01-31", "2026-06-01")
    ]


@pytest.fixture
def client(monkeypatch):
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(ledger_service, "net_worth_history", _canned_history)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_default_period_is_1y(client):
    r = client.get("/api/net-worth/history")
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "1Y"
    assert body["base_currency"] == "SGD"
    assert isinstance(body["history"], list)
    # 1Y window drops the 2022/2024 points, keeps the two 2026 points.
    assert {p["date"] for p in body["history"]} == {"2026-01-31", "2026-06-01"}
    # Point shape is the canonical net-worth-history projection.
    assert {
        "date",
        "classes",
        "liabilities",
        "accounts",
        "net_worth",
        "market_tracked",
        "fx_approximate",  # MATH-4: surfaced on every point, not dropped
    } <= set(body["history"][0])


def test_all_returns_full_series_unfiltered(client):
    r = client.get("/api/net-worth/history", params={"period": "ALL"})
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "ALL"
    assert len(body["history"]) == 4  # every point


@pytest.mark.parametrize("period", ["1M", "3M", "YTD", "1Y", "ALL"])
def test_each_period_enum_200(client, period):
    r = client.get("/api/net-worth/history", params={"period": period})
    assert r.status_code == 200
    assert r.json()["period"] == period


def test_period_window_is_inclusive_and_monotone(client):
    """A narrower window is a subset of a wider one (1M ⊆ 3M ⊆ 1Y ⊆ ALL)."""
    sizes = {
        p: len(client.get("/api/net-worth/history", params={"period": p}).json()["history"])
        for p in ("1M", "3M", "1Y", "ALL")
    }
    assert sizes["1M"] <= sizes["3M"] <= sizes["1Y"] <= sizes["ALL"]


def test_unknown_period_falls_back_to_1y(client):
    r = client.get("/api/net-worth/history", params={"period": "10Y"})
    assert r.status_code == 200  # never 422 — defensive fallback, not an enum gate
    assert r.json()["period"] == "1Y"


def test_lowercase_period_normalized(client):
    r = client.get("/api/net-worth/history", params={"period": "all"})
    assert r.status_code == 200
    assert r.json()["period"] == "ALL"
    assert len(r.json()["history"]) == 4


# ── Empty / young book → [] (never 500) ──────────────────────────────────────────


@pytest.fixture
def empty_client(monkeypatch):
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(ledger_service, "net_worth_history", lambda: [])
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("period", ["1M", "3M", "YTD", "1Y", "ALL"])
def test_empty_book_returns_empty_history(empty_client, period):
    r = empty_client.get("/api/net-worth/history", params={"period": period})
    assert r.status_code == 200
    body = r.json()
    assert body["history"] == []
    assert body["base_currency"] == "USD"
    assert body["period"] == period
