"""Revision 202610271200_holding_plans: watch-item ``intent`` moves onto per-instrument plans."""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from backend.db.alembic_config import make_config
from backend.db.tables import DEFAULT_BOOK_ID

_PARENT = "202610251700_statement_previews"
_REVISION = "202610271200_holding_plans"


def _instrument(conn, iid: str, source_id: str, seq: int) -> None:
    payload = {"id": iid, "symbol": source_id, "price_source_id": source_id}
    conn.execute(
        text("INSERT INTO instruments (seq, id, payload) VALUES (:s, :i, :p)"),
        {"s": seq, "i": iid, "p": json.dumps(payload)},
    )


@pytest.fixture
def seeded(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _PARENT)
    engine = create_engine(url)
    items = [
        # matched by its own instrument_id
        {
            "id": "w1",
            "type": "ticker",
            "value": "AAA",
            "instrument_id": "inst_a",
            "intent": {"thesis": " Moat ", "buy_below": 90, "fair_value": 150},
        },
        # matched by a unique price_source_id
        {"id": "w2", "type": "ticker", "value": "bbb", "intent": {"buy_below": 10}},
        # two instruments share the id → ambiguous → keeps its intent
        {"id": "w3", "type": "ticker", "value": "DUP", "intent": {"buy_below": 5}},
        # no instrument at all → keeps its intent
        {"id": "w4", "type": "ticker", "value": "NONE", "intent": {"thesis": "later"}},
        {"id": "w5", "type": "topic", "value": "AI chips"},
        # matched by a unique bare symbol (held crypto: "BTC" vs "bitcoin")
        {"id": "w6", "type": "ticker", "value": "BTC", "intent": {"buy_below": 50000}},
        # second item for an instrument that already got a plan → keeps its intent
        {"id": "w7", "type": "ticker", "value": "aaa", "intent": {"thesis": "other"}},
        # a stored price that is not a number → keeps its intent, boot does not fail
        {"id": "w8", "type": "ticker", "value": "CCC", "intent": {"fair_value": "lots"}},
    ]
    with engine.begin() as conn:
        _instrument(conn, "inst_a", "AAA", 0)
        _instrument(conn, "inst_b", "BBB", 1)
        _instrument(conn, "inst_d1", "DUP", 2)
        _instrument(conn, "inst_d2", "DUP", 3)
        conn.execute(
            text("INSERT INTO instruments (seq, id, payload) VALUES (4, 'inst_btc', :p)"),
            {"p": json.dumps({"id": "inst_btc", "symbol": "BTC", "price_source_id": "bitcoin"})},
        )
        _instrument(conn, "inst_c", "CCC", 5)
        conn.execute(
            text("INSERT INTO watchlists (book_id, payload) VALUES (:b, :p)"),
            {"b": DEFAULT_BOOK_ID, "p": json.dumps({"watchlists": [], "items": items})},
        )
    yield engine, url
    engine.dispose()


def _items(engine) -> dict:
    with engine.connect() as conn:
        payload = conn.execute(text("SELECT payload FROM watchlists")).scalar_one()
    payload = json.loads(payload) if isinstance(payload, str) else payload
    return {i["id"]: i for i in payload["items"]}


def _plans(engine) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT book_id, payload FROM holding_plans")).fetchall()
    out = {}
    for book_id, payload in rows:
        payload = json.loads(payload) if isinstance(payload, str) else payload
        assert book_id == DEFAULT_BOOK_ID
        out[payload["instrument_id"]] = payload
    return out


def test_upgrade_moves_matched_intent_and_keeps_the_rest(seeded):
    engine, url = seeded
    command.upgrade(make_config(url), _REVISION)

    plans = _plans(engine)
    assert set(plans) == {"inst_a", "inst_b", "inst_btc"}
    assert plans["inst_btc"]["buy_below"] == 50000.0
    a = plans["inst_a"]
    assert (a["thesis"], a["buy_below"], a["fair_value"], a["role"], a["exit_if"]) == (
        "Moat",
        90.0,
        150.0,
        None,
        None,
    )
    assert a["id"].startswith("hp_") and plans["inst_b"]["buy_below"] == 10.0

    items = _items(engine)
    assert "intent" not in items["w1"] and "intent" not in items["w2"]
    assert items["w3"]["intent"] == {"buy_below": 5}
    assert items["w4"]["intent"] == {"thesis": "later"}
    assert items["w7"]["intent"] == {"thesis": "other"}
    assert items["w8"]["intent"] == {"fair_value": "lots"}


def test_downgrade_restores_the_intent_and_drops_the_table(seeded):
    engine, url = seeded
    command.upgrade(make_config(url), _REVISION)
    command.downgrade(make_config(url), _PARENT)

    assert "holding_plans" not in inspect(engine).get_table_names()
    items = _items(engine)
    assert items["w1"]["intent"] == {"thesis": "Moat", "buy_below": 90.0, "fair_value": 150.0}
    assert items["w2"]["intent"] == {"buy_below": 10.0}
    assert items["w4"]["intent"] == {"thesis": "later"}
