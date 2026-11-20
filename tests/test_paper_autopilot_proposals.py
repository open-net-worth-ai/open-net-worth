"""Autopilot proposes, the app sizes (flag ``autopilot_committee``): a review stops placing orders
and records proposals — buy (new or add), trim or sell, each with a conviction and a reason. Code
sizes them (low 4%, medium 7%, high 10% of the portfolio, under the mandate's cap and above its
cash floor), places sells first, then buys by conviction, through the normal paper choke, and
logs every proposal with what happened to it. Flag off: the review places its own orders, as
before. Lucius here is a fake runner — no model is called."""

from __future__ import annotations

import asyncio

import pytest

from backend.core import headless_turn
from backend.core.automation_runner import get_automation_approval
from backend.core.errors import ConflictError, ValidationError
from backend.services.paper_trading import autopilot_run, autopilot_sizing, facade, views
from backend.services.paper_trading.facade import place
from backend.services.paper_trading.service import apply_paper_change
from tests.flags import flags

ACME = "inst:inst_acme"


def _new(**mandate):
    m = {"goal": "Test ACME", "max_position_pct": 20, "min_cash_pct": 5, **mandate}
    return apply_paper_change(
        {"op": "create", "name": "Auto", "cash": 100_000, "home_currency": "USD", "mandate": m}
    )["portfolio"]


def _hold(pid: str, qty: float) -> None:
    place(pid, {"asset_key": ACME, "side": "buy", "quantity": qty}, origin="owner")


@pytest.fixture
def lucius(monkeypatch):
    seen: dict = {}

    def install(proposals=None):
        async def runner(prompt, **kw):
            ctx = get_automation_approval()
            seen.update(prompt=prompt, **kw)
            if proposals is not None:
                facade.record_proposals(ctx.paper_portfolio_id, proposals)
            facade.record_memo(ctx.paper_portfolio_id, "Proposed what the record shows.", None)
            return "done"

        monkeypatch.setattr(headless_turn, "_runner", runner)
        return seen

    return install


def _review(pid: str) -> dict:
    apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto"})
    apply_paper_change({"op": "autopilot_run_now", "id": pid})
    return asyncio.run(autopilot_run.review(pid))


def _weight(pid: str, key: str = ACME) -> float:
    page = views.page(pid)
    return next((h["weight"] for h in page["holdings"] if h["asset_key"] == key), 0.0)


def _last_run(pid: str) -> dict:
    return views.page(pid)["autopilot"]["runs"][0]


# ── sizing (pure) ───────────────────────────────────────────────────────────────────────────


def test_conviction_sets_the_target_weight():
    for conviction, weight in (("low", 0.04), ("medium", 0.07), ("high", 0.10)):
        amount, _ = autopilot_sizing.buy_amount(
            conviction=conviction, total=100_000, current=0, cash=100_000, floor_pct=5, cap_pct=20
        )
        assert amount == pytest.approx(weight * 100_000 * autopilot_sizing.FILL_ROOM)


def test_a_buy_never_passes_the_cap_or_the_cash_floor():
    amount, why = autopilot_sizing.buy_amount(
        conviction="high", total=100_000, current=0, cash=100_000, floor_pct=5, cap_pct=6
    )
    assert amount == pytest.approx(6_000 * autopilot_sizing.FILL_ROOM) and "cap" in why
    amount, why = autopilot_sizing.buy_amount(
        conviction="high", total=100_000, current=0, cash=8_000, floor_pct=5, cap_pct=20
    )
    assert amount == pytest.approx(3_000 * autopilot_sizing.FILL_ROOM) and "cash" in why


def test_a_holding_already_at_its_target_is_left_alone():
    amount, why = autopilot_sizing.buy_amount(
        conviction="medium", total=100_000, current=7_200, cash=50_000, floor_pct=5, cap_pct=20
    )
    assert amount == 0 and "already" in why


def test_a_trim_sells_down_to_the_conviction_weight():
    amount, _ = autopilot_sizing.trim_amount(conviction="low", total=100_000, current=15_000)
    assert amount == pytest.approx(11_000)
    amount, why = autopilot_sizing.trim_amount(conviction="high", total=100_000, current=9_000)
    assert amount == 0 and "below" in why


def test_whole_shares_for_stocks_fractions_for_the_rest():
    assert autopilot_sizing.units(9_850, 100.0, fractional=False) == 98
    assert autopilot_sizing.units(125, 50.0, fractional=True) == pytest.approx(2.5)


# ── a review with the flag on ───────────────────────────────────────────────────────────────


def test_flag_on_the_review_proposes_and_cannot_place(paper_db, lucius):
    pid = _new()["id"]
    seen = lucius(proposals=[])
    with flags(autopilot_committee=True):
        _review(pid)
    assert "propose_paper_orders" in seen["allowed_tools"]
    assert "place_paper_orders" not in seen["allowed_tools"]
    assert "propose_paper_orders" in seen["prompt"]


def test_flag_off_the_review_places_its_own_orders_as_before(paper_db, lucius):
    pid = _new()["id"]
    seen = lucius()
    with flags(autopilot_committee=False):
        _review(pid)
    assert "place_paper_orders" in seen["allowed_tools"]
    assert "propose_paper_orders" not in seen["allowed_tools"]


def test_a_new_name_is_bought_to_its_conviction_weight(paper_db, lucius):
    pid = _new()["id"]
    lucius(proposals=[{"asset": "ACME", "action": "buy", "conviction": "high", "why": "Leader"}])
    with flags(autopilot_committee=True):
        out = _review(pid)
    assert out["status"] == "done"
    assert 0.09 < _weight(pid) <= 0.10
    [p] = _last_run(pid)["proposals"]
    assert p["status"] == "placed" and p["conviction"] == "high" and p["why"] == "Leader"
    assert "10%" in p["detail"]


def test_a_holding_competes_and_is_topped_up_to_its_weight(paper_db, lucius):
    pid = _new()["id"]
    _hold(pid, 30)  # about 3%
    lucius(proposals=[{"asset": ACME, "action": "buy", "conviction": "medium", "why": "Add"}])
    with flags(autopilot_committee=True):
        _review(pid)
    assert 0.065 < _weight(pid) <= 0.07


def test_sells_go_first_and_a_sell_exits_in_full(paper_db, lucius):
    pid = _new()["id"]
    _hold(pid, 150)
    proposals = [
        {"asset": "ACOIN", "action": "buy", "conviction": "low", "why": "Diversify"},
        {"asset": ACME, "action": "sell", "why": "Past its stop"},
    ]
    lucius(proposals=proposals)
    with flags(autopilot_committee=True):
        _review(pid)
    assert _weight(pid) == 0.0
    run = _last_run(pid)
    assert [p["action"] for p in run["proposals"]] == ["sell", "buy"]
    assert all(p["status"] == "placed" for p in run["proposals"])


def test_a_trim_sells_down_to_its_weight(paper_db, lucius):
    pid = _new()["id"]
    _hold(pid, 150)  # about 15%
    lucius(proposals=[{"asset": ACME, "action": "trim", "conviction": "low", "why": "Too big"}])
    with flags(autopilot_committee=True):
        _review(pid)
    assert 0.035 < _weight(pid) < 0.045


def test_a_proposal_the_mandate_refuses_is_logged_not_fatal(paper_db, lucius, monkeypatch):
    # Offline: the market lookup for a ticker the app has never seen finds nothing.
    monkeypatch.setattr(
        "backend.services.instruments.resolve_instrument", lambda q, **kw: (None, False)
    )
    pid = _new(max_positions=1)["id"]
    _hold(pid, 30)
    proposals = [
        {"asset": "ACOIN", "action": "buy", "conviction": "medium", "why": "Second name"},
        {"asset": "NOSUCHTICKER", "action": "buy", "conviction": "low", "why": "Unknown"},
    ]
    lucius(proposals=proposals)
    with flags(autopilot_committee=True):
        out = _review(pid)
    assert out["status"] == "done"
    by_asset = {p["asset"]: p for p in _last_run(pid)["proposals"]}
    assert by_asset["ACOIN"]["status"] == "skipped" and "holdings" in by_asset["ACOIN"]["detail"]
    assert by_asset["NOSUCHTICKER"]["status"] == "refused"


def test_no_proposals_places_nothing(paper_db, lucius):
    pid = _new()["id"]
    lucius(proposals=None)
    with flags(autopilot_committee=True):
        out = _review(pid)
    assert out["status"] == "done" and _last_run(pid)["proposals"] == []
    assert _last_run(pid)["orders"] == []


# ── what a proposal must say ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "proposal, match",
    [
        ({"asset": "ACME", "action": "buy", "why": "x"}, "conviction"),
        ({"asset": "ACME", "action": "hold", "conviction": "low", "why": "x"}, "buy, trim or sell"),
        ({"asset": "", "action": "sell", "why": "x"}, "asset"),
        ({"asset": "ACME", "action": "sell", "why": ""}, "why"),
    ],
)
def test_a_proposal_says_what_why_and_how_sure(paper_db, proposal, match):
    pid = _new()["id"]
    apply_paper_change({"op": "autopilot", "id": pid, "mode": "auto"})
    from backend.services.paper_trading import autopilot

    autopilot.start(pid, autopilot._now())
    with pytest.raises(ValidationError, match=match):
        facade.record_proposals(pid, [proposal])


def test_proposals_are_recorded_only_during_a_review(paper_db):
    pid = _new()["id"]
    with pytest.raises(ConflictError):
        facade.record_proposals(pid, [{"asset": "ACME", "action": "sell", "why": "x"}])
