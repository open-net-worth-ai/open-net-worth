"""Autopilot memory: every review reads a track record built by code from the run log and the
orders (its last reviews, how each buy did since, how each stock moved after it was sold, what
it said it was watching), plus the lessons it wrote about stocks it fully sold — its own earlier
read, kept apart from the facts. Lucius here is a fake runner — no model is called."""

from __future__ import annotations

import asyncio

import pytest

from backend.core import headless_turn
from backend.core.automation_runner import get_automation_approval
from backend.core.errors import ConflictError, ValidationError
from backend.services.paper_trading import autopilot_memory, autopilot_run, facade, qa_quotes
from backend.services.paper_trading.facade import place
from backend.services.paper_trading.service import apply_paper_change, portfolio
from tests.paper_helpers import PRICES

ACME = "inst:inst_acme"


def _new():
    return apply_paper_change(
        {
            "op": "create",
            "name": "Auto",
            "cash": 100_000,
            "home_currency": "USD",
            "mandate": {"goal": "Test ACME", "max_position_pct": 50},
        }
    )["portfolio"]


def _price(acme: float) -> None:
    qa_quotes.set_state("open", {**PRICES, "ACME": acme})


def _trade(pid: str, side: str, qty: float, note: str = "") -> None:
    place(pid, {"asset_key": ACME, "side": side, "quantity": qty, "note": note}, origin="owner")


def _record(pid: str) -> dict:
    return autopilot_memory.track_record(portfolio(pid))


@pytest.fixture
def lucius(monkeypatch):
    seen: dict = {}

    def install(memo="Held everything.", watching="ACME's earnings", lessons=None):
        async def runner(prompt, **kw):
            ctx = get_automation_approval()
            seen["prompt"] = prompt
            facade.record_memo(ctx.paper_portfolio_id, memo, watching, lessons)
            return "done"

        monkeypatch.setattr(headless_turn, "_runner", runner)
        return seen

    return install


def _review(pid: str) -> dict:
    apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto"})
    apply_paper_change({"op": "autopilot_run_now", "id": pid})
    return asyncio.run(autopilot_run.review(pid))


# ── facts ───────────────────────────────────────────────────────────────────────────────────


def test_a_buy_reads_how_it_has_done_since(paper_db):
    pid = _new()["id"]
    _trade(pid, "buy", 20)
    _price(120.0)
    [buy] = _record(pid)["buys"]
    assert buy["symbol"] == "ACME" and buy["now"] == 120.0
    assert buy["change_pct"] == pytest.approx(buy["now"] / buy["price"] - 1)


def test_a_sell_reads_how_the_stock_moved_after_it(paper_db):
    pid = _new()["id"]
    _trade(pid, "buy", 20)
    _price(110.0)
    _trade(pid, "sell", 10, note="Took some profit")
    _price(99.0)
    [sell] = _record(pid)["sells"]
    assert sell["symbol"] == "ACME" and sell["now"] == 99.0 and sell["why"] == "Took some profit"
    # Negative: the stock fell after the sale, so selling saved that much.
    assert sell["move_since_pct"] == pytest.approx(99.0 / sell["price"] - 1)
    assert "avoided" in autopilot_memory.render(_record(pid))


def test_a_stock_with_no_price_now_says_so_and_stays_listed(paper_db, monkeypatch):
    pid = _new()["id"]
    _trade(pid, "buy", 5)
    # No source has a quote now: the quote fetch leaves the asset out, as it does for real.
    monkeypatch.setattr(autopilot_memory.quotes, "fetch", lambda found, now, **kw: {})
    [buy] = _record(pid)["buys"]
    assert buy["now"] is None and buy["change_pct"] is None
    assert "no price now" in autopilot_memory.render(_record(pid))


def test_a_buy_of_a_stock_since_sold_says_so(paper_db):
    pid = _new()["id"]
    _trade(pid, "buy", 10)
    assert _record(pid)["buys"][0]["held"] is True
    _trade(pid, "sell", 10)
    assert _record(pid)["buys"][0]["held"] is False
    assert "since sold" in autopilot_memory.render(_record(pid))


def test_a_lesson_matches_its_stock_whatever_the_case():
    # Review finding: lessons are stored upper-cased; a lower-cased symbol stayed "due" forever.
    class Book:
        def quantity(self, key):
            return 0.0

    fills = [{"side": "sell", "asset_key": "inst:x", "symbol": "acme", "at": "2026-11-20T10:00"}]
    lessons = [{"symbol": "ACME", "lesson": "x", "at": "2026-11-21T10:00"}]
    assert autopilot_memory._lesson_due(Book(), fills, lessons) == []


def test_a_partly_sold_stock_needs_no_lesson_a_fully_sold_one_does(paper_db):
    pid = _new()["id"]
    _trade(pid, "buy", 20)
    _trade(pid, "sell", 5)
    assert _record(pid)["lesson_due"] == []
    _trade(pid, "sell", 15)
    assert _record(pid)["lesson_due"] == ["ACME"]


def test_the_last_reviews_and_what_to_follow_up(paper_db, lucius):
    pid = _new()["id"]
    lucius(memo="Held ACME.", watching="ACME at 95")
    _review(pid)
    rec = _record(pid)
    [r] = rec["reviews"]
    assert r["status"] == "done" and r["did"] == "no trades" and r["watching"] == "ACME at 95"
    assert rec["follow_up"] == "ACME at 95"


def test_the_record_is_capped_newest_first(paper_db, lucius):
    pid = _new()["id"]
    for _ in range(14):
        _trade(pid, "buy", 1)
        _trade(pid, "sell", 1)
    lucius()
    for _ in range(8):
        _review(pid)
    rec = _record(pid)
    assert len(rec["reviews"]) == autopilot_memory.MAX_REVIEWS
    assert len(rec["buys"]) == autopilot_memory.MAX_TRADES
    assert len(rec["sells"]) == autopilot_memory.MAX_TRADES
    assert len(autopilot_memory.render(rec)) <= autopilot_memory.MAX_CHARS


# ── lessons ─────────────────────────────────────────────────────────────────────────────────


def test_a_review_writes_a_lesson_on_a_fully_sold_stock_and_the_next_reads_it(paper_db, lucius):
    pid = _new()["id"]
    _trade(pid, "buy", 10)
    _trade(pid, "sell", 10)
    lesson = {"symbol": "ACME", "lesson": "Sold on the first dip; the stop was set too tight."}
    lucius(lessons=[lesson])
    out = _review(pid)
    assert out["status"] == "done"
    rec = _record(pid)
    assert [(x["symbol"], x["lesson"]) for x in rec["lessons"]] == [("ACME", lesson["lesson"])]
    assert rec["lesson_due"] == []  # written once, after its last sell
    seen = lucius()
    _review(pid)
    assert "your earlier read" in seen["prompt"] and lesson["lesson"] in seen["prompt"]


def test_the_prompt_carries_the_track_record_and_asks_for_due_lessons(paper_db, lucius):
    pid = _new()["id"]
    _trade(pid, "buy", 10)
    _trade(pid, "sell", 10)
    seen = lucius()
    _review(pid)
    assert "Your track record" in seen["prompt"]
    assert "Write a lesson for: ACME" in seen["prompt"]


@pytest.mark.parametrize(
    "lessons, match",
    [
        ([{"symbol": "ACME", "lesson": "x" * 241}], "240"),
        ([{"symbol": f"S{i}", "lesson": "ok"} for i in range(6)], "5 lessons"),
        ([{"symbol": "", "lesson": "ok"}], "symbol"),
    ],
)
def test_lessons_are_bounded(paper_db, lucius, lessons, match):
    pid = _new()["id"]
    apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto"})
    apply_paper_change({"op": "autopilot_run_now", "id": pid})
    from backend.services.paper_trading import autopilot

    autopilot.start(pid, autopilot._now())
    with pytest.raises(ValidationError, match=match):
        facade.record_memo(pid, "Memo.", None, lessons)


def test_a_lesson_is_recorded_only_during_a_review(paper_db):
    pid = _new()["id"]
    with pytest.raises(ConflictError):
        facade.record_memo(pid, "Memo.", None, [{"symbol": "ACME", "lesson": "ok"}])
