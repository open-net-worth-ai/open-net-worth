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
from backend.services.paper_trading import autopilot, autopilot_run, views
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
            seen.update(prompt=prompt, ctx=get_automation_approval(), **kw)
            if fail:
                raise fail
            if act:
                act()
            return memo

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
    assert set(seen["allowed_tools"]) == set(autopilot_run.TOOLS)
    assert not {"add_transactions", "web_search", "create_automation"} & set(seen["allowed_tools"])
    ctx = seen["ctx"]
    assert ctx.simulation_only and ctx.paper_portfolio_id == pid
    assert "max 20% per holding" in seen["prompt"] and "Prefer adding to winners." in seen["prompt"]
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
