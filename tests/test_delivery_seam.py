"""The delivery seam (``monitoring.delivery``): with ``intel_delivery_decider`` off each of the three
Intel senders runs its own push exactly as before; on, it records a candidate and pushes nothing.
Synthetic data only."""

from __future__ import annotations

import asyncio

import pytest

from backend.monitoring import analyst, delivery, price_monitor, watch_analyst
from backend.services import news_delivery, notify_routes
from tests.flags import flags


@pytest.fixture
def routes(monkeypatch):
    """Every kind routes to Telegram; nothing is actually sent."""
    monkeypatch.setattr(notify_routes, "channels_for", lambda kind: ["telegram"])


def _legacy_recorder():
    calls: list[str] = []

    async def legacy():
        calls.append("legacy")

    return calls, legacy


PARSED = {
    "headline": "Acme agrees a takeover",
    "why": "A bid was accepted.",
    "impact": "You hold it.",
}


def _analysis():
    return delivery.analysis_candidate(
        "Acme Corp",
        "ACME",
        PARSED,
        sources=[],
        story_id="story_1",
        subject="asset:ins_acme",
        materiality="new",
        legacy_message="Analysis — Acme Corp (ACME)",
    )


def test_off_runs_the_legacy_push_and_records_nothing(db_isolated, routes):
    calls, legacy = _legacy_recorder()
    with flags(intel_delivery_decider=False):
        asyncio.run(delivery.route(_analysis(), legacy))
    assert calls == ["legacy"]
    assert news_delivery.pending() == []


def test_on_records_the_candidate_and_does_not_push(db_isolated, routes):
    calls, legacy = _legacy_recorder()
    with flags(intel_delivery_decider=True):
        asyncio.run(delivery.route(_analysis(), legacy))
    assert calls == []
    (row,) = news_delivery.pending()
    assert row["source"] == "analysis" and row["story_id"] == "story_1"
    assert row["legacy_message"] == "Analysis — Acme Corp (ACME)"


def test_on_with_no_channel_falls_back_to_the_legacy_path(db_isolated, monkeypatch):
    monkeypatch.setattr(notify_routes, "channels_for", lambda kind: [])
    calls, legacy = _legacy_recorder()
    with flags(intel_delivery_decider=True):
        asyncio.run(delivery.route(_analysis(), legacy))
    assert calls == ["legacy"]
    assert news_delivery.pending() == []


def test_on_falls_back_to_the_legacy_push_when_the_row_cannot_be_written(
    db_isolated, routes, monkeypatch
):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.services.news_delivery.apply_delivery_change", boom)
    calls, legacy = _legacy_recorder()
    with flags(intel_delivery_decider=True):
        asyncio.run(delivery.route(_analysis(), legacy))
    assert calls == ["legacy"]


# ── the three real senders ──────────────────────────────────────────────────────────


@pytest.mark.flag_both("intel_delivery_decider")
def test_the_analyst_sender(db_isolated, routes, monkeypatch):
    pushed: list[str] = []

    async def fake_push(label, ticker, parsed, evidence=None, *, story_id=""):
        pushed.append(label)

    monkeypatch.setattr(analyst, "_push_analysis", fake_push)
    asyncio.run(
        analyst._deliver_analysis(
            "Acme Corp", "ACME", PARSED, [], story_id="story_1", subject="asset:ins_acme"
        )
    )
    if delivery.delivery_active():
        assert pushed == [] and len(news_delivery.pending()) == 1
        assert "Acme Corp" in news_delivery.pending()[0]["legacy_message"]
    else:
        assert pushed == ["Acme Corp"] and news_delivery.pending() == []


@pytest.mark.flag_both("intel_delivery_decider")
def test_the_price_alert_sender_keeps_its_own_channels(db_isolated, monkeypatch):
    monkeypatch.setattr(notify_routes, "channels_for", lambda kind: [])
    pushed: list[tuple] = []

    async def fake_push(signal, channels):
        pushed.append((signal["id"], channels))

    monkeypatch.setattr(price_monitor, "_push_external_alert", fake_push)
    signal = {
        "id": "alert-1",
        "label": "ACME — price alert",
        "ticker": "ACME",
        "instrument_id": "ins_acme",
        "current_summary": "ACME crossed 100.",
        "portfolio_impact": "You hold 10.",
        "action_items": ["Review"],
    }
    asyncio.run(price_monitor._deliver_alert(signal, ["webhook"]))
    if delivery.delivery_active():
        assert pushed == []
        (row,) = news_delivery.pending()
        assert row["source"] == "price_alert" and row["user_set"] is True
        assert row["channels"] == ["webhook"] and row["subject"] == "asset:ins_acme"
        assert row["label"] == "ACME"
    else:
        assert pushed == [("alert-1", ["webhook"])]


def test_a_crash_watch_is_its_own_source(db_isolated):
    with flags(intel_delivery_decider=True):
        asyncio.run(
            price_monitor._deliver_alert(
                {"id": "crash-watch-1", "label": "ACME crash", "ticker": "ACME"},
                ["telegram"],
                crash_watch=True,
            )
        )
    assert news_delivery.pending()[0]["source"] == "crash_watch"


@pytest.mark.flag_both("intel_delivery_decider")
def test_the_watch_alert_sender(db_isolated, routes, monkeypatch):
    pushed: list[str] = []

    async def fake_push(ticker, explained):
        pushed.append(ticker)

    monkeypatch.setattr(watch_analyst, "_push", fake_push)
    story = {
        "id": "watch-story-1",
        "instrument_id": "ins_acme",
        "label": "Acme Corp",
        "explained": {
            "what_happened": "ACME fell 9%.",
            "why": "A delay was reported.",
            "what_it_means": "Inside your buy zone.",
            "worth_weighing": ["Peers"],
            "sources": [{"url": "https://example.com/a", "title": "Wire", "domain": "example.com"}],
        },
    }
    asyncio.run(watch_analyst._deliver("ACME", story))
    if delivery.delivery_active():
        assert pushed == []
        (row,) = news_delivery.pending()
        assert row["source"] == "watch_alert" and row["user_set"] is False
        assert row["headline"] == "ACME fell 9%." and row["sources"][0]["domain"] == "example.com"
    else:
        assert pushed == ["ACME"]


def test_a_sweep_with_the_flag_on_hands_the_push_to_the_decider(tmp_path, monkeypatch, db_isolated):
    import backend.monitoring.analyst as analyst_mod
    from tests.test_analyst_sweep import _seed_pending_row, _seed_signal, _stub_analyst

    _, notify_calls = _stub_analyst(monkeypatch, tmp_path, db_isolated, channels=["telegram"])
    monkeypatch.setattr(notify_routes, "channels_for", lambda kind: ["telegram"])
    _seed_signal(tmp_path, db_isolated)
    _seed_pending_row(tmp_path, db_isolated)

    with flags(intel_delivery_decider=True):
        asyncio.run(analyst_mod.run_analysis_sweep())

    assert notify_calls == []
    (row,) = news_delivery.pending()
    assert row["source"] == "analysis" and row["decision"] is None
    assert row["story_id"] and "AAPL" in row["legacy_message"] + row["ticker"]
