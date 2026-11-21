"""The autopilot committee (flag ``autopilot_committee``), steps 3 and 4: after the review proposes,
a separate CHALLENGE turn argues against each proposed buy (keep / shrink one conviction step /
drop), then a RISK turn looks at the buys as a set (approve / trim one step). Neither can add an
order or raise a size; sells always go. Fail-safe, never fail-open: a challenge that doesn't
finish shrinks every buy, a buy it gives no verdict is shrunk, and a risk turn that doesn't finish
drops every buy. Lucius here is a fake runner that answers each turn by its prompt."""

from __future__ import annotations

import asyncio

import pytest

from backend.core import headless_turn
from backend.core.automation_runner import get_automation_approval
from backend.core.errors import ConflictError, ValidationError
from backend.services.paper_trading import autopilot_committee, autopilot_run, facade, views
from backend.services.paper_trading.facade import place
from backend.services.paper_trading.service import apply_paper_change
from tests.flags import flags

ACME = "inst:inst_acme"
BUY = {"asset": "ACME", "action": "buy", "conviction": "high", "why": "Leader", "case": "Grows"}


def _new():
    m = {"goal": "Test ACME", "max_position_pct": 20, "min_cash_pct": 5}
    return apply_paper_change(
        {"op": "create", "name": "Auto", "cash": 100_000, "home_currency": "USD", "mandate": m}
    )["portfolio"]


@pytest.fixture
def lucius(monkeypatch):
    """Answers the research, challenge and risk turns by their prompts."""
    seen: dict = {"turns": []}

    def install(proposals, challenge=None, risk=None, fail=()):
        async def runner(prompt, **kw):
            ctx = get_automation_approval()
            pid = ctx.paper_portfolio_id
            turn = (
                "challenge"
                if autopilot_committee.CHALLENGE_MARK in prompt
                else "risk"
                if autopilot_committee.RISK_MARK in prompt
                else "research"
            )
            seen["turns"].append(turn)
            seen[turn] = {"prompt": prompt, **kw}
            if turn in fail:
                raise RuntimeError(f"{turn} turn broke")
            if turn == "research":
                facade.record_proposals(pid, proposals)
                facade.record_memo(pid, "Proposed a buy.", None)
            elif turn == "challenge" and challenge is not None:
                facade.record_challenge(pid, challenge)
            elif turn == "risk" and risk is not None:
                facade.record_risk_review(pid, risk)
            return "done"

        monkeypatch.setattr(headless_turn, "_runner", runner)
        return seen

    return install


def _review(pid: str) -> dict:
    apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto"})
    apply_paper_change({"op": "autopilot_run_now", "id": pid})
    with flags(autopilot_committee=True):
        return asyncio.run(autopilot_run.review(pid))


def _weight(pid: str) -> float:
    page = views.page(pid)
    return next((h["weight"] for h in page["holdings"] if h["asset_key"] == ACME), 0.0)


def _proposal(pid: str) -> dict:
    return views.page(pid)["autopilot"]["runs"][0]["proposals"][0]


KEEP = [{"asset": "ACME", "verdict": "keep", "against": "Pricey, but the growth covers it."}]
APPROVE = [{"asset": "ACME", "verdict": "approve", "reason": "One name, well under the cap."}]


# ── the turns ───────────────────────────────────────────────────────────────────────────────


def test_a_buy_is_challenged_then_risk_reviewed_then_placed(paper_db, lucius):
    pid = _new()["id"]
    seen = lucius([BUY], challenge=KEEP, risk=APPROVE)
    assert _review(pid)["status"] == "done"
    assert seen["turns"] == ["research", "challenge", "risk"]
    assert 0.09 < _weight(pid) <= 0.10
    p = _proposal(pid)
    assert p["status"] == "placed"
    assert p["challenge"] == {"verdict": "keep", "against": KEEP[0]["against"]}
    assert p["risk"] == {"verdict": "approve", "reason": APPROVE[0]["reason"]}
    # Each turn is offered only its own tools, and reads the proposal it judges.
    assert "record_challenge" in seen["challenge"]["allowed_tools"]
    assert "place_paper_orders" not in seen["challenge"]["allowed_tools"]
    assert "record_risk_review" in seen["risk"]["allowed_tools"]
    assert "Leader" in seen["challenge"]["prompt"] and "Grows" in seen["challenge"]["prompt"]
    assert "10%" in seen["risk"]["prompt"]


def test_a_shrink_buys_one_conviction_step_smaller(paper_db, lucius):
    pid = _new()["id"]
    shrink = [{"asset": "ACME", "verdict": "shrink", "against": "Crowded trade."}]
    lucius([BUY], challenge=shrink, risk=APPROVE)
    _review(pid)
    assert 0.065 < _weight(pid) <= 0.07
    assert _proposal(pid)["conviction"] == "medium"


def test_a_drop_buys_nothing_and_skips_the_risk_turn(paper_db, lucius):
    pid = _new()["id"]
    drop = [{"asset": "ACME", "verdict": "drop", "against": "Accounting questions."}]
    seen = lucius([BUY], challenge=drop, risk=APPROVE)
    _review(pid)
    assert _weight(pid) == 0.0 and seen["turns"] == ["research", "challenge"]
    p = _proposal(pid)
    assert p["status"] == "dropped" and "Accounting questions." in p["detail"]


def test_a_risk_trim_buys_one_step_smaller(paper_db, lucius):
    pid = _new()["id"]
    trim = [{"asset": "ACME", "verdict": "trim", "reason": "Same bet as the rest of the book."}]
    lucius([BUY], challenge=KEEP, risk=trim)
    _review(pid)
    assert 0.065 < _weight(pid) <= 0.07


def test_no_buys_means_no_challenge_or_risk_turn(paper_db, lucius):
    pid = _new()["id"]
    place(pid, {"asset_key": ACME, "side": "buy", "quantity": 50}, origin="owner")
    sell = {"asset": ACME, "action": "sell", "why": "Past its stop"}
    seen = lucius([sell])
    _review(pid)
    assert seen["turns"] == ["research"] and _weight(pid) == 0.0


# ── fail-safe, never fail-open ──────────────────────────────────────────────────────────────


def test_a_challenge_with_no_verdict_shrinks_the_buy(paper_db, lucius):
    pid = _new()["id"]
    lucius([BUY], challenge=None, risk=APPROVE)
    _review(pid)
    assert 0.065 < _weight(pid) <= 0.07
    assert "no verdict" in _proposal(pid)["challenge"]["against"]


def test_a_broken_challenge_turn_shrinks_every_buy(paper_db, lucius):
    pid = _new()["id"]
    lucius([BUY], risk=APPROVE, fail=("challenge",))
    assert _review(pid)["status"] == "done"
    assert 0.065 < _weight(pid) <= 0.07


def test_a_broken_risk_turn_drops_the_buys_but_sells_still_go(paper_db, lucius):
    pid = _new()["id"]
    place(pid, {"asset_key": "inst:inst_coin", "side": "buy", "quantity": 10}, origin="owner")
    sell = {"asset": "inst:inst_coin", "action": "sell", "why": "Past its stop"}
    lucius([BUY, sell], challenge=KEEP, fail=("risk",))
    assert _review(pid)["status"] == "done"
    assert _weight(pid) == 0.0
    by = {p["action"]: p for p in views.page(pid)["autopilot"]["runs"][0]["proposals"]}
    assert by["sell"]["status"] == "placed"
    assert by["buy"]["status"] == "dropped" and "risk review" in by["buy"]["detail"]


# ── what a verdict must say ─────────────────────────────────────────────────────────────────


def _running(pid: str, proposals: list[dict]) -> None:
    apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto"})
    from backend.services.paper_trading import autopilot

    autopilot.start(pid, autopilot._now())
    facade.record_proposals(pid, proposals)


@pytest.mark.parametrize(
    "verdicts, match",
    [
        ([{"asset": "ZZZ", "verdict": "keep", "against": "x"}], "ACME"),
        ([{"asset": "ACME", "verdict": "maybe", "against": "x"}], "keep, shrink or drop"),
        ([{"asset": "ACME", "verdict": "keep", "against": ""}], "against"),
    ],
)
def test_a_challenge_verdict_names_a_proposed_buy_and_says_why(paper_db, verdicts, match):
    pid = _new()["id"]
    _running(pid, [BUY])
    with pytest.raises(ValidationError, match=match):
        facade.record_challenge(pid, verdicts)


def test_a_risk_verdict_can_only_approve_or_trim(paper_db):
    pid = _new()["id"]
    _running(pid, [BUY])
    with pytest.raises(ValidationError, match="approve or trim"):
        facade.record_risk_review(pid, [{"asset": "ACME", "verdict": "raise", "reason": "x"}])


def test_verdicts_are_recorded_only_during_a_review(paper_db):
    pid = _new()["id"]
    with pytest.raises(ConflictError):
        facade.record_challenge(pid, KEEP)
    with pytest.raises(ConflictError):
        facade.record_risk_review(pid, APPROVE)


# ── pure rules ──────────────────────────────────────────────────────────────────────────────


def test_shrinking_steps_down_and_a_low_one_drops():
    assert autopilot_committee.step_down("high") == "medium"
    assert autopilot_committee.step_down("medium") == "low"
    assert autopilot_committee.step_down("low") is None
