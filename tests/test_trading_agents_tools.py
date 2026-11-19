"""The four TradingAgents tools of Lucius — registration and seeding, the flag, the start tool's
precheck and approval card, and the bounded reads. The runner is stubbed (it has its own tests);
synthetic data only (ticker ACME)."""

from __future__ import annotations

import json

import pytest

from backend.ai import _confirm_gate_v2 as policy
from backend.ai.tools import _confirm_gate, registry
from backend.ai.tools import trading_agents as tools
from backend.ai.tools._confirm_gate_v2 import is_gated_tool
from backend.ai.tools._connector_gate import hidden_connector_tools
from backend.ai.trading_agents import runner
from backend.core.errors import (
    BudgetExceededError,
    FeatureDisabledError,
    NotInstalledError,
    TooManyRunsError,
    ValidationError,
)
from backend.core.tool_use_context import set_tool_use_id
from backend.db.model_config_store import ModelConfigStore
from backend.db.trading_agents_config_store import TradingAgentsConfigStore
from backend.services import thesis_judgment
from backend.services import trading_agents as ta
from backend.services.trading_agents import budget, context, runs, settings
from tests.flags import flags

NAMES = (
    "start_trading_agents_analysis",
    "get_trading_agents_run",
    "list_trading_agents_runs",
    "cancel_trading_agents_run",
)


@pytest.fixture(autouse=True)
def isolated_db(db_isolated):
    return db_isolated


@pytest.fixture
def ready():
    """The connector on, installed and accepted, with a model that can serve (flag on)."""
    ModelConfigStore().save(
        {"default_model": "anthropic:model-a", "providers": {"anthropic": {"api_key": "test-key"}}}
    )
    store = TradingAgentsConfigStore()
    store.write(enabled=True, config={"disclaimer_accepted_at": "2026-11-19T00:00:00+00:00"})
    store.merge_state({"install_status": "ready"})
    with flags(trading_agents=True):
        yield


@pytest.fixture
def submitted(monkeypatch):
    """Stands in for ``runner.submit``: records what it was asked and queues nothing."""
    calls: list[dict] = []

    async def submit(symbol, **kw):
        calls.append({"symbol": symbol, **kw})
        return "tar_000000000001"

    monkeypatch.setattr(runner, "submit", submit)
    return calls


def _parsed(result) -> dict:
    text, proposed = result
    assert proposed is None
    return json.loads(text)


def _run(*, ticker="ACME", reports=None, decision=None, finish=True) -> str:
    row = runs.apply_run_change(
        "create",
        ticker=ticker,
        requested_by="lucius",
        analysts=["market"],
        config_snapshot={"deep": "anthropic:model-a"},
        context_level="investments",
        portfolio={"cash": 1000.0, "currency": "USD", "positions": []},
        disclosure={"level": "investments"},
        estimate_usd=0.4,
        cap_usd=2.0,
    )
    if finish:
        runs.apply_run_change("start", run_id=row["id"])
        runs.apply_run_change("usage", run_id=row["id"], llm_calls=2, cost_usd=0.25)
        runs.apply_run_change(
            "finish",
            run_id=row["id"],
            signal="Hold",
            decision_text=decision,
            reports=reports,
        )
    return row["id"]


# ── registration ─────────────────────────────────────────────────────────────────────────


def test_the_four_tools_are_registered_under_the_connector():
    for name in NAMES:
        entry = registry._REGISTRY[name]
        assert entry.connector == "trading_agents"
        assert entry.unavailable is not None
        assert entry.input_schema["type"] == "object"
        assert not {"oneOf", "allOf", "anyOf"} & set(entry.input_schema)


def test_only_a_start_and_a_cancel_write_and_a_start_is_seeded_ask():
    assert registry._REGISTRY[NAMES[0]].external_effect
    assert registry._REGISTRY[NAMES[0]].mutates == ("trading_agents",)
    assert registry._REGISTRY[NAMES[3]].mutates == ("trading_agents",)
    assert is_gated_tool(NAMES[0])
    assert not is_gated_tool(NAMES[1])
    assert not is_gated_tool(NAMES[2])
    assert not {"trading_agents"} & policy.WORKSPACE_DOMAINS


@pytest.mark.parametrize("name", NAMES)
def test_every_description_says_what_it_is_and_never_an_order(name):
    description = registry._REGISTRY[name].description
    if name == NAMES[0]:
        assert "not financial advice" in description
        assert "never an order" in description or "never places an order" in description
        assert "minutes" in description and "costs money" in description
        assert "never start one on your own initiative" in description


# ── the flag ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.flag_both("trading_agents")
@pytest.mark.parametrize("name", NAMES)
async def test_flag_off_hides_and_refuses_every_tool(name):
    args = {"ticker": "ACME"} if name == NAMES[0] else {"run_id": "tar_000000000001"}
    result, _ = await registry.dispatch(name, args, tool_use_id="toolu_flag")
    if ta.flag_on():
        # Flag on, connector never set up: a start refuses (nothing to run on), the rest answer.
        assert (name in hidden_connector_tools()) is True
        if name == NAMES[0]:
            assert "connector" in json.loads(result)["error"]
        return
    assert name in hidden_connector_tools()
    assert json.loads(result)["error"] == "TradingAgents is not turned on."


@pytest.mark.usefixtures("ready")
def test_with_the_connector_on_no_tool_is_hidden():
    assert not set(NAMES) & set(hidden_connector_tools())


# ── the start tool ───────────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("ready")
async def test_a_start_queues_and_returns_at_once(submitted):
    out = _parsed(
        await tools.start_trading_agents_analysis(
            {"ticker": "acme", "analysts": ["market", "news"], "context_level": "position"}
        )
    )
    assert out["ok"] and out["run_id"] == "tar_000000000001" and out["status"] == "queued"
    assert out["estimate_usd"] == 0.0 and out["eta_minutes"] >= 1
    assert submitted == [
        {
            "symbol": "ACME",
            "trade_date": submitted[0]["trade_date"],
            "analysts": ["market", "news"],
            "context_level": "position",
            "requested_by": "lucius",
            "session_id": None,
        }
    ]


@pytest.mark.usefixtures("ready")
async def test_an_unattended_start_is_recorded_as_an_automation(submitted):
    from backend.core import unattended

    token = unattended._unattended.set(True)
    try:
        await tools.start_trading_agents_analysis({"ticker": "ACME"})
    finally:
        unattended._unattended.reset(token)
    assert submitted[0]["requested_by"] == "automation"


@pytest.mark.usefixtures("ready")
def test_a_precheck_refuses_before_a_card_is_drawn(monkeypatch):
    tools._precheck({"ticker": "ACME"})

    TradingAgentsConfigStore().write(config={"disclaimer_accepted_at": None})
    with pytest.raises(FeatureDisabledError, match="research-only notice"):
        tools._precheck({"ticker": "ACME"})
    TradingAgentsConfigStore().write(config={"disclaimer_accepted_at": "2026-11-19T00:00:00+00:00"})

    monkeypatch.setattr(runner, "pending_count", lambda: runner.MAX_PENDING)
    with pytest.raises(TooManyRunsError):
        tools._precheck({"ticker": "ACME"})
    monkeypatch.undo()

    with pytest.raises(ValidationError, match="ticker"):
        tools._precheck({"ticker": "not a ticker!"})
    with pytest.raises(ValidationError, match="trade_date"):
        tools._precheck({"ticker": "ACME", "trade_date": "2999-01-01"})


@pytest.mark.usefixtures("ready")
def test_a_precheck_refuses_a_run_the_monthly_cap_cannot_afford(monkeypatch):
    monkeypatch.setattr(tools, "list_price_rates", lambda model: (3.0, 15.0))
    settings.update({"monthly_cap_usd": 0.0})
    with pytest.raises(BudgetExceededError, match="monthly"):
        tools._precheck({"ticker": "ACME"})


def test_a_precheck_refuses_when_nothing_is_installed():
    with flags(trading_agents=True):
        TradingAgentsConfigStore().write(enabled=True)
        with pytest.raises(NotInstalledError):
            tools._precheck({"ticker": "ACME"})


@pytest.mark.usefixtures("ready")
async def test_a_refused_start_at_approval_time_is_an_error_not_a_raise(monkeypatch):
    monkeypatch.setattr(runner, "pending_count", lambda: runner.MAX_PENDING)
    out = _parsed(await tools.start_trading_agents_analysis({"ticker": "ACME"}))
    assert out["ok"] is False and "already waiting" in out["error"]


@pytest.mark.usefixtures("ready")
def test_the_card_shows_models_cost_caps_and_the_exact_context(monkeypatch):
    monkeypatch.setattr(tools, "list_price_rates", lambda model: (3.0, 15.0))
    card = tools._StartPreview().build({"ticker": "acme", "context_level": "position"})
    data = card["data"]
    assert card["kind"] == "trading_agents_run" and card["raw_arguments"]["ticker"] == "acme"
    assert data["ticker"] == "ACME"
    assert data["deep_model"].startswith("anthropic:model-a via ")
    assert data["quick_model"].startswith("anthropic:model-a via ")
    assert data["estimated_cost"].startswith("about $")
    assert "$0.00 spent of a $" in data["this_month"]
    assert "model calls" in data["per_run_cap"]
    portfolio, disclosure = context.build("ACME", "position")
    assert data["shared_with_the_ai_provider"] == portfolio
    assert data["disclosure"] == disclosure


@pytest.mark.usefixtures("ready")
def test_a_ticker_level_card_says_nothing_is_shared():
    data = tools._StartPreview().build({"ticker": "ACME", "context_level": "ticker"})["data"]
    assert data["shared_with_the_ai_provider"] == "nothing: ticker and date only"


def test_a_cli_subscription_estimate_reads_plan_included():
    plan_included = budget.Estimate(0.0, 0.0, 0.0, 30, budget.BASIS_STATIC, 0, True, True)
    assert "plan-included" in tools._cost_line(plan_included)
    unpriced = budget.Estimate(0.0, 0.0, 0.0, 30, budget.BASIS_STATIC, 0, False, False)
    assert "understates" in tools._cost_line(unpriced)


@pytest.mark.usefixtures("ready")
def test_the_gate_stages_the_card_with_the_preview_and_runs_nothing(submitted):
    set_tool_use_id("toolu_ta_start")
    try:
        text, change = _confirm_gate.stage_generic(
            "start_trading_agents_analysis", {"ticker": "acme", "context_level": "ticker"}
        )
    finally:
        set_tool_use_id(None)
    assert text is None and change["grant_tool"] == "start_trading_agents_analysis"
    preview = change["display"]["preview"]
    assert preview["kind"] == "trading_agents_run" and preview["data"]["ticker"] == "ACME"
    assert submitted == []


@pytest.mark.usefixtures("ready")
def test_the_gate_draws_no_card_for_a_start_that_would_be_refused(monkeypatch, submitted):
    monkeypatch.setattr(runner, "pending_count", lambda: runner.MAX_PENDING)
    set_tool_use_id("toolu_ta_full")
    try:
        text, change = _confirm_gate.stage_generic(
            "start_trading_agents_analysis", {"ticker": "ACME"}
        )
    finally:
        set_tool_use_id(None)
    assert change is None and "already waiting" in text
    assert submitted == []


# ── reading ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("ready")
async def test_a_run_reads_back_with_every_report_capped_and_honest_about_it():
    long = "x" * 9_000
    run_id = _run(
        reports={"market_report": long, "news_report": "short"},
        decision="Hold ACME.",
    )
    out = _parsed(await tools.get_trading_agents_run({"run_id": run_id}))
    assert out["status"] == "done" and out["signal"] == "Hold"
    market = out["reports"]["market_report"]
    assert len(market["text"]) == 6_000 and market["truncated"] is True
    assert market["full_chars"] == 9_000
    assert out["reports"]["news_report"] == {"text": "short", "truncated": False, "full_chars": 5}
    assert out["decision"]["text"] == "Hold ACME."
    assert out["cost"]["cost_usd"] == 0.25 and out["cost"]["llm_calls"] == 2
    assert out["context_sent"]["portfolio"]["cash"] == 1000.0


@pytest.mark.usefixtures("ready")
async def test_a_report_is_sanitized_as_untrusted_text():
    run_id = _run(reports={"market_report": "ok\x00\x07done"}, decision="Hold")
    out = _parsed(await tools.get_trading_agents_run({"run_id": run_id}))
    assert out["reports"]["market_report"]["text"] == "okdone"


@pytest.mark.usefixtures("ready")
async def test_the_thesis_judgment_is_present_only_with_a_thesis(monkeypatch):
    run_id = _run(reports={"market_report": "m"}, decision="Hold")
    none = _parsed(await tools.get_trading_agents_run({"run_id": run_id}))
    assert "thesis_judgment" not in none

    thesis = {
        "role": "speculative",
        "reason": "Holding 3+ years on ACME hardware",
        "breaks_if": "The ACME roadmap is abandoned",
        "stop_below": 50.0,
        "fair_value": 140.0,
        "trim_above": 180.0,
    }
    judged = thesis_judgment.judge(thesis, 85.0, held=True, currency="USD", ticker="ACME")
    seen = []
    monkeypatch.setattr(
        thesis_judgment,
        "for_ticker",
        lambda symbol, price, **kw: seen.append(symbol) or judged,
    )
    out = _parsed(await tools.get_trading_agents_run({"run_id": run_id}))
    assert seen == ["ACME"]
    assert out["thesis_judgment"]["stance"] == "intact"
    assert out["thesis_judgment"]["cite"]


@pytest.mark.usefixtures("ready")
async def test_an_unknown_run_is_an_error():
    out = _parsed(await tools.get_trading_agents_run({"run_id": "tar_nope"}))
    assert "No TradingAgents run" in out["error"]


@pytest.mark.usefixtures("ready")
async def test_a_list_is_bounded_and_reports_the_true_total():
    for _ in range(23):
        _run(finish=False)
    other = _run(ticker="BOLT", finish=False)

    out = _parsed(await tools.list_trading_agents_runs({}))
    assert out["total"] == 24 and len(out["runs"]) == tools._LIST_DEFAULT
    assert "24" in out["note"]
    assert out["runs"][0]["run_id"] == other

    capped = _parsed(await tools.list_trading_agents_runs({"limit": 500}))
    assert len(capped["runs"]) == tools._LIST_MAX and capped["total"] == 24

    only = _parsed(await tools.list_trading_agents_runs({"ticker": "bolt", "status": "queued"}))
    assert only["total"] == 1 and "note" not in only
    assert set(only["runs"][0]) >= {"run_id", "ticker", "status", "signal", "cost_usd"}
    assert "reports" not in only["runs"][0]


@pytest.mark.usefixtures("ready")
async def test_a_list_refuses_a_malformed_ticker():
    out = _parsed(await tools.list_trading_agents_runs({"ticker": "no spaces!"}))
    assert out["ok"] is False


# ── cancelling ───────────────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("ready")
async def test_a_queued_run_cancels_and_a_finished_one_cannot():
    queued = _run(finish=False)
    out = _parsed(await tools.cancel_trading_agents_run({"run_id": queued}))
    assert out == {"ok": True, "run_id": queued, "status": "cancelled"}

    finished = _run(reports=None, decision="Hold")
    refused = _parsed(await tools.cancel_trading_agents_run({"run_id": finished}))
    assert refused["ok"] is False and "cannot be cancelled" in refused["error"]
