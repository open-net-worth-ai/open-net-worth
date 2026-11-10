"""get_lots by ticker, and an error that names what the model got wrong.

Scar (2026-11-01): Lucius called ``get_lots {"ticker": "ACME"}``. Dispatch validates types
only, so the unknown argument reached the handler, which answered a bare "position_id is
required" — and the model abandoned the call instead of fixing it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.services import lots as svc


def call(tool, **inp):
    text, _ = asyncio.run(execute_tool(tool, inp, tool_use_id="toolu_test"))
    return json.loads(text)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    return tmp_path


def _holding(symbol: str, account_id: str, qty: float = 10) -> dict:
    inst = next(
        (i for i in InstrumentStore().all() if i.get("symbol") == symbol), None
    ) or InstrumentStore().create(
        {
            "symbol": symbol,
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source_id": symbol,
        }
    )
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": account_id,
            "quantity": 0,
            "cost_currency": "USD",
        }
    )
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": qty,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        }
    )
    return pos


def test_a_ticker_held_once_returns_that_positions_lots(isolated):
    pos = _holding("ACME", "acc-1")
    _holding("OTHR", "acc-1")

    res = call("get_lots", ticker="acme")

    assert res["position_id"] == pos["id"]
    assert len(res["lots"]) == 1


def test_a_ticker_held_at_two_custodians_returns_both(isolated):
    a = _holding("ACME", "acc-1", qty=4)
    b = _holding("ACME", "acc-2", qty=6)

    res = call("get_lots", ticker="ACME")

    assert {r["position_id"] for r in res["results"]} == {a["id"], b["id"]}


def test_a_ticker_not_held_says_so(isolated):
    res = call("get_lots", ticker="NOPE")

    assert "no position holds ticker 'NOPE'" in res["error"]


def test_an_unknown_argument_is_named_in_the_error(isolated):
    res = call("get_lots", symbol="ACME")

    assert "position_id is required" in res["error"]
    assert "not a get_lots argument: symbol" in res["error"]
