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
    assert stored == {
        "action": "hold",
        "why": "A close below the stop would mean earnings, not mood.",
        "held": True,
        "price_at": 85.0,
        "stop": 70.0,
    }
