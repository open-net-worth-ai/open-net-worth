"""A crash alert reaches Lucius with the news behind it, and a later analysis of that news is not
muted by an earlier, different fact about the same company (2026-11-12: a −13% drop on a CEO's
resignation was sent as "check for news before acting", and the analysis that did read the
resignation 25 seconds later was suppressed as "already communicated"). Synthetic data only."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import backend.services.claims as claims_svc
from backend.monitoring import analyst, delivery_prompt, price_alert_run
from backend.monitoring import price_monitor as pm
from backend.services import news_delivery, notify_routes
from tests.flags import flags

_NOW = datetime.now(timezone.utc)
_EVIDENCE = [
    {
        "title": "Acme CEO resigns,\nboard names interim chief",
        "source": "Example Wire",
        "published_at": (_NOW - timedelta(minutes=9)).isoformat(),
        "url": "https://news.example.com/acme-ceo-resigns",
        "snippet": "The board accepted the resignation.",
    },
    {
        "title": "Acme slides after hours",
        "source": "Market Daily",
        "published_at": (_NOW - timedelta(minutes=40)).isoformat(),
        "url": "https://markets.example.org/acme-slides",
        "snippet": "",
    },
]
_SIGNAL = {
    "id": "crash-watch-ins_acme",
    "type": "price_alert",
    "instrument_id": "ins_acme",
    "label": "Acme Corp — sharp drop",
    "ticker": "ACME",
    "current_summary": "Acme Corp (ACME) is down 13.1% to USD 151 (prior close USD 173.7).",
    "portfolio_impact": "Your position (~SGD 1,000) is down about SGD 150 today.",
    "action_items": ["Review Acme Corp — check for news before acting"],
}


@pytest.fixture
def env(db_isolated, monkeypatch):
    monkeypatch.setattr(notify_routes, "channels_for", lambda kind: ["telegram"])

    async def fresh(ticker, iid):
        return list(_EVIDENCE)

    monkeypatch.setattr(pm, "_fresh_evidence", fresh)


_CITED = [
    {
        "url": "https://wire.example.net/acme-ceo",
        "title": "Acme CEO out",
        "domain": "wire.example.net",
        "outlet": "wire.example.net",
    }
]


def _deliver(active: bool, monkeypatch, found: str | None = None) -> dict:
    monkeypatch.setattr(price_alert_run, "active", lambda: active)

    async def search(prompt, *, model):
        assert "Acme Corp (ACME) fell" in prompt
        return price_alert_run.Findings(found, list(_CITED)) if found else None

    monkeypatch.setattr(price_alert_run, "search", search)

    async def go():
        await pm._deliver_crash(dict(_SIGNAL), ["telegram"], "Acme Corp", "ins_acme", 151.0)
        await asyncio.gather(*list(price_alert_run._tasks), return_exceptions=True)

    with flags(intel_delivery_decider=True):
        asyncio.run(go())
    (row,) = news_delivery.pending()
    return row


def test_a_crash_candidate_carries_the_cached_headlines_and_their_sources(env, monkeypatch):
    row = _deliver(False, monkeypatch)
    assert row["source"] == "crash_watch"
    assert "Acme CEO resigns, board names interim chief (Example Wire, " in row["news"]
    assert "Acme slides after hours (Market Daily, " in row["news"]
    assert [s["url"] for s in row["sources"]] == [
        "https://news.example.com/acme-ceo-resigns",
        "https://markets.example.org/acme-slides",
    ]
    assert [s["outlet"] for s in row["sources"]] == ["Example Wire", "Market Daily"]


def test_lucius_is_shown_the_news_and_told_to_use_it(env, monkeypatch):
    row = _deliver(False, monkeypatch)
    user, system = delivery_prompt.build_prompt([row], [], {"nickname": "Sam"})
    assert "news in Intel's feed: Acme CEO resigns, board names interim chief" in user
    assert "[Example Wire](https://news.example.com/acme-ceo-resigns)" in user
    assert "instead of telling them to go and check" in system


def test_a_crash_is_never_framed_as_a_level_they_set(env, monkeypatch):
    row = _deliver(False, monkeypatch)
    user, system = delivery_prompt.build_prompt([row], [], {"nickname": "Sam"})
    assert "automatic sharp-drop alert" in user
    assert "a level the user set" not in user
    assert "never say they set it" in system


def test_lucius_is_never_told_to_call_findings_unverified(env, monkeypatch):
    """Owner, 2026-11-13: "unverified web search" in every crash push read as boilerplate and
    undercut the message. Claims are attributed to their outlet instead, with citations."""
    _, system = delivery_prompt.build_prompt([], [], {})
    assert "unverified, so say so" not in system
    assert 'Never call anything "unverified"' in system
    assert "naming the outlet that reported it" in system


def test_with_the_run_on_the_findings_stay_apart_from_the_feed_and_are_cited(env, monkeypatch):
    row = _deliver(True, monkeypatch, found="Example Wire reports Acme's CEO resigned today.")
    assert "Acme CEO resigns" in row["news"]
    assert "Example Wire reports" not in row["news"]
    assert row["search"] == "Example Wire reports Acme's CEO resigned today."
    assert "unverified" not in row["news"] + row["search"]
    assert [s["url"] for s in row["sources"]] == [
        "https://news.example.com/acme-ceo-resigns",
        "https://markets.example.org/acme-slides",
        "https://wire.example.net/acme-ceo",
    ]
    user, _ = delivery_prompt.build_prompt([row], [], {"nickname": "Sam"})
    assert "what outlets reported just now: Example Wire reports" in user
    assert "[wire.example.net](https://wire.example.net/acme-ceo)" in user


def test_a_failed_search_still_sends_the_crash_with_its_headlines(env, monkeypatch):
    row = _deliver(True, monkeypatch, found=None)
    assert "Acme CEO resigns" in row["news"]
    assert row["search"] == ""


def test_no_cached_news_still_sends_the_crash(db_isolated, monkeypatch):
    monkeypatch.setattr(notify_routes, "channels_for", lambda kind: ["telegram"])

    async def broken(ticker, iid):
        raise OSError("evidence unreadable")

    monkeypatch.setattr(pm, "_fresh_evidence", broken)
    row = _deliver(False, monkeypatch)
    assert row["news"] == "" and row["sources"] == []


# ── The claims gate: one company is not one fact ─────────────────────────────────────────────

_GROUNDED = {
    "duplicate": False,
    "flagged": False,
    "sources": [{"url": "https://news.example.com/a", "title": "x", "domain": "example.com"}],
    "source_url": "",
    "confirmed_at": None,
}


def _earlier_ouster_report_pushed() -> str:
    group_key = claims_svc.group_key_for("asset", ["ins_acme"])
    claims_svc.apply_claim_push(
        group_key,
        story_id="news-ins_acme",
        statement="A report says Acme's board may oust its CEO.",
        sources=_GROUNDED["sources"],
        materiality="new",
        decision_relevant=True,
        confirmed_at=None,
    )
    return group_key


def test_with_the_decider_on_a_new_fact_about_the_company_is_handed_to_lucius(db_isolated):
    group_key = _earlier_ouster_report_pushed()
    row = {"created_at": datetime.now(timezone.utc).isoformat()}
    with flags(intel_delivery_decider=True):
        should_push, reason, _ = analyst._push_decision(
            row, "new", True, _GROUNDED, group_key=group_key
        )
    assert (should_push, reason) == (True, "")


def test_with_the_decider_off_the_claims_gate_still_holds(db_isolated):
    group_key = _earlier_ouster_report_pushed()
    row = {"created_at": datetime.now(timezone.utc).isoformat()}
    with flags(intel_delivery_decider=False):
        should_push, reason, _ = analyst._push_decision(
            row, "new", True, _GROUNDED, group_key=group_key
        )
    assert (should_push, reason) == (False, "duplicate_claim")
