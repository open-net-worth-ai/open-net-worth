"""Thesis judgment — price moves and entry points read against the user's investment thesis.

The pure judgment (stance per price) and the surfaces that read it: the Analyst's move alert, the Intel scan's stories, the daily briefing's lines, the
held pulse and Lucius's portfolio tool. With no thesis each one is unchanged.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.services import thesis_judgment as tj
from tests.test_analyst_sweep import (
    _read_signal_with_timeline,
    _seed_pending_row,
    _seed_signal,
    _stub_analyst,
)

# A speculative long-horizon holding — the case that kept drawing "sell" after a 15% drop.
QUANTUM = {
    "role": "speculative",
    "reason": "Holding 3+ years on quantum computing",
    "breaks_if": "The quantum hardware roadmap is abandoned",
    "stop_below": 50.0,
    "fair_value": 140.0,
    "trim_above": 180.0,
}


def _held(price: float, thesis: dict | None = None) -> dict:
    return tj.judge(thesis or QUANTUM, price, held=True, currency="USD", ticker="AAPL")


# ── the judgment ───────────────────────────────────────────────────────────────────


def test_a_fifteen_percent_drop_above_the_stop_is_intact_noise():
    j = _held(85.0)  # down 15% from 100, still 70% above the 50 stop
    assert j["stance"] == "intact"
    assert not j["sell_ok"] and not j["trim_ok"]
    assert j["holding_period"] == "3+ years"
    assert "noise against a 3+ years holding period" in j["read"]
    assert "70% above your 50 stop" in j["position"]


@pytest.mark.parametrize(
    ("price", "stance"),
    [
        (49.0, "below_stop"),
        (50.0, "below_stop"),
        (190.0, "above_trim"),
        (175.0, "near_trim"),
        (136.0, "near_fair_value"),
        (150.0, "above_fair_value"),
        (100.0, "intact"),
    ],
)
def test_held_stance_by_price(price, stance):
    assert _held(price)["stance"] == stance


@pytest.mark.parametrize(
    ("price", "stance"),
    [(75.0, "in_buy_zone"), (55.0, "below_buy_zone"), (95.0, "above_buy_zone")],
)
def test_watched_entry_is_judged_against_the_buy_zone(price, stance):
    thesis = {"reason": "Wait for a pullback", "buy_above": 60.0, "buy_below": 80.0}
    assert tj.judge(thesis, price, held=False)["stance"] == stance


def test_no_thesis_is_no_judgment():
    assert tj.judge(None, 85.0, held=True) is None
    assert tj.prompt_block(None) == ""


@pytest.mark.parametrize(
    ("reason", "period"),
    [
        ("Holding 3+ years on quantum", "3+ years"),
        ("A long-term compounder", "long-term"),
        ("5-10 years of runway", "5-10 years"),
        ("Momentum trade", ""),
    ],
)
def test_holding_period_is_read_from_the_reason(reason, period):
    assert tj.holding_period(reason) == period


def test_the_citation_names_role_reason_breaks_if_and_every_level():
    line = tj.cite_line(_held(85.0))
    for part in (
        "speculative",
        "Holding 3+ years on quantum computing",
        "The quantum hardware roadmap is abandoned",
        "stop",
        "fair value",
        "trim level",
    ):
        assert part in line


# ── the surfaces ───────────────────────────────────────────────────────────────────


@pytest.fixture
def aapl_thesis(db_isolated):
    from backend.db.instrument_store import InstrumentStore
    from backend.services import investment_theses

    InstrumentStore().insert(
        {
            "id": "inst-aapl",
            "symbol": "AAPL",
            "name": "Apple",
            "asset_class": "equity",
            "asset_subclass": "stock",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
            "identifiers": {},
            "quote_per_unit": 1.0,
        }
    )
    investment_theses.apply_thesis_change(
        {"op": "set", "instrument_id": "inst-aapl", "fields": dict(QUANTUM)}
    )


_SELL_REPLY = json.dumps(
    {
        "why": "Apple fell 15% after a short-seller report.",
        "headline": "Apple Falls 15% After Short Report",
        "impact": "Your position lost about USD 150.",
        "actions": ["Sell to cut your losses"],
        "message": "Apple fell 15% after a short-seller report. Consider selling to limit "
        "further losses.",
        "source_url": "https://example.com/aapl",
    }
)


def test_a_drop_on_a_thesis_is_judged_against_it_and_the_alert_cites_it(
    tmp_path, monkeypatch, db_isolated, aapl_thesis
):
    """The model is told the thesis and its rule; its words are shown as written, and the thesis
    line computed from the price rides beside them (no post-hoc rewrite of the advice)."""
    import backend.monitoring.analyst as analyst_mod

    calls, pushes = _stub_analyst(
        monkeypatch, tmp_path, db_isolated, llm_response=_SELL_REPLY, channels=["telegram"]
    )
    monkeypatch.setattr(analyst_mod, "_is_held_instrument", lambda iid: iid == "inst-aapl")
    _seed_signal(tmp_path, db_isolated)
    _seed_pending_row(tmp_path, db_isolated)  # AAPL 100 → 85, -15%

    asyncio.run(analyst_mod.run_analysis_sweep())

    assert "YOUR THESIS for Apple" in calls[0]["prompt"]
    assert "Holding period stated: 3+ years" in calls[0]["prompt"]
    assert "THE USER'S INVESTMENT THESIS" in calls[0]["system"]
    sig = _read_signal_with_timeline("crash-watch-aapl")
    assert sig["action_items"] == ["Sell to cut your losses"]
    message = pushes[0]["message"]
    assert "The quantum hardware roadmap is abandoned" in message
    assert "the thesis is intact on price" in message


def test_without_a_thesis_the_analyst_is_unchanged(tmp_path, monkeypatch, db_isolated):
    import backend.monitoring.analyst as analyst_mod

    calls, _ = _stub_analyst(monkeypatch, tmp_path, db_isolated, llm_response=_SELL_REPLY)
    _seed_signal(tmp_path, db_isolated)
    _seed_pending_row(tmp_path, db_isolated)

    asyncio.run(analyst_mod.run_analysis_sweep())

    assert "YOUR THESIS" not in calls[0]["prompt"]
    assert "INVESTMENT THESIS" not in calls[0]["system"]
    assert _read_signal_with_timeline("crash-watch-aapl")["action_items"] == [
        "Sell to cut your losses"
    ]


def test_the_portfolio_tool_carries_the_thesis_read(aapl_thesis):
    from backend.ai.tools.portfolio import _attach_theses

    rows = [
        {"ticker": "AAPL", "instrument_id": "inst-aapl", "price": 85.0, "currency": "USD"},
        {"ticker": "MSFT", "instrument_id": "inst-msft", "price": 400.0, "currency": "USD"},
    ]
    projected = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
    _attach_theses(rows, projected)
    assert projected[0]["thesis"]["stance"] == "intact"
    assert "Holding 3+ years" in projected[0]["thesis"]["cite"]
    assert "thesis" not in projected[1]


def test_the_held_pulse_flags_the_stop_and_the_trim_not_the_move():
    from backend.services.watch_intent import held_thesis_findings

    assert held_thesis_findings({"price": 85.0}, QUANTUM) == []
    assert held_thesis_findings({"price": 48.0}, QUANTUM)[0]["kind"] == "at_stop"
    assert held_thesis_findings({"price": 175.0}, QUANTUM)[0]["kind"] == "near_trim"
    assert held_thesis_findings({"price": 185.0}, QUANTUM)[0]["kind"] == "above_trim"
    assert held_thesis_findings({"price": 85.0}, None) == []


def test_a_price_alert_narration_is_told_the_thesis(aapl_thesis, monkeypatch):
    from backend.monitoring import price_monitor as pm
    from backend.services import investment_theses

    monkeypatch.setattr(investment_theses, "held_instrument_ids", lambda: {"inst-aapl"})
    monkeypatch.setattr(pm, "_build_position_context", lambda *a: "Asset: Apple (AAPL)")
    seen: dict = {}

    async def _no_news(*a):
        return ""

    async def _signal(**kw):
        seen["position_ctx"] = kw["position_ctx"]
        return {"id": kw["signal_id"], "ticker": "AAPL", "action_items": ["Sell into weakness"]}

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(pm, "_news_context_for_alert", _no_news)
    monkeypatch.setattr(pm, "_ai_signal", _signal)
    monkeypatch.setattr(pm, "_instant_news_freshen", _noop)
    monkeypatch.setattr(pm, "_record_alert_fired", lambda *a: None)
    monkeypatch.setattr(pm, "apply_story_change", lambda s: seen.setdefault("story", s))

    asyncio.run(
        pm._fire_alert_condition(
            "AAPL",
            {"op": "<=", "value": 90, "kind": "price"},
            85.0,
            None,
            {"instrument_id": "inst-aapl"},
            "USD",
            "Apple",
            {},
            [],
            {},
            "k",
        )
    )
    assert "YOUR THESIS for Apple" in seen["position_ctx"]
    assert "THE USER'S INVESTMENT THESIS" in seen["position_ctx"]
    assert seen["story"]["action_items"] == ["Sell into weakness"]


def test_the_briefing_reads_a_thesis_for_each_holding_that_has_one(aapl_thesis, monkeypatch):
    from backend.services import briefing_context, investment_theses

    monkeypatch.setattr(investment_theses, "held_instrument_ids", lambda: {"inst-aapl"})
    book = {
        "merged_rows": [
            {
                "symbol": "AAPL",
                "name": "Apple",
                "instrument_id": "inst-aapl",
                "price": 85.0,
                "currency": "USD",
            },
            {
                "symbol": "MSFT",
                "name": "Microsoft",
                "instrument_id": "inst-msft",
                "price": 400.0,
                "currency": "USD",
            },
        ]
    }
    judgments, names = briefing_context.thesis_judgments(book)
    assert list(judgments) == ["AAPL"] and names == {"AAPL": "Apple"}
    (line,) = briefing_context.thesis_lines(judgments, names)
    assert line.startswith("  Apple (AAPL, held): Your thesis (speculative)")
    assert briefing_context.thesis_judgments({"merged_rows": book["merged_rows"][1:]}) == ({}, {})
