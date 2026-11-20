"""Paper autopilot: Manual or Auto. Auto needs a mandate; a review is one Lucius turn offered only
reads and this portfolio's paper tools, under the paper-only guard bound to the portfolio; its
orders are "autopilot", can never break the mandate, and only happen
during a review; the memo is logged; two failures in a row pause it; Start over keeps the
settings, not the log. Lucius here is a fake runner — no model is called."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.core import headless_turn
from backend.core.automation_runner import get_automation_approval
from backend.core.errors import ConflictError, ValidationError
from backend.services.paper_trading import autopilot, autopilot_run, facade, views
from backend.services.paper_trading.facade import place
from backend.services.paper_trading.service import apply_paper_change

MANDATE = {"goal": "Test ACME", "max_position_pct": 20}


def _new(m: dict | None = MANDATE):
    return apply_paper_change(
        {"op": "create", "name": "Auto", "cash": 10_000, "home_currency": "USD", "mandate": m}
    )["portfolio"]


def _auto(pid: str, **kw) -> dict:
    return apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto", **kw})["portfolio"]


def _buy(pid, qty, **kw):
    order = {"asset_key": "inst:inst_acme", "side": "buy", "quantity": qty, **kw}
    return place(pid, order, origin="autopilot")


@pytest.fixture
def lucius(monkeypatch):
    """A fake runner: records what it was offered, runs ``act`` as the model's tool calls."""
    seen: dict = {}

    def install(act=None, memo="Bought a little ACME; watching its earnings.", fail=None):
        async def runner(prompt, **kw):
            ctx = get_automation_approval()
            seen.update(prompt=prompt, ctx=ctx, **kw)
            if fail:
                raise fail
            if act:
                act()
            if memo:  # as the model does: the memo tool, then a reply that is never kept
                facade.record_memo(ctx.paper_portfolio_id, memo, "ACME's earnings")
            return "Let me redo this properly with correct calls. Done."

        monkeypatch.setattr(headless_turn, "_runner", runner)
        return seen

    return install


def _review(pid: str) -> dict:
    apply_paper_change({"op": "autopilot_run_now", "id": pid})
    return asyncio.run(autopilot_run.review(pid))


# ── settings ────────────────────────────────────────────────────────────────────────────────


def test_auto_needs_a_mandate(paper_db):
    pid = _new(None)["id"]
    with pytest.raises(ValidationError, match="Set a mandate first"):
        _auto(pid)


def test_auto_schedules_the_next_review_and_manual_clears_it(paper_db):
    pid = _new()["id"]
    a = views.page(pid)["autopilot"]
    assert a["mode"] == "manual" and a["next_run_at"] is None
    a = autopilot.view(_auto(pid, cadence="weekly"))
    nxt = datetime.fromisoformat(a["next_run_at"])
    assert (
        nxt > datetime.now(timezone.utc)
        and nxt.astimezone(autopilot.user_now().tzinfo).weekday() == 0
    )
    off = apply_paper_change({"op": "autopilot", "id": pid, "mode": "manual"})["portfolio"]
    assert autopilot.view(off)["next_run_at"] is None


def test_review_now_needs_auto(paper_db):
    pid = _new()["id"]
    with pytest.raises(ConflictError, match="Turn autopilot on"):
        apply_paper_change({"op": "autopilot_run_now", "id": pid})


# ── a review ────────────────────────────────────────────────────────────────────────────────


def test_a_review_is_offered_only_reads_and_paper_tools_under_the_paper_guard(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid, instructions="Prefer adding to winners.")
    seen = lucius(act=lambda: _buy(pid, 10, note="Within the cap"))
    out = _review(pid)
    assert out["status"] == "done"
    assert seen["enforce_offer"] is True
    assert seen["max_tool_calls"] == autopilot_run.MAX_TOOL_CALLS
    assert set(seen["allowed_tools"]) == set(autopilot_run.TOOLS)
    assert not {"add_transactions", "fetch_page", "create_automation"} & set(seen["allowed_tools"])
    assert seen["ctx"].outbound_reads == autopilot_run.OUTBOUND
    ctx = seen["ctx"]
    assert ctx.simulation_only and ctx.paper_portfolio_id == pid
    assert "max 20% per holding" in seen["prompt"] and "Prefer adding to winners." in seen["prompt"]
    # Every review hunts for new names that fit the goal, and idle cash needs a reason
    # (owner, 2026-11-20: reviews that only checked holdings sold stops and sat on the cash).
    assert "Look for new names, every review" in seen["prompt"]
    assert "Cash well above its floor is a choice too" in seen["prompt"]
    # The new-name checks use only tools the review is offered.
    for name in ("web_search", "get_asset_fundamentals", "preview_paper_orders"):
        assert name in seen["prompt"] and name in autopilot_run.TOOLS
    [run] = views.page(pid)["autopilot"]["runs"]
    assert run["status"] == "done" and run["memo"].startswith("Bought a little ACME")
    assert [o["status"] for o in run["orders"]] == ["filled"]


def test_a_review_can_never_break_the_mandate(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid)
    errors: list[str] = []

    def act():
        try:
            _buy(pid, 30, override_mandate=True)  # 30% > 20%, and asking to override
        except ConflictError as exc:
            errors.append(str(exc))

    lucius(act=act)
    _review(pid)
    assert errors and "breaks the portfolio's mandate" in errors[0]
    assert views.page(pid)["holdings"] == []


def test_a_review_has_no_cap_on_its_orders(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid)
    lucius(act=lambda: [_buy(pid, 1) for _ in range(8)])
    out = _review(pid)
    assert len(out["orders"]) == 8


def test_the_autopilot_trades_only_during_a_review(paper_db):
    pid = _new()["id"]
    _auto(pid)
    with pytest.raises(ConflictError, match="only during a review"):
        _buy(pid, 1)


def test_two_failed_reviews_pause_it_and_auto_again_resumes(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid)
    lucius(fail=RuntimeError("model down"))
    assert _review(pid)["status"] == "failed"
    assert views.page(pid)["autopilot"]["paused_reason"] is None
    apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto"})
    _review(pid)
    a = views.page(pid)["autopilot"]
    assert a["paused_reason"].startswith("Paused after two failed reviews")
    assert a["next_run_at"] is None and autopilot.due(datetime.now(timezone.utc)) == []
    resumed = autopilot.view(_auto(pid))
    assert resumed["paused_reason"] is None and resumed["next_run_at"]


def test_only_due_auto_portfolios_are_reviewed(paper_db, lucius):
    manual = _new()["id"]
    auto = _new()["id"]
    _auto(auto)
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert [r["id"] for r in autopilot.due(later)] == [auto]
    assert manual not in [r["id"] for r in autopilot.due(later)]


def test_start_over_keeps_the_settings_not_the_log(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid, cadence="weekly", instructions="Keep it simple.")
    lucius()
    _review(pid)
    nxt = apply_paper_change({"op": "start_over", "id": pid})["portfolio"]
    a = views.page(nxt["id"])["autopilot"]
    assert (a["mode"], a["cadence"], a["instructions"]) == ("auto", "weekly", "Keep it simple.")
    assert a["runs"] == [] and a["next_run_at"]


# ── model ───────────────────────────────────────────────────────────────────────────────────


def test_a_review_runs_on_the_portfolios_own_model(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid, model="anthropic:claude-haiku-5-5", effort="low")
    seen = lucius()
    _review(pid)
    assert seen["model"] == "anthropic:claude-haiku-5-5" and seen["effort"] == "low"
    [run] = views.page(pid)["autopilot"]["runs"]
    assert (run["model"], run["effort"]) == ("anthropic:claude-haiku-5-5", "low")


def test_the_default_model_is_left_to_the_app(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid)
    seen = lucius()
    _review(pid)
    assert seen["model"] is None and seen["effort"] == "medium"
    assert views.page(pid)["autopilot"]["model"] == ""


def test_effort_is_low_medium_or_high(paper_db):
    pid = _new()["id"]
    with pytest.raises(ValidationError, match="Effort is low, medium or high"):
        _auto(pid, effort="max")


# ── memo ────────────────────────────────────────────────────────────────────────────────────


def test_the_memo_is_what_lucius_recorded_never_his_reply(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid)
    lucius(memo="Held everything: nothing broke a limit.")
    _review(pid)
    [run] = views.page(pid)["autopilot"]["runs"]
    assert run["memo"] == "Held everything: nothing broke a limit."
    assert run["watching"] == "ACME's earnings"
    assert "redo this" not in run["memo"]


def test_a_review_without_a_memo_fails(paper_db, lucius):
    pid = _new()["id"]
    _auto(pid)
    lucius(memo=None)
    _review(pid)
    [run] = views.page(pid)["autopilot"]["runs"]
    assert run["status"] == "failed"
    assert run["error"] == "Lucius finished without writing his memo."


def test_a_memo_is_recorded_only_during_a_review(paper_db):
    pid = _new()["id"]
    _auto(pid)
    with pytest.raises(ConflictError, match="only during an autopilot review"):
        facade.record_memo(pid, "Sneaky.", None)


def test_the_memo_tool_refuses_outside_an_autopilot_review(paper_db):
    from backend.ai.tools.paper_trading import record_autopilot_memo

    text, _ = asyncio.run(record_autopilot_memo({"memo": "Hello"}))
    assert "only for a paper autopilot review" in text


# ── news ────────────────────────────────────────────────────────────────────────────────────

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Acme beats estimates</title><link>https://example.com/a</link>
<pubDate>Tue, 17 Nov 2026 14:00:00 GMT</pubDate></item>
<item><title>Acme beats estimates</title><link>https://example.com/a2</link>
<pubDate>Tue, 17 Nov 2026 13:00:00 GMT</pubDate></item>
<item><title>Old Acme story</title><link>https://example.com/b</link>
<pubDate>Mon, 01 Sep 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return RSS


def _holding(pid: str) -> None:
    place(pid, {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 5})


def test_paper_news_reads_each_holdings_feeds(paper_db, monkeypatch):
    from backend.services.paper_trading import news

    asked: list[str] = []

    def fake_open(url, **kw):
        asked.append(url)
        return _Resp()

    monkeypatch.setattr(news, "pinned_urlopen", fake_open)
    news._cache.clear()
    pid = _new(None)["id"]
    _holding(pid)
    [acme] = facade.news(pid)["holdings"]
    assert acme["symbol"] == "ACME" and acme["note"] is None
    # Newest first, the same headline once, nothing older than two weeks.
    assert [h["title"] for h in acme["headlines"]] == ["Acme beats estimates"]
    # Only the holding's own feed addresses, built by code.
    assert asked and all("news.google.com" in u or "yahoo.com" in u for u in asked)


def test_a_holding_whose_feeds_fail_says_so(paper_db, monkeypatch):
    from backend.services.paper_trading import news

    def boom(url, **kw):
        raise OSError("down")

    monkeypatch.setattr(news, "pinned_urlopen", boom)
    news._cache.clear()
    pid = _new(None)["id"]
    _holding(pid)
    [acme] = facade.news(pid)["holdings"]
    assert acme["headlines"] == []
    assert acme["note"] == "Its news feeds could not be read just now."
