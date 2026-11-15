"""Shared fixtures for the paper-trading tests: an isolated database with the flag on, a few
synthetic instruments (ACME and friends — never a real holding), and the deterministic QA
market every paper test prices from."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.db.instrument_store import InstrumentStore
from backend.services.paper_trading import qa_quotes

INSTRUMENTS = (
    {
        "id": "inst_acme",
        "symbol": "ACME",
        "name": "Acme Corp",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "price_source": "yahoo",
        "price_source_id": "ACME",
        "quote_currency": "USD",
    },
    {
        "id": "inst_acmej",
        "symbol": "9999.T",
        "name": "Acme Japan",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "price_source": "yahoo",
        "price_source_id": "9999.T",
        "quote_currency": "JPY",
    },
    {
        "id": "inst_coin",
        "symbol": "ACOIN",
        "name": "Acme Coin",
        "asset_class": "crypto",
        "asset_subclass": "coin",
        "price_source": "coingecko",
        "price_source_id": "acme-coin",
        "quote_currency": "USD",
    },
    {
        "id": "inst_fund",
        "symbol": "ACMFX",
        "name": "Acme Index Fund",
        "asset_class": "equity",
        "asset_subclass": "mutual_fund",
        "price_source": "yahoo",
        "price_source_id": "ACMFX",
        "quote_currency": "USD",
    },
    {
        "id": "inst_gold",
        "symbol": "GC=F",
        "name": "Gold (spot)",
        "asset_class": "commodity",
        "asset_subclass": "precious_metal",
        "price_source": "yahoo",
        "price_source_id": "GC=F",
        "quote_currency": "USD",
        "unit": "g",
        "quote_per_unit": 1 / 31.1034768,
    },
    {
        "id": "inst_idx",
        "symbol": "^GSPC",
        "name": "S&P 500",
        "asset_class": "equity",
        "asset_subclass": "index",
        "price_source": "yahoo",
        "price_source_id": "^GSPC",
        "quote_currency": "USD",
    },
)

# A Wednesday mid-session in New York: every market is open, whatever day the suite runs.
NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)

PRICES = {
    "ACME": 100.0,
    "9999.T": 2000.0,
    "acme-coin": 50.0,
    "ACMFX": 40.0,
    "GC=F": 2400.0,
    "^GSPC": 5000.0,
}


def seed_instruments() -> None:
    store = InstrumentStore()
    for inst in INSTRUMENTS:
        if not store.find_by_id(inst["id"]):
            store.insert(dict(inst))


@pytest.fixture
def paper_db(db_isolated, monkeypatch):
    """An isolated book with synthetic instruments and an open QA market at fixed prices."""
    from types import SimpleNamespace

    from backend.services.paper_trading import quotes
    from tests.flags import flags

    qa = lambda: SimpleNamespace(onw_qa_mode=True)  # noqa: E731
    monkeypatch.setattr(qa_quotes, "get_settings", qa)
    monkeypatch.setattr(quotes, "get_settings", qa)
    from backend.services.paper_trading import engine, service

    monkeypatch.setattr(service, "_now", lambda: NOW)
    monkeypatch.setattr(engine, "_now", lambda: NOW)
    seed_instruments()
    monkeypatch.setattr(
        qa_quotes, "_state", {"market": "open", "prices": {}, "fx": {}, "opens_at": None}
    )
    qa_quotes.set_state("open", PRICES, {"JPYUSD": 0.0067, "EURUSD": 1.08, "USDEUR": 0.925})
    with flags(paper_trading=True):
        yield


@pytest.fixture
def paper_client(paper_db):
    from backend.api.paper_trading import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)
