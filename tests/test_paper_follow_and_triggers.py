"""A ticker held on paper is followed like a watched one (names only, through core.paper_follow):
its news is polled into the asset page's stories but never Intel's scan pool, a review reads
those stored stories, and a big move, a stop loss or a broken limit starts an autopilot review
once a day, telling Lucius why. ACME is 100 USD; no network and no model are touched."""

from __future__ import annotations

import asyncio

from backend.core import headless_turn, paper_follow
from backend.core.automation_runner import get_automation_approval
from backend.services import prices
from backend.services.paper_trading import (
    autopilot_run,
    autopilot_watch,
    facade,
    follow,
    news,
    views,
)
from backend.services.paper_trading.facade import place
from backend.services.paper_trading.service import apply_paper_change
from tests.paper_helpers import NOW


def _auto_portfolio(mandate: dict | None = None) -> str:
    pid = apply_paper_change(
        {
            "op": "create",
            "name": "Auto",
            "cash": 10_000,
            "home_currency": "USD",
            "mandate": mandate or {"max_position_pct": 50},
        }
    )["portfolio"]["id"]
    order = {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 10, "override_mandate": True}
    place(pid, order)
    apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto"})
    return pid


# ── follow ──────────────────────────────────────────────────────────────────────────────────


def test_paper_tickers_are_offered_by_name_only(paper_db):
    pid = _auto_portfolio()
    place(pid, {"asset_key": "inst:inst_coin", "side": "buy", "quantity": 1})
    rows = follow.held_tickers()
    assert {r["price_source_id"] for r in rows} == {"ACME", "acme-coin"}
    assert all(set(r) == {"instrument_id", "price_source_id", "label", "asset_class"} for r in rows)
    follow.register()
    try:
        assert {r["instrument_id"] for r in paper_follow.followed()} == {"inst_acme", "inst_coin"}
    finally:
        paper_follow.register_provider(None)


def test_intel_polls_paper_tickers_for_the_asset_page_only(monkeypatch):
    from backend.monitoring import rss_poller

    paper_follow.register_provider(
        lambda: [
            {
                "instrument_id": "inst_x",
                "price_source_id": "ACME",
                "label": "Acme",
                "asset_class": "equity",
            },
            {
                "instrument_id": "inst_held",
                "price_source_id": "HELD",
                "label": "Held",
                "asset_class": "equity",
            },
        ]
    )
    polled: list[list[dict]] = []

    async def fake_gather(feeds, **kw):
        polled.append(feeds)
        return []

    monkeypatch.setattr(rss_poller, "gather_rss", fake_gather)
    try:
        assert asyncio.run(rss_poller._poll_paper_tickers({"inst_held"})) == 1
    finally:
        paper_follow.register_provider(None)
    [feeds] = polled
    assert feeds and all(f["subjects"] == ["asset:inst_x"] for f in feeds)  # never scan:intel


def test_a_failing_provider_follows_nothing():
    paper_follow.register_provider(lambda: 1 / 0)
    try:
        assert paper_follow.followed() == []
    finally:
        paper_follow.register_provider(None)


# ── news ────────────────────────────────────────────────────────────────────────────────────


def test_a_review_reads_the_stored_stories_first(paper_db, monkeypatch):
    from backend.services import asset_detail

    monkeypatch.setattr(
        asset_detail,
        "stored_news",
        lambda inst: [
            {
                "title": "Acme wins a big order",
                "source": "Wire",
                "url": "https://x/1",
                "published_at": "2026-11-17T10:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(news, "pinned_urlopen", lambda *a, **k: 1 / 0)  # never fetched
    pid = _auto_portfolio()
    [acme] = facade.news(pid)["holdings"]
    assert [h["title"] for h in acme["headlines"]] == ["Acme wins a big order"]
    assert acme["note"] is None


# ── triggers ────────────────────────────────────────────────────────────────────────────────


def _snapshots(monkeypatch, price: float, prev: float) -> None:
    monkeypatch.setattr(
        prices,
        "fetch_quote_snapshots",
        lambda syms: {s: {"price": price, "prev_close": prev} for s in syms},
    )


def test_a_big_move_starts_a_review_once_a_day(paper_db, monkeypatch):
    pid = _auto_portfolio()
    _snapshots(monkeypatch, 100.0, 106.0)
    assert autopilot_watch.check(NOW) == [pid]
    a = views.page(pid)["autopilot"]
    assert a["review_soon"]
    assert autopilot_watch.check(NOW) == []  # the same reason, the same day: once


def test_a_quiet_day_starts_nothing(paper_db, monkeypatch):
    _auto_portfolio()
    _snapshots(monkeypatch, 100.0, 99.0)
    assert autopilot_watch.check(NOW) == []


def test_a_stop_loss_and_a_broken_limit_are_reasons(paper_db, monkeypatch):
    from backend.services.paper_trading import qa_quotes

    pid = _auto_portfolio({"max_position_pct": 5, "stop_loss_pct": 10})
    qa_quotes.set_state(prices={"ACME": 85.0})
    _snapshots(monkeypatch, 85.0, 86.0)
    from backend.services.paper_trading import service

    found = autopilot_watch.reasons_for(service.portfolio(pid), NOW)
    assert set(found) == {"stop:ACME", "limit:max_position_pct"}
    assert "past the 10% stop loss" in found["stop:ACME"]


def test_the_review_knows_why_it_ran_and_history_says_so(paper_db, monkeypatch):
    pid = _auto_portfolio()
    _snapshots(monkeypatch, 100.0, 106.0)
    autopilot_watch.check(NOW)
    seen: dict = {}

    async def runner(prompt, **kw):
        seen["prompt"] = prompt
        facade.record_memo(get_automation_approval().paper_portfolio_id, "Held.", None)
        return ""

    monkeypatch.setattr(headless_turn, "_runner", runner)
    asyncio.run(autopilot_run.review(pid))
    assert "started early because: ACME fell 5.7% in its last session" in seen["prompt"]
    [run] = views.page(pid)["autopilot"]["runs"]
    assert run["trigger"] == "ACME fell 5.7% in its last session"


def test_review_now_is_its_own_reason(paper_db, monkeypatch):
    pid = _auto_portfolio()
    apply_paper_change({"op": "autopilot_run_now", "id": pid})

    async def runner(prompt, **kw):
        facade.record_memo(get_automation_approval().paper_portfolio_id, "Held.", None)
        return ""

    monkeypatch.setattr(headless_turn, "_runner", runner)
    asyncio.run(autopilot_run.review(pid))
    assert views.page(pid)["autopilot"]["runs"][0]["trigger"] == "Review now"
