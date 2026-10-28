"""Holding plans — the store's rules, the one write choke, REST, the watchlist ``intent``
compatibility path, the watch readers' view and the Lucius tools."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import execute_tool
from backend.core.errors import NotFoundError, ValidationError
from backend.db.holding_plan_store import HoldingPlanStore, validate_plan
from backend.services import holding_plans


@pytest.fixture
def inst(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    row = {
        "id": "inst_acme",
        "symbol": "ACME",
        "name": "Acme Semiconductor",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "ACME",
        "identifiers": {},
        "quote_per_unit": 1.0,
    }
    InstrumentStore().insert(row)
    return row


@pytest.fixture
def client(inst):
    from backend.api.holding_plans import router as plans_router
    from backend.api.watchlist import router as wl_router

    app = FastAPI()
    app.include_router(plans_router)
    app.include_router(wl_router)
    return TestClient(app)


def _set(fields: dict, clear: list | None = None, iid: str = "inst_acme") -> dict | None:
    change = {"op": "set", "instrument_id": iid, "fields": fields, "clear": clear or []}
    return holding_plans.apply_plan_change(change)["plan"]


def call(tool, **inp):
    text, _ = asyncio.run(execute_tool(tool, inp))
    return json.loads(text)


# ── the store's rules ───────────────────────────────────────────────────────────────


def test_validate_normalizes_and_drops_blanks():
    out = validate_plan(
        {"role": "core", "thesis": "  Own   the market  ", "exit_if": "", "fair_value": "120"}
    )
    assert out == {"role": "core", "thesis": "Own the market", "fair_value": 120.0}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"role": "growth"}, "Role must be one of"),
        ({"thesis": "x" * 281}, "at most 280"),
        ({"stop_below": -1}, "positive price"),
        ({"fair_value": "lots"}, "must be a number"),
        ({"buy_above": 70}, "needs a top"),
        ({"buy_below": 70, "buy_above": 90}, "floor must be below"),
        ({"stop_below": 200, "trim_above": 150}, "stop must be below"),
        ({"colour": "red"}, "Unknown plan field"),
    ],
)
def test_validate_refuses_bad_plans(fields, message):
    with pytest.raises(ValidationError, match=message):
        validate_plan(fields)


# ── the write choke ─────────────────────────────────────────────────────────────────


def test_set_creates_then_merges(inst):
    first = _set({"thesis": "Share gains", "role": "satellite", "stop_below": 120})
    assert first["id"].startswith("hp_") and first["instrument_id"] == "inst_acme"
    second = _set({"fair_value": 190})
    assert second["id"] == first["id"]
    assert (second["thesis"], second["role"], second["stop_below"], second["fair_value"]) == (
        "Share gains",
        "satellite",
        120.0,
        190.0,
    )
    assert len(HoldingPlanStore().all()) == 1


def test_clear_blanks_fields_and_an_empty_plan_is_deleted(inst):
    _set({"thesis": "Share gains", "stop_below": 120})
    assert _set({}, clear=["stop_below"])["stop_below"] is None
    assert _set({}, clear=["thesis"]) is None
    assert HoldingPlanStore().all() == []


def test_validation_runs_on_the_merged_plan(inst):
    _set({"buy_below": 150})
    with pytest.raises(ValidationError, match="floor must be below"):
        _set({"buy_above": 160})
    assert holding_plans.plan_for("inst_acme")["buy_above"] is None


def test_delete_and_unknown_instrument(inst):
    _set({"thesis": "x"})
    assert holding_plans.apply_plan_change({"op": "delete", "instrument_id": "inst_acme"}) == {
        "plan": None
    }
    assert holding_plans.plan_for("inst_acme") is None
    with pytest.raises(NotFoundError):
        _set({"thesis": "x"}, iid="inst_missing")


def test_list_plans_joins_ticker_and_held(inst):
    from backend.db.position_store import PositionStore

    _set({"thesis": "x"})
    assert holding_plans.list_plans()[0]["held"] is False
    PositionStore().insert(
        {"id": "pos_1", "instrument_id": "inst_acme", "account_id": "acc_1", "quantity": 3}
    )
    plans = holding_plans.list_plans(["acme"])
    assert [(p["ticker"], p["held"]) for p in plans] == [("ACME", True)]
    assert holding_plans.list_plans(["OTHER"]) == []


# ── REST ────────────────────────────────────────────────────────────────────────────


def test_put_replaces_the_whole_plan(client):
    res = client.put("/api/holding-plans/inst_acme", json={"thesis": "A", "stop_below": 100})
    assert res.status_code == 200 and res.json()["plan"]["stop_below"] == 100.0
    res = client.put("/api/holding-plans/inst_acme", json={"thesis": "B"})
    assert res.json()["plan"]["thesis"] == "B" and res.json()["plan"]["stop_below"] is None
    listed = client.get("/api/holding-plans").json()["plans"]
    assert [(p["ticker"], p["thesis"]) for p in listed] == [("ACME", "B")]


def test_put_refuses_a_bad_plan_with_its_reason(client):
    res = client.put("/api/holding-plans/inst_acme", json={"buy_below": 70, "buy_above": 90})
    assert res.status_code == 422
    assert "floor must be below" in res.json()["detail"]
    assert client.put("/api/holding-plans/nope", json={"thesis": "x"}).status_code == 404


def test_delete_route(client):
    client.put("/api/holding-plans/inst_acme", json={"thesis": "A"})
    assert client.delete("/api/holding-plans/inst_acme").json() == {"plan": None}
    assert client.get("/api/holding-plans").json()["plans"] == []


# ── the watchlist ``intent`` compatibility path ─────────────────────────────────────


def _watch_acme(client) -> str:
    """Inserted straight into the store: adding through POST resolves the ticker online."""
    from backend.services.watchlist import watchlist_store

    store = watchlist_store()
    wid = store.create_watchlist("Chips")["id"]
    store.insert({"id": "wli_acme", "type": "ticker", "value": "ACME", "watchlist_ids": [wid]})
    return "wli_acme"


def test_watch_intent_writes_the_plan_and_leaves_held_fields_alone(client):
    _set({"role": "core", "exit_if": "Margins fall"})
    item_id = _watch_acme(client)
    res = client.patch(
        f"/api/watchlist/items/{item_id}", json={"intent": {"thesis": "Cheap", "buy_below": 90}}
    )
    assert res.status_code == 200
    assert res.json()["intent"] == {"thesis": "Cheap", "buy_below": 90.0}
    plan = holding_plans.plan_for("inst_acme")
    assert (plan["thesis"], plan["buy_below"], plan["role"], plan["exit_if"]) == (
        "Cheap",
        90.0,
        "core",
        "Margins fall",
    )
    from backend.services.watchlist import watchlist_store

    assert not watchlist_store().find_by_id(item_id).get("intent")


def test_watch_intent_refusal_names_intent(client):
    item_id = _watch_acme(client)
    res = client.patch(
        f"/api/watchlist/items/{item_id}", json={"intent": {"buy_below": 70, "buy_above": 90}}
    )
    assert res.status_code == 422 and "intent" in res.json()["detail"]


def test_a_plan_written_elsewhere_shows_as_the_items_intent(client):
    item_id = _watch_acme(client)
    _set({"thesis": "From the asset page", "fair_value": 150, "stop_below": 100})
    item = call("get_watchlist", values=["ACME"])["items"][0]
    assert item["id"] == item_id
    assert item["intent"] == {"thesis": "From the asset page", "fair_value": 150.0}


def test_intent_for_item_falls_back_to_a_legacy_unmatched_intent(db_isolated):
    legacy = {"type": "ticker", "value": "ZZZQ", "intent": {"buy_below": 5.0}}
    assert holding_plans.intent_for_item(legacy, {}) == {"buy_below": 5.0}
    assert holding_plans.intent_for_item({"type": "topic", "value": "AI"}, {}) is None


# ── Lucius tools ────────────────────────────────────────────────────────────────────


def test_set_get_delete_tools_round_trip(inst):
    res = call(
        "set_holding_plans",
        updates=[
            {"ticker": "ACME", "role": "satellite", "thesis": "Share gains", "trim_above": 220}
        ],
    )
    assert res["results"][0]["ok"] is True and res["results"][0]["plan"]["role"] == "satellite"
    res = call("set_holding_plans", updates=[{"ticker": "ACME", "clear": ["trim_above"]}])
    assert res["results"][0]["plan"]["trim_above"] is None
    got = call("get_holding_plans", tickers=["ACME"])
    assert got["count"] == 1 and got["plans"][0]["thesis"] == "Share gains"
    assert call("delete_holding_plans", tickers=["ACME"])["results"][0]["ok"] is True
    assert call("get_holding_plans")["count"] == 0


def test_set_tool_reports_a_bad_entry_without_failing_the_others(inst):
    res = call(
        "set_holding_plans",
        updates=[
            {"ticker": "ACME", "stop_below": 200, "trim_above": 100},
            {"ticker": "ACME", "thesis": "fine"},
        ],
    )
    assert [r["ok"] for r in res["results"]] == [False, True]
    assert "stop must be below" in res["results"][0]["error"]


# ── review follow-ups ───────────────────────────────────────────────────────────────


def test_get_watchlist_route_shows_the_plan_backed_intent(client):
    item_id = _watch_acme(client)
    client.patch(
        f"/api/watchlist/items/{item_id}", json={"intent": {"thesis": "T", "buy_below": 90}}
    )
    items = {i["id"]: i for i in client.get("/api/watchlist").json()["items"]}
    assert items[item_id]["intent"] == {"thesis": "T", "buy_below": 90.0}


def test_a_plan_without_intent_fields_keeps_the_leftover_item_intent(inst):
    item = {"type": "ticker", "value": "ACME", "intent": {"buy_below": 5.0}}
    plans = {"inst_acme": {"instrument_id": "inst_acme", "role": "core"}}
    assert holding_plans.intent_for_item(item, plans) == {"buy_below": 5.0}


def test_the_first_plan_write_absorbs_a_leftover_item_intent(client):
    from backend.services.watchlist import watchlist_store

    store = watchlist_store()
    wid = store.create_watchlist("Old")["id"]
    store.insert(
        {
            "id": "wli_old",
            "type": "ticker",
            "value": "ACME",
            "watchlist_ids": [wid],
            "intent": {"thesis": "Kept from before", "buy_below": 90},
        }
    )
    plan = _set({"role": "core"})
    assert (plan["thesis"], plan["buy_below"], plan["role"]) == ("Kept from before", 90.0, "core")
    assert not store.find_by_id("wli_old").get("intent")


def test_a_bare_crypto_symbol_finds_its_instrument(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    InstrumentStore().insert(
        {
            "id": "inst_btc",
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "USD",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "identifiers": {},
            "quote_per_unit": 1.0,
        }
    )
    _set({"buy_below": 50000}, iid="inst_btc")
    item = {"type": "ticker", "value": "BTC"}
    assert holding_plans.intent_for_item(item) == {"buy_below": 50000.0}
    assert call("delete_holding_plans", tickers=["BTC"])["results"][0]["ok"] is True
    assert holding_plans.plan_for("inst_btc") is None


def test_a_refused_item_field_leaves_the_plan_untouched(client):
    item_id = _watch_acme(client)
    res = client.patch(
        f"/api/watchlist/items/{item_id}",
        json={"intent": {"thesis": "New"}, "custom_feed_urls": ["ftp://not-a-feed"]},
    )
    assert res.status_code == 422
    assert holding_plans.plan_for("inst_acme") is None


def test_a_refused_intent_leaves_the_item_untouched(client):
    item_id = _watch_acme(client)
    res = client.patch(
        f"/api/watchlist/items/{item_id}",
        json={"intent": {"buy_below": 70, "buy_above": 90}, "label": "Renamed"},
    )
    assert res.status_code == 422
    from backend.services.watchlist import watchlist_store

    assert watchlist_store().find_by_id(item_id).get("label") != "Renamed"
