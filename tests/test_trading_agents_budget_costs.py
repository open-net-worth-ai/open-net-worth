"""TradingAgents spend: the estimate, the monthly cap, and the cost figures — synthetic data only
(ticker ACME, made-up models and prices).

The estimate is the median of the last finished runs with the same models, analysts and rounds,
else a static heuristic; a run on CLI subscriptions is plan-included and never blocked by the
dollar cap; the cost panel reads the permanent run rows.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.cost import router as cost_router
from backend.core.errors import BudgetExceededError, FeatureDisabledError, ModelUnavailableError
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import cost as cost_service
from backend.services.trading_agents import budget, costs, settings
from backend.services.trading_agents.runs import apply_run_change
from backend.services.trading_agents.settings import ResolvedModels

pytestmark = pytest.mark.flags(trading_agents=True)

API = ResolvedModels(
    deep="acme-ai:model-a", quick="acme-ai:model-b", deep_transport="api", quick_transport="api"
)
CLI = ResolvedModels(
    deep="acme-cli:model-a",
    quick="acme-cli:model-b",
    deep_transport="claude_cli",
    quick_transport="claude_cli",
)
MIXED = replace(API, deep_transport="claude_cli")
ANALYSTS = ["market", "news"]


@pytest.fixture(autouse=True)
def isolated_db(db_isolated):
    return db_isolated


def _rate_for(model: str):
    return budget.Rate(3.0, 15.0)


def _run(models=API, analysts=ANALYSTS, ticker="ACME", cfg=None, finish=True, **usage):
    cfg = cfg or settings.load()
    run = apply_run_change(
        "create",
        ticker=ticker,
        analysts=analysts,
        config_snapshot=budget.config_snapshot(cfg, models),
        context_level="ticker",
        cap_usd=2.0,
    )
    apply_run_change("start", run_id=run["id"])
    if usage:
        apply_run_change("usage", run_id=run["id"], **{"llm_calls": 1, **usage})
    if finish:
        run = apply_run_change("finish", run_id=run["id"], signal="Hold")
    return run


def _queued(estimate: float) -> dict:
    return apply_run_change(
        "create",
        ticker="ACME",
        analysts=ANALYSTS,
        config_snapshot={},
        context_level="ticker",
        cap_usd=2.0,
        estimate_usd=estimate,
    )


def _age(run_id: str, days: int) -> None:
    store = TradingAgentsRunStore()
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with store.transaction() as conn:
        store.update(run_id, {"created_at": stamp}, conn)


# ── estimate ─────────────────────────────────────────────────────────────────


def test_a_new_setup_is_estimated_statically():
    got = budget.estimate(settings.load(), API, ANALYSTS, _rate_for)
    assert (got.basis, got.sample_size, got.calls) == ("static", 0, 26)
    # 2 deep calls at 12k in / 1.5k out, 24 quick at 6k in / 0.8k out, at $3 / $15 per Mtok.
    assert got.median_usd == pytest.approx(0.837)
    assert (got.low_usd, got.high_usd) == (got.median_usd, got.median_usd)
    assert not got.plan_included and got.priced


def test_three_matching_runs_switch_the_estimate_to_history():
    for cost in (0.30, 0.50):
        _run(cost_usd=cost, llm_calls=20)
    assert budget.estimate(settings.load(), API, ANALYSTS, _rate_for).basis == "static"
    _run(cost_usd=0.40, llm_calls=22)
    got = budget.estimate(settings.load(), API, ANALYSTS, _rate_for)
    assert (got.basis, got.sample_size) == ("history", 3)
    assert got.median_usd == pytest.approx(0.40)
    assert got.low_usd == pytest.approx(0.32) and got.high_usd == pytest.approx(0.48)
    assert got.calls == 20


def test_only_runs_with_the_same_models_analysts_and_rounds_count():
    for _ in range(3):
        _run(analysts=["market"], cost_usd=9.0)
        _run(models=replace(API, quick="acme-ai:model-c"), cost_usd=9.0)
        _run(cfg=replace(settings.load(), max_debate_rounds=2), cost_usd=9.0)
        _run(finish=False, cost_usd=9.0)
    got = budget.estimate(settings.load(), API, ANALYSTS, _rate_for)
    assert got.basis == "static" and got.median_usd == pytest.approx(0.837)


def test_history_uses_only_the_last_ten():
    for cost in [5.0] * 4 + [1.0] * 10:
        _run(cost_usd=cost)
    got = budget.estimate(settings.load(), API, ANALYSTS, _rate_for)
    assert (got.sample_size, got.median_usd) == (10, 1.0)


def test_a_cli_only_run_is_plan_included_and_free():
    got = budget.estimate(settings.load(), CLI, ANALYSTS, _rate_for)
    assert got.as_dict()["estimate_usd"] == 0.0
    assert got.plan_included and got.calls == 26 and got.priced


def test_a_mixed_run_prices_only_its_metered_tier():
    got = budget.estimate(settings.load(), MIXED, ANALYSTS, _rate_for)
    assert not got.plan_included
    assert got.median_usd == pytest.approx(0.72)


def test_a_model_without_a_price_is_flagged_not_priced_at_zero_silently():
    got = budget.estimate(settings.load(), API, ANALYSTS, lambda model: None)
    assert got.median_usd == 0.0 and not got.priced


def test_a_cli_on_a_stored_api_key_is_billed_so_it_is_metered_not_plan_included():
    keyed = replace(CLI, deep_keyed=True, quick_keyed=True)
    got = budget.estimate(settings.load(), keyed, ANALYSTS, _rate_for)
    assert not got.plan_included and got.median_usd == pytest.approx(0.837)
    half = replace(CLI, quick_keyed=True)
    assert not budget.estimate(settings.load(), half, ANALYSTS, _rate_for).plan_included


def test_an_unknown_transport_name_is_metered_never_assumed_to_be_a_subscription():
    odd = replace(API, deep_transport="claude_cli_plus", quick_transport="claude_cli_plus")
    assert not budget.estimate(settings.load(), odd, ANALYSTS, _rate_for).plan_included


def test_a_local_model_costs_nothing_and_is_not_unpriced():
    local = replace(API, deep_local=True, quick_local=True)
    got = budget.estimate(settings.load(), local, ANALYSTS, lambda model: None)
    assert got.median_usd == 0.0 and got.priced and got.unpriced_models == ()


def test_a_local_endpoint_serving_a_priced_model_is_estimated_like_any_other():
    """A proxy on loopback may forward to a paid model: a known price is never zeroed for locality."""
    local = replace(API, deep_local=True, quick_local=True)
    got = budget.estimate(settings.load(), local, ANALYSTS, _rate_for)
    assert got.median_usd == pytest.approx(0.837) and got.priced


def test_the_unpriced_models_are_named_in_the_estimate():
    got = budget.estimate(settings.load(), API, ANALYSTS, lambda model: None)
    assert got.unpriced_models == ("acme-ai:model-a", "acme-ai:model-b")
    assert got.as_dict()["unpriced_models"] == ["acme-ai:model-a", "acme-ai:model-b"]


def test_a_metered_model_with_no_price_is_refused_by_name_unless_it_is_local():
    cfg = replace(settings.load(), monthly_cap_usd=50.0)
    with pytest.raises(ModelUnavailableError) as raised:
        budget.check_can_start(cfg, API, ANALYSTS, lambda model: None)
    assert "acme-ai:model-a" in raised.value.message
    assert raised.value.context["model_ref"] == "acme-ai:model-a"
    local = replace(API, deep_local=True, quick_local=True)
    assert budget.check_can_start(cfg, local, ANALYSTS, lambda model: None).priced
    assert budget.check_can_start(cfg, CLI, ANALYSTS, lambda model: None).plan_included


# ── monthly cap ──────────────────────────────────────────────────────────────


def test_a_run_the_month_cannot_afford_is_refused_with_the_numbers():
    cfg = replace(settings.load(), monthly_cap_usd=1.0)
    _run(cost_usd=0.50)
    with pytest.raises(BudgetExceededError) as raised:
        budget.check_can_start(cfg, API, ANALYSTS, _rate_for)
    assert "$0.84" in raised.value.message and "$0.50" in raised.value.message
    assert "$1.00" in raised.value.message
    assert raised.value.context["month_spend_usd"] == 0.5
    assert raised.value.context["monthly_cap_usd"] == 1.0


def test_a_run_still_queued_or_going_counts_its_estimate_against_the_month():
    cfg = replace(settings.load(), monthly_cap_usd=1.0)
    apply_run_change(
        "create",
        ticker="ACME",
        analysts=ANALYSTS,
        config_snapshot={},
        context_level="ticker",
        cap_usd=2.0,
        estimate_usd=0.60,
    )
    assert budget.month_spend() == 0.0 and budget.month_committed() == pytest.approx(0.60)
    with pytest.raises(BudgetExceededError):
        budget.check_can_start(cfg, API, ANALYSTS, _rate_for)


def test_a_running_run_counts_what_it_has_spent_when_that_passes_its_estimate():
    run = _queued(estimate=0.20)
    apply_run_change("start", run_id=run["id"])
    apply_run_change("usage", run_id=run["id"], llm_calls=1, cost_usd=0.90)
    assert budget.month_committed() == pytest.approx(0.90)


def test_a_finished_run_counts_what_it_cost_not_what_it_was_estimated_at():
    run = _queued(estimate=5.0)
    apply_run_change("start", run_id=run["id"])
    apply_run_change("usage", run_id=run["id"], llm_calls=1, cost_usd=0.10)
    apply_run_change("finish", run_id=run["id"], signal="Hold")
    assert budget.month_committed() == pytest.approx(0.10)


def test_the_live_limit_is_the_run_cap_or_what_is_left_of_the_month():
    cfg = replace(settings.load(), per_run_cap_usd=2.0, monthly_cap_usd=10.0)
    assert budget.run_limit_usd(cfg, 0.0) == 2.0
    assert budget.run_limit_usd(cfg, 9.25) == pytest.approx(0.75)
    assert budget.run_limit_usd(cfg, 12.0) == 0.0


def test_a_run_inside_the_month_is_allowed_and_returns_its_estimate():
    cfg = replace(settings.load(), monthly_cap_usd=5.0)
    _run(cost_usd=0.50)
    assert budget.check_can_start(cfg, API, ANALYSTS, _rate_for).median_usd == pytest.approx(0.837)


def test_last_months_spend_does_not_count():
    cfg = replace(settings.load(), monthly_cap_usd=1.0)
    old = _run(cost_usd=50.0)
    _age(old["id"], 62)
    assert budget.check_can_start(cfg, API, ANALYSTS, _rate_for).median_usd > 0


def test_a_cap_of_zero_allows_no_metered_run_but_a_plan_included_one():
    cfg = replace(settings.load(), monthly_cap_usd=0.0)
    with pytest.raises(BudgetExceededError):
        budget.check_can_start(cfg, API, ANALYSTS, _rate_for)
    assert budget.check_can_start(cfg, CLI, ANALYSTS, _rate_for).plan_included


def test_a_plan_included_run_is_not_blocked_by_an_exhausted_month():
    cfg = replace(settings.load(), monthly_cap_usd=1.0)
    _run(cost_usd=10.0)
    assert budget.check_can_start(cfg, CLI, ANALYSTS, _rate_for).plan_included


def test_the_month_starts_in_the_owners_zone(monkeypatch):
    tokyo = datetime(2026, 11, 1, 0, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    monkeypatch.setattr(budget, "user_now", lambda: tokyo)
    assert budget.month_start_utc() == "2026-10-31T15:00:00+00:00"


# ── bucket ───────────────────────────────────────────────────────────────────


def test_trading_agents_calls_have_their_own_surface_bucket():
    assert cost_service._source_to_bucket("trading_agents") == "trading_agents"
    assert cost_service._source_to_bucket("chat") == "chat"
    assert cost_service._source_to_bucket("trading_agents_other") == "other"


# ── cost figures ─────────────────────────────────────────────────────────────


def test_the_cost_route_takes_an_explicit_window(client):
    old = _run(cost_usd=0.40)
    _age(old["id"], 10)
    _run(cost_usd=0.25)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    got = client.get(
        "/api/cost/trading-agents", params={"start": week_ago, "end": today, "tz": "UTC"}
    )
    assert got.status_code == 200
    body = got.json()
    assert body["window"] == {"days": 30, "start": week_ago, "end": today}
    assert body["totals"]["runs"] == 1 and body["totals"]["metered_usd"] == pytest.approx(0.25)
    only_end = client.get("/api/cost/trading-agents", params={"end": week_ago, "tz": "UTC"}).json()
    assert only_end["totals"]["runs"] == 1 and only_end["totals"]["metered_usd"] == pytest.approx(
        0.4
    )
    # an explicit window beats the preset, as in /api/cost/summary
    both = client.get(
        "/api/cost/trading-agents",
        params={"range": "today", "start": week_ago, "end": today, "tz": "UTC"},
    ).json()
    assert both["totals"]["runs"] == 1 and both["window"]["start"] == week_ago
    assert client.get("/api/cost/trading-agents", params={"start": "last week"}).status_code == 422


def _book():
    _run(
        cost_usd=0.10,
        input_tokens=1000,
        output_tokens=100,
        role="analyst_market",
        model="acme-ai:model-b",
    )
    first = _run(
        cost_usd=0.20,
        llm_calls=2,
        unpriced_calls=1,
        role="portfolio_manager",
        model="acme-ai:model-a",
    )
    _run(
        models=CLI,
        llm_calls=3,
        plan_included_calls=3,
        input_tokens=500,
        role="analyst_news",
        model="acme-cli:model-b",
    )
    failed = _run(ticker="BETA", finish=False, cost_usd=0.05, role="analyst_market")
    apply_run_change("fail", run_id=failed["id"], error="boom")
    cancelled = _run(ticker="BETA", finish=False)
    apply_run_change("cancel", run_id=cancelled["id"])
    stopped = _run(ticker="GAMMA", finish=False, cost_usd=0.5)
    apply_run_change("fail", run_id=stopped["id"], status="stopped_budget", error="cap")
    return first


def test_totals_count_runs_and_money_and_plan_included_and_unpriced():
    _book()
    totals = costs.summary(days=0)["totals"]
    assert (totals["runs"], totals["done"], totals["failed"], totals["stopped"]) == (6, 3, 1, 2)
    assert totals["metered_usd"] == pytest.approx(0.85)
    assert totals["avg_cost_per_run_usd"] == pytest.approx(0.85 / 6, abs=1e-6)
    assert totals["median_cost_per_run_usd"] == pytest.approx(0.075)
    assert totals["calls"] == 1 + 2 + 3 + 1 + 1
    assert (totals["plan_included_calls"], totals["unpriced_calls"]) == (3, 1)
    assert totals["input_tokens"] == 1500


def test_breakdowns_by_role_model_ticker_and_day():
    _book()
    got = costs.summary(days=0)
    assert got["by_role"]["analyst_market"]["cost_usd"] == pytest.approx(0.15)
    assert got["by_role"]["analyst_market"]["runs"] == 2
    assert got["by_model"]["acme-ai:model-a"]["cost_usd"] == pytest.approx(0.20)
    assert got["by_model"]["acme-cli:model-b"]["transport"] == "claude_cli"
    assert got["by_model"]["acme-cli:model-b"]["plan_included_calls"] == 3
    assert [t["ticker"] for t in got["by_ticker"]] == ["GAMMA", "ACME", "BETA"]
    assert len(got["by_day"]) == 1 and got["by_day"][0]["runs"] == 6


def test_by_ticker_keeps_the_top_ten():
    for i in range(12):
        _run(ticker=f"T{i:02d}", cost_usd=0.01 * (i + 1))
    tickers = [t["ticker"] for t in costs.summary(days=0)["by_ticker"]]
    assert len(tickers) == 10 and tickers[0] == "T11"


def test_recent_runs_show_estimate_against_actual():
    run = apply_run_change(
        "create",
        ticker="ACME",
        analysts=ANALYSTS,
        config_snapshot=budget.config_snapshot(settings.load(), API),
        context_level="ticker",
        cap_usd=2.0,
        estimate_usd=0.8,
    )
    apply_run_change("start", run_id=run["id"])
    apply_run_change("usage", run_id=run["id"], llm_calls=1, cost_usd=0.6)
    apply_run_change("finish", run_id=run["id"], signal="Buy")
    row = costs.summary(days=0)["recent"][0]
    assert (row["ticker"], row["signal"], row["estimate_usd"], row["cost_usd"]) == (
        "ACME",
        "Buy",
        0.8,
        0.6,
    )


def test_month_block_is_spend_against_the_caps():
    _run(cost_usd=5.0)
    month = costs.summary(days=0)["month"]
    assert month == {
        "spend_usd": 5.0,
        "cap_usd": 20.0,
        "pct_used": 0.25,
        "per_run_cap_usd": 2.0,
        "max_llm_calls": 150,
    }


def test_the_window_limits_the_runs_but_not_the_month():
    old = _run(cost_usd=1.0)
    _age(old["id"], 40)
    _run(cost_usd=0.5)
    assert costs.summary(days=30)["totals"]["runs"] == 1
    assert costs.summary(days=0)["totals"]["runs"] == 2
    assert costs.summary(days=30)["month"]["spend_usd"] == 0.5
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert costs.summary(start=today, end=today, tz="UTC")["totals"]["runs"] == 1


def test_an_empty_book_is_all_zero():
    got = costs.summary()
    assert got["totals"]["runs"] == 0 and got["totals"]["avg_cost_per_run_usd"] == 0.0
    assert got["by_ticker"] == [] and got["recent"] == [] and got["by_day"] == []


# ── flag and API ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cost_router)
    return TestClient(app)


@pytest.mark.flag_both("trading_agents")
def test_check_can_start_and_the_cost_service_refuse_with_the_flag_off(_feature_flags):
    from backend.services import trading_agents

    if not trading_agents.flag_on():
        with pytest.raises(FeatureDisabledError):
            budget.check_can_start(settings.load(), API, ANALYSTS, _rate_for)
        with pytest.raises(FeatureDisabledError):
            costs.summary()
    else:
        assert budget.check_can_start(settings.load(), API, ANALYSTS, _rate_for).calls == 26
        assert costs.summary()["totals"]["runs"] == 0


@pytest.mark.flag_both("trading_agents")
def test_the_cost_route_answers_only_with_the_flag_on(client):
    from backend.services import trading_agents

    if not trading_agents.flag_on():
        with pytest.raises(FeatureDisabledError):
            client.get("/api/cost/trading-agents")
        return
    _run(cost_usd=0.25)
    got = client.get("/api/cost/trading-agents", params={"days": 0, "tz": "UTC"})
    assert got.status_code == 200
    assert got.json()["totals"]["metered_usd"] == pytest.approx(0.25)
    today = client.get("/api/cost/trading-agents", params={"range": "today", "tz": "UTC"})
    assert today.json()["totals"]["runs"] == 1
