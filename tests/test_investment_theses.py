"""Investment theses — the store's rules, the one write choke, REST, the watchlist ``intent``
compatibility path, the watch readers' view and the Lucius tools."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import execute_tool
from backend.core.errors import NotFoundError, ValidationError
from backend.db.investment_thesis_store import InvestmentThesisStore, validate_thesis
from backend.services import investment_theses


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
    from backend.api.investment_theses import router as theses_router
    from backend.api.watchlist import router as wl_router

    app = FastAPI()
    app.include_router(theses_router)
    app.include_router(wl_router)
    return TestClient(app)


def _set(fields: dict, clear: list | None = None, iid: str = "inst_acme") -> dict | None:
    change = {"op": "set", "instrument_id": iid, "fields": fields, "clear": clear or []}
    return investment_theses.apply_thesis_change(change)["thesis"]


def call(tool, **inp):
    text, _ = asyncio.run(execute_tool(tool, inp))
    return json.loads(text)


# ── the store's rules ───────────────────────────────────────────────────────────────


def test_validate_normalizes_and_drops_blanks():
    out = validate_thesis(
        {"role": "core", "reason": "  Own   the market  ", "breaks_if": "", "fair_value": "120"}
    )
    assert out == {"role": "core", "reason": "Own the market", "fair_value": 120.0}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"role": "growth"}, "Role must be one of"),
        ({"reason": "x" * 281}, "at most 280"),
        ({"stop_below": -1}, "positive price"),
        ({"fair_value": "lots"}, "must be a number"),
        ({"buy_above": 70}, "needs a top"),
        ({"buy_below": 70, "buy_above": 90}, "floor must be below"),
        ({"stop_below": 200, "trim_above": 150}, "stop must be below"),
        ({"colour": "red"}, "Unknown thesis field"),
    ],
)
def test_validate_refuses_bad_theses(fields, message):
    with pytest.raises(ValidationError, match=message):
        validate_thesis(fields)


# ── the write choke ─────────────────────────────────────────────────────────────────


def test_set_creates_then_merges(inst):
    first = _set({"reason": "Share gains", "role": "satellite", "stop_below": 120})
    assert first["id"].startswith("th_") and first["instrument_id"] == "inst_acme"
    second = _set({"fair_value": 190})
    assert second["id"] == first["id"]
    assert (second["reason"], second["role"], second["stop_below"], second["fair_value"]) == (
        "Share gains",
        "satellite",
        120.0,
        190.0,
    )
    assert len(InvestmentThesisStore().all()) == 1


def test_clear_blanks_fields_and_an_empty_thesis_is_deleted(inst):
    _set({"reason": "Share gains", "stop_below": 120})
    assert _set({}, clear=["stop_below"])["stop_below"] is None
    assert _set({}, clear=["reason"]) is None
    assert InvestmentThesisStore().all() == []


def test_validation_runs_on_the_merged_thesis(inst):
    _set({"buy_below": 150})
    with pytest.raises(ValidationError, match="floor must be below"):
        _set({"buy_above": 160})
    assert investment_theses.thesis_for("inst_acme")["buy_above"] is None


def test_delete_and_unknown_instrument(inst):
    _set({"reason": "x"})
    assert investment_theses.apply_thesis_change(
        {"op": "delete", "instrument_id": "inst_acme"}
    ) == {"thesis": None}
    assert investment_theses.thesis_for("inst_acme") is None
    with pytest.raises(NotFoundError):
        _set({"reason": "x"}, iid="inst_missing")


def test_list_theses_joins_ticker_and_held(inst):
    from backend.db.position_store import PositionStore

    _set({"reason": "x"})
    assert investment_theses.list_theses()[0]["held"] is False
    PositionStore().insert(
        {"id": "pos_1", "instrument_id": "inst_acme", "account_id": "acc_1", "quantity": 3}
    )
    theses = investment_theses.list_theses(["acme"])
    assert [(p["ticker"], p["held"]) for p in theses] == [("ACME", True)]
    assert investment_theses.list_theses(["OTHER"]) == []


# ── REST ────────────────────────────────────────────────────────────────────────────


def test_put_replaces_the_whole_thesis(client):
    res = client.put("/api/investment-theses/inst_acme", json={"reason": "A", "stop_below": 100})
    assert res.status_code == 200 and res.json()["thesis"]["stop_below"] == 100.0
    res = client.put("/api/investment-theses/inst_acme", json={"reason": "B"})
    assert res.json()["thesis"]["reason"] == "B" and res.json()["thesis"]["stop_below"] is None
    listed = client.get("/api/investment-theses").json()["theses"]
    assert [(p["ticker"], p["reason"]) for p in listed] == [("ACME", "B")]


def test_put_refuses_a_bad_thesis_with_its_reason(client):
    res = client.put("/api/investment-theses/inst_acme", json={"buy_below": 70, "buy_above": 90})
    assert res.status_code == 422
    assert "floor must be below" in res.json()["detail"]
    assert client.put("/api/investment-theses/nope", json={"reason": "x"}).status_code == 404


def test_delete_route(client):
    client.put("/api/investment-theses/inst_acme", json={"reason": "A"})
    assert client.delete("/api/investment-theses/inst_acme").json() == {"thesis": None}
    assert client.get("/api/investment-theses").json()["theses"] == []


# ── the watchlist ``intent`` compatibility path ─────────────────────────────────────


def _watch_acme(client) -> str:
    """Inserted straight into the store: adding through POST resolves the ticker online."""
    from backend.services.watchlist import watchlist_store

    store = watchlist_store()
    wid = store.create_watchlist("Chips")["id"]
    store.insert({"id": "wli_acme", "type": "ticker", "value": "ACME", "watchlist_ids": [wid]})
    return "wli_acme"


def test_watch_intent_writes_the_thesis_and_leaves_held_fields_alone(client):
    _set({"role": "core", "breaks_if": "Margins fall"})
    item_id = _watch_acme(client)
    res = client.patch(
        f"/api/watchlist/items/{item_id}", json={"intent": {"thesis": "Cheap", "buy_below": 90}}
    )
    assert res.status_code == 200
    assert res.json()["intent"] == {"thesis": "Cheap", "buy_below": 90.0}
    thesis = investment_theses.thesis_for("inst_acme")
    assert (thesis["reason"], thesis["buy_below"], thesis["role"], thesis["breaks_if"]) == (
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


def test_a_thesis_written_elsewhere_shows_as_the_items_intent(client):
    item_id = _watch_acme(client)
    _set({"reason": "From the asset page", "fair_value": 150, "stop_below": 100})
    item = call("get_watchlist", values=["ACME"])["items"][0]
    assert item["id"] == item_id
    assert item["intent"] == {"thesis": "From the asset page", "fair_value": 150.0}


def test_intent_for_item_falls_back_to_a_legacy_unmatched_intent(db_isolated):
    legacy = {"type": "ticker", "value": "ZZZQ", "intent": {"buy_below": 5.0}}
    assert investment_theses.intent_for_item(legacy, {}) == {"buy_below": 5.0}
    assert investment_theses.intent_for_item({"type": "topic", "value": "AI"}, {}) is None


# ── Lucius tools ────────────────────────────────────────────────────────────────────


def test_set_get_delete_tools_round_trip(inst):
    res = call(
        "set_investment_theses",
        updates=[
            {"ticker": "ACME", "role": "satellite", "reason": "Share gains", "trim_above": 220}
        ],
    )
    assert res["results"][0]["ok"] is True and res["results"][0]["thesis"]["role"] == "satellite"
    res = call("set_investment_theses", updates=[{"ticker": "ACME", "clear": ["trim_above"]}])
    assert res["results"][0]["thesis"]["trim_above"] is None
    got = call("get_investment_theses", tickers=["ACME"])
    assert got["count"] == 1 and got["theses"][0]["reason"] == "Share gains"
    assert call("delete_investment_theses", tickers=["ACME"])["results"][0]["ok"] is True
    assert call("get_investment_theses")["count"] == 0


def test_set_tool_reports_a_bad_entry_without_failing_the_others(inst):
    res = call(
        "set_investment_theses",
        updates=[
            {"ticker": "ACME", "stop_below": 200, "trim_above": 100},
            {"ticker": "ACME", "reason": "fine"},
        ],
    )
    assert [r["ok"] for r in res["results"]] == [False, True]
    assert "stop must be below" in res["results"][0]["error"]


# ── review follow-ups ───────────────────────────────────────────────────────────────


def test_get_watchlist_route_shows_the_thesis_backed_intent(client):
    item_id = _watch_acme(client)
    client.patch(
        f"/api/watchlist/items/{item_id}", json={"intent": {"thesis": "T", "buy_below": 90}}
    )
    items = {i["id"]: i for i in client.get("/api/watchlist").json()["items"]}
    assert items[item_id]["intent"] == {"thesis": "T", "buy_below": 90.0}


def test_a_thesis_without_intent_fields_keeps_the_leftover_item_intent(inst):
    item = {"type": "ticker", "value": "ACME", "intent": {"buy_below": 5.0}}
    theses = {"inst_acme": {"instrument_id": "inst_acme", "role": "core"}}
    assert investment_theses.intent_for_item(item, theses) == {"buy_below": 5.0}


def test_the_first_thesis_write_absorbs_a_leftover_item_intent(client):
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
    thesis = _set({"role": "core"})
    assert (thesis["reason"], thesis["buy_below"], thesis["role"]) == (
        "Kept from before",
        90.0,
        "core",
    )
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
    assert investment_theses.intent_for_item(item) == {"buy_below": 50000.0}
    assert call("delete_investment_theses", tickers=["BTC"])["results"][0]["ok"] is True
    assert investment_theses.thesis_for("inst_btc") is None


def test_a_refused_item_field_leaves_the_thesis_untouched(client):
    item_id = _watch_acme(client)
    res = client.patch(
        f"/api/watchlist/items/{item_id}",
        json={"intent": {"thesis": "New"}, "custom_feed_urls": ["ftp://not-a-feed"]},
    )
    assert res.status_code == 422
    assert investment_theses.thesis_for("inst_acme") is None


def test_a_refused_intent_leaves_the_item_untouched(client):
    item_id = _watch_acme(client)
    res = client.patch(
        f"/api/watchlist/items/{item_id}",
        json={"intent": {"buy_below": 70, "buy_above": 90}, "label": "Renamed"},
    )
    assert res.status_code == 422
    from backend.services.watchlist import watchlist_store

    assert watchlist_store().find_by_id(item_id).get("label") != "Renamed"
