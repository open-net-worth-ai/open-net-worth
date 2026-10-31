"""The action line on "Lucius's take" — the synthesis's one call, checked and stored on the Asset
Brief, and composed with the user's thesis levels for the asset page."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.services import position_verdict as pv
from tests.test_analyst_sweep import _seed_pending_row, _seed_signal, _stub_analyst
from tests.test_thesis_judgment import aapl_thesis  # noqa: F401 — fixture

# ── normalize: what the model said, checked against the price it was made at ──────────


def test_a_held_call_keeps_levels_on_the_right_side_of_the_price():
    v = pv.normalize(
        {"action": "Hold", "why": " Business  intact. ", "stop": 72, "trim": 120},
        held=True,
        price=85.0,
    )
    assert v == {
        "action": "hold",
        "why": "Business intact.",
        "held": True,
        "price_at": 85.0,
        "stop": 72.0,
        "trim": 120.0,
    }


@pytest.mark.parametrize(
    ("raw", "dropped"),
    [
        ({"stop": 90}, "stop"),  # a stop above the price
        ({"stop": 30}, "stop"),  # more than half the price away
        ({"trim": 80}, "trim"),  # a trim below the price
        ({"trim": 400}, "trim"),  # beyond 3x the price
        ({"stop": "soon"}, "stop"),
        ({"stop": True}, "stop"),
    ],
)
def test_a_suggested_level_that_cannot_be_right_is_dropped(raw, dropped):
    v = pv.normalize({"action": "hold", "why": "x", **raw}, held=True, price=85.0)
    assert v is not None and dropped not in v


@pytest.mark.parametrize(
    ("raw", "held"),
    [
        ({"action": "buy", "why": "x"}, True),  # a watched action on a holding
        ({"action": "sell", "why": "x"}, False),  # a held action on a watched ticker
        ({"action": "hold", "why": "  "}, True),
        ({"action": "panic", "why": "x"}, True),
        ("hold", True),
        ({}, True),
    ],
)
def test_an_unusable_call_is_none(raw, held):
    assert pv.normalize(raw, held=held, price=85.0) is None


def test_no_price_keeps_the_call_without_suggested_levels():
    v = pv.normalize({"action": "wait", "why": "x", "entry": 60}, held=False, price=None)
    assert v == {"action": "wait", "why": "x", "held": False}


# ── compose: what the page shows ─────────────────────────────────────────────────


def test_the_thesis_level_wins_and_distances_come_from_todays_price():
    stored = pv.normalize(
        {"action": "hold", "why": "x", "stop": 72, "trim": 120}, held=True, price=85.0
    )
    v = pv.compose(stored, {"stop_below": 50.0}, held=True, price=100.0, currency="USD")
    assert v == {
        "action": "hold",
        "why": "x",
        "currency": "USD",
        "order": None,
        "levels": [
            {"kind": "stop", "price": 50.0, "source": "thesis", "distance_pct": -50.0},
            {"kind": "trim", "price": 120.0, "source": "lucius", "distance_pct": 20.0},
        ],
    }


def test_a_watched_call_shows_the_buy_zone_top_as_its_entry():
    stored = pv.normalize({"action": "buy", "why": "x", "entry": 80}, held=False, price=82.0)
    v = pv.compose(stored, {"buy_below": 78.0}, held=False, price=78.0, currency="USD")
    assert v["levels"] == [
        {"kind": "entry", "price": 78.0, "source": "thesis", "distance_pct": 0.0}
    ]


def test_a_call_made_for_the_other_side_of_held_is_not_shown():
    stored = pv.normalize({"action": "buy", "why": "x"}, held=False, price=82.0)
    assert pv.compose(stored, None, held=True, price=82.0, currency="USD") is None
    assert pv.compose(None, None, held=True, price=82.0, currency="USD") is None


# ── the analyst writes it ────────────────────────────────────────────────────────

_REPLY = json.dumps(
    {
        "why": "Apple fell 15% after a short-seller report.",
        "headline": "Apple Falls 15% After Short Report",
        "impact": "",
        "actions": ["Hold"],
        "message": "Apple fell 15% after a short-seller report.",
        "source_url": "https://example.com/aapl",
        "key_issues": [{"question": "Is demand slowing?", "bullish": "No.", "bearish": "Yes."}],
        "portfolio_narrative": "The business is intact and the drop is about sentiment.",
        "verdict": {
            "action": "hold",
            "why": "A close below the stop would mean earnings, not mood.",
            "stop": 70,
            "trim": 500,
        },
    }
)


def test_the_synthesis_asks_for_a_verdict_and_stores_it_on_the_brief(
    tmp_path,
    monkeypatch,
    db_isolated,
    aapl_thesis,  # noqa: F811
):
    import backend.monitoring.analyst as analyst_mod
    from backend.db.asset_brief_store import AssetBriefStore
    from backend.services import thesis_judgment

    calls, _ = _stub_analyst(monkeypatch, tmp_path, db_isolated, llm_response=_REPLY)
    monkeypatch.setattr(analyst_mod, "_is_held_instrument", lambda iid: iid == "inst-aapl")
    monkeypatch.setattr(thesis_judgment, "local_price", lambda inst: 85.0)
    _seed_signal(tmp_path, db_isolated)
    _seed_pending_row(tmp_path, db_isolated)

    asyncio.run(analyst_mod.run_analysis_sweep())

    assert '"verdict": {"action": "hold|add|trim|sell"' in calls[0]["system"]
    assert "verdict: your ONE call on this position" in calls[0]["system"]
    stored = AssetBriefStore().get("inst-aapl")["position_verdict"]
    assert stored.pop("at")
    assert stored == {
        "action": "hold",
        "why": "A close below the stop would mean earnings, not mood.",
        "held": True,
        "price_at": 85.0,
        "stop": 70.0,
    }


# ── the next take is shown the previous call ────────────────────────────────────

_PRIOR = {
    "action": "hold",
    "why": "Cloud demand is strong.",
    "held": True,
    "price_at": 100.0,
    "stop": 88.0,
    "trim": 120.0,
    "at": "2026-10-31T08:10:00+00:00",
}


def test_the_previous_call_is_told_with_its_levels_and_date():
    block = pv.prior_block(_PRIOR, held=True, currency="USD")
    assert block.startswith(
        "YOUR PREVIOUS CALL on this position (2026-10-31, price then USD 100): HOLD — "
        "Cloud demand is strong."
    )
    assert "Levels you suggested then: stop USD 88, trim USD 120." in block
    assert "never move a level only because the price moved" in block


def test_no_previous_call_or_one_for_the_other_side_is_no_block():
    assert pv.prior_block(None, held=True) == ""
    assert pv.prior_block(_PRIOR, held=False) == ""


def test_the_synthesis_prompt_carries_the_previous_call(
    tmp_path,
    monkeypatch,
    db_isolated,
    aapl_thesis,  # noqa: F811
):
    import backend.monitoring.analyst as analyst_mod
    from backend.db.asset_brief_store import AssetBriefStore

    AssetBriefStore().upsert_brief({"instrument_id": "inst-aapl", "position_verdict": _PRIOR})
    calls, _ = _stub_analyst(monkeypatch, tmp_path, db_isolated, llm_response=_REPLY)
    monkeypatch.setattr(analyst_mod, "_is_held_instrument", lambda iid: iid == "inst-aapl")
    _seed_signal(tmp_path, db_isolated)
    _seed_pending_row(tmp_path, db_isolated)

    asyncio.run(analyst_mod.run_analysis_sweep())

    assert "YOUR PREVIOUS CALL on this position" in calls[0]["prompt"]
    assert "stop USD 88" in calls[0]["prompt"]


def test_a_watched_ticker_is_told_its_price(monkeypatch, db_isolated, aapl_thesis):  # noqa: F811
    import backend.monitoring.analyst as analyst_mod
    from backend.services import thesis_judgment

    monkeypatch.setattr(thesis_judgment, "local_price", lambda inst: 85.5)
    assert analyst_mod._verdict_context("inst-aapl", held=False) == "\n\nCurrent price: USD 85.5"
    assert analyst_mod._verdict_context("inst-aapl", held=True) == ""


# ── a call that moves money carries the order to place ──────────────────────────


def test_a_trim_carries_its_order_capped_at_the_position():
    raw = {"action": "trim", "why": "x", "quantity": 25, "limit": 102}
    v = pv.normalize(raw, held=True, price=100.0, held_qty=100)
    assert v["order"] == {"side": "sell", "quantity": 25.0, "limit": 102.0}
    shown = pv.compose(v, None, held=True, price=100.0, currency="USD")
    assert shown["order"] == {"side": "sell", "quantity": 25.0, "limit": 102.0}


@pytest.mark.parametrize(
    ("raw", "held_qty", "order"),
    [
        ({"action": "sell", "quantity": 150}, 100, None),  # more than is held
        ({"action": "sell", "quantity": 10}, None, None),  # holding unknown
        ({"action": "add", "quantity": 10, "limit": 300}, 100, {"side": "buy", "quantity": 10.0}),
        ({"action": "hold", "quantity": 10}, 100, None),
        ({"action": "trim", "quantity": 0}, 100, None),
    ],
)
def test_an_order_that_cannot_be_placed_is_dropped(raw, held_qty, order):
    v = pv.normalize({"why": "x", **raw}, held=True, price=100.0, held_qty=held_qty)
    assert v.get("order") == order


def test_a_watched_buy_is_an_order_too_and_the_next_take_is_told_it():
    v = pv.normalize(
        {"action": "buy", "why": "x", "quantity": 5, "limit": 98}, held=False, price=100.0
    )
    assert v["order"] == {"side": "buy", "quantity": 5.0, "limit": 98.0}
    assert "Order you suggested then: buy 5 at USD 98 limit." in pv.prior_block(
        v, held=False, currency="USD"
    )


# ── a chat bound to the stock carries the call ──────────────────────────────────


def test_a_chat_about_the_stock_is_told_the_take_and_its_call(db_isolated, aapl_thesis):  # noqa: F811
    from backend.db.asset_brief_store import AssetBriefStore
    from backend.services.mentions.resolve import session_brief_block

    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-aapl",
            "portfolio_narrative": "Large holding that ran ahead of earnings.",
            "position_verdict": {
                **_PRIOR,
                "action": "trim",
                "order": {"side": "sell", "quantity": 7, "limit": 101},
            },
        }
    )
    block = session_brief_block("inst-aapl", "Apple")
    assert "lucius_take: Large holding that ran ahead of earnings." in block
    assert (
        "lucius_call: TRIM — Cloud demand is strong. Order: sell 7 at a USD 101 limit. "
        "Levels: stop USD 50 (your thesis), trim USD 180 (your thesis). (as of 2026-10-31)"
    ) in block


# ── the take is told the user's price alerts ────────────────────────────────────


def test_price_alerts_are_told_so_a_level_reuses_one_already_set():
    block = pv.alerts_block(
        [
            {"op": "<=", "value": 112, "name": "Stop review", "kind": "price"},
            {"op": ">=", "value": 5, "kind": "move"},  # a daily-move alert is not a level
            {"op": ">=", "value": 150},
        ],
        currency="USD",
    )
    assert block.startswith("YOUR PRICE ALERTS on this ticker: ≤ USD 112 (Stop review); ≥ USD 150.")
    assert pv.alerts_block([], currency="USD") == ""
    assert pv.alerts_block([{"op": ">=", "value": 5, "kind": "move"}]) == ""


def test_the_take_context_carries_the_alerts(monkeypatch, db_isolated, aapl_thesis):  # noqa: F811
    import backend.monitoring.analyst as analyst_mod
    from backend.services import alerts

    monkeypatch.setattr(
        alerts, "list_alerts", lambda ticker: [{"op": "<=", "value": 80, "name": "Floor"}]
    )
    ctx = analyst_mod._verdict_context("inst-aapl", held=True)
    assert "YOUR PRICE ALERTS on this ticker: ≤ USD 80 (Floor)." in ctx
