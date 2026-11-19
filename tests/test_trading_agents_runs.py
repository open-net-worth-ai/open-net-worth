"""The TradingAgents run choke — synthetic data only (ticker ACME).

``apply_run_change`` is the one writer of ``trading_agents_runs``: one DB transaction per op,
validated status transitions, a data event after every committed change and none after a refusal.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.core import data_events
from backend.core.errors import ConflictError, FeatureDisabledError, NotFoundError, ValidationError
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import trading_agents
from backend.services.trading_agents import runs
from backend.services.trading_agents.runs import apply_run_change

pytestmark = pytest.mark.flags(trading_agents=True)


@pytest.fixture(autouse=True)
def isolated_db(db_isolated):
    return db_isolated


@pytest.fixture
def events(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(data_events, "publish", lambda domains: seen.append(list(domains)))
    return seen


def _create(**over):
    kw = {
        "ticker": "acme",
        "analysts": ["market", "news"],
        "config_snapshot": {"deep": "anthropic:model-a", "rounds": {"debate": 1}},
        "context_level": "investments",
        "portfolio": {"cash": 1000.0, "currency": "USD", "positions": []},
        "disclosure": {"level": "investments"},
        "cap_usd": 2.0,
        **over,
    }
    return apply_run_change("create", **kw)


def _running(**over):
    run = _create(**over)
    return apply_run_change("start", run_id=run["id"])


def _usage(run_id, **over):
    return apply_run_change(
        "usage",
        run_id=run_id,
        **{"llm_calls": 1, "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.01, **over},
    )


# ── create ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.flag_both("trading_agents")
def test_a_run_can_only_be_created_while_the_flag_is_on(events):
    if not trading_agents.flag_on():
        with pytest.raises(FeatureDisabledError):
            _create()
        assert TradingAgentsRunStore().count() == 0
        assert events == []
        return
    run = _create()
    assert TradingAgentsRunStore().count() == 1
    assert events == [["trading_agents"]]
    assert run["status"] == "queued"


def test_create_stores_the_run_with_zeroed_totals():
    run = _create(requested_by="lucius", session_id="sess_1", estimate_usd=0.4)
    assert run["id"].startswith("tar_") and len(run["id"]) == 16
    assert run["ticker"] == "ACME"
    assert date.fromisoformat(run["trade_date"]) <= date.today() + timedelta(days=1)
    assert run["analysts"] == ["market", "news"]
    assert run["context_sent"] == {
        "ticker": "ACME",
        "portfolio": {"cash": 1000.0, "currency": "USD", "positions": []},
        "disclosure": {"level": "investments"},
    }
    assert (run["llm_calls"], run["cost_usd"], run["plan_included_calls"]) == (0, 0.0, 0)
    assert run["progress"] == [] and run["cost_by_role"] == {} and run["cost_by_model"] == {}
    assert (run["requested_by"], run["session_id"], run["estimate_usd"]) == (
        "lucius",
        "sess_1",
        0.4,
    )
    assert run["cap_usd"] == 2.0
    assert run["started_at"] is None and run["finished_at"] is None


def test_a_ticker_level_run_records_that_no_portfolio_was_sent():
    run = _create(context_level="ticker", portfolio=None)
    assert run["context_sent"]["portfolio"] is None


@pytest.mark.parametrize(
    "over",
    [
        {"ticker": "../etc"},
        {"ticker": "."},
        {"ticker": ".."},
        {"ticker": "-.="},
        {"ticker": ""},
        {"ticker": "A" * 16},
        {"trade_date": (date.today() + timedelta(days=3)).isoformat()},
        {"trade_date": "yesterday"},
        {"requested_by": "stranger"},
        {"context_level": "everything"},
        {"cap_usd": 0},
        {"cap_usd": None},
        {"analysts": []},
        {"analysts": ["tarot"]},
        {"config_snapshot": {"api_key": "sentinel"}},
        {"config_snapshot": {"nested": {"bridge_token": "sentinel"}}},
    ],
)
def test_create_refuses_bad_input_and_writes_nothing(events, over):
    with pytest.raises(ValidationError):
        _create(**over)
    assert TradingAgentsRunStore().count() == 0
    assert events == []


# ── events ───────────────────────────────────────────────────────────────────────────────


def test_usage_and_progress_publish_at_most_once_a_second_but_transitions_always(
    events, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr(runs.time, "monotonic", lambda: clock[0])
    run = _running()
    assert len(events) == 2  # create, start
    for index in range(20):
        _usage(run["id"])
        apply_run_change(
            "progress",
            run_id=run["id"],
            stage="Trader",
            status="started" if index % 2 else "finished",
        )
    assert len(events) == 3  # the whole burst is one event
    clock[0] += 1.5
    _usage(run["id"])
    assert len(events) == 4
    apply_run_change("finish", run_id=run["id"], signal="Hold")
    assert len(events) == 5


# ── lifecycle ────────────────────────────────────────────────────────────────────────────


def test_a_run_goes_queued_running_done_and_every_step_publishes(events):
    run = _create()
    started = apply_run_change("start", run_id=run["id"])
    assert started["status"] == "running" and started["started_at"]
    stage = apply_run_change("progress", run_id=run["id"], stage="Market Analyst", status="started")
    assert [p["stage"] for p in stage["progress"]] == ["Market Analyst"]
    done = apply_run_change(
        "finish",
        run_id=run["id"],
        signal="Hold",
        decision_text="Rating: Hold",
        reports={"market_report": "Prices were flat.", "bull_history": "Bull case."},
    )
    assert done["status"] == "done" and done["signal"] == "Hold" and done["finished_at"]
    assert done["decision_text"] == "Rating: Hold"
    assert done["reports"] == {
        "fields": {"market_report": "Prices were flat.", "bull_history": "Bull case."},
        "truncated": [],
    }
    assert events == [["trading_agents"]] * 4


def test_usage_adds_up_by_role_and_by_model():
    run = _running()
    _usage(run["id"], role="analyst_market", model="anthropic:model-a")
    _usage(run["id"], role="analyst_market", model="anthropic:model-a", cost_usd=0.02)
    _usage(
        run["id"],
        role="trader",
        model="claude_cli:model-b",
        cost_usd=0.0,
        plan_included_calls=1,
        cache_read_tokens=50,
    )
    _usage(run["id"], unpriced_calls=1, cost_usd=0.0)
    row = TradingAgentsRunStore().get(run["id"])
    assert (row["llm_calls"], row["input_tokens"], row["output_tokens"]) == (4, 400, 80)
    assert row["cost_usd"] == pytest.approx(0.03)
    assert (row["plan_included_calls"], row["unpriced_calls"], row["cache_read_tokens"]) == (
        1,
        1,
        50,
    )
    assert row["cost_by_role"]["analyst_market"] == {
        "calls": 2,
        "input_tokens": 200,
        "output_tokens": 40,
        "cost_usd": pytest.approx(0.03),
        "plan_included_calls": 0,
    }
    assert row["cost_by_role"]["trader"]["plan_included_calls"] == 1
    assert row["cost_by_role"]["other"]["calls"] == 1  # no role given
    assert set(row["cost_by_model"]) == {"anthropic:model-a", "claude_cli:model-b"}


def test_a_call_in_flight_when_the_run_stopped_is_still_recorded():
    run = _running()
    apply_run_change("cancel", run_id=run["id"])
    after = _usage(run["id"], cost_usd=0.05)
    assert after["status"] == "cancelled"
    assert after["cost_usd"] == pytest.approx(0.05)


def test_a_queued_run_cannot_be_charged():
    run = _create()
    with pytest.raises(ConflictError):
        _usage(run["id"])


def test_negative_usage_is_refused_and_leaves_the_run_unchanged():
    run = _running()
    with pytest.raises(ValidationError):
        _usage(run["id"], input_tokens=-5)
    assert TradingAgentsRunStore().get(run["id"])["llm_calls"] == 0


def test_a_long_report_is_capped_and_flagged():
    run = _running()
    done = apply_run_change(
        "finish",
        run_id=run["id"],
        signal="Buy",
        reports={"news_report": "x" * (runs.MAX_REPORT_CHARS + 10), "market_report": "ok"},
    )
    assert len(done["reports"]["fields"]["news_report"]) == runs.MAX_REPORT_CHARS
    assert done["reports"]["truncated"] == ["news_report"]


def test_only_the_report_fields_a_run_keeps_are_stored():
    run = _running()
    with pytest.raises(ValidationError):
        apply_run_change(
            "finish", run_id=run["id"], signal="Hold", reports={"portfolio_context": "cash 1000"}
        )
    assert TradingAgentsRunStore().get(run["id"])["status"] == "running"


def test_finish_needs_a_known_signal():
    run = _running()
    with pytest.raises(ValidationError):
        apply_run_change("finish", run_id=run["id"], signal="Strong Buy")
    unreadable = apply_run_change("finish", run_id=run["id"], signal="REVIEW")
    assert unreadable["signal"] == "REVIEW"


def test_progress_needs_a_stage_and_keeps_the_newest_entries():
    run = _running()
    with pytest.raises(ValidationError):
        apply_run_change("progress", run_id=run["id"], stage="", status="started")
    with pytest.raises(ValidationError):
        apply_run_change("progress", run_id=run["id"], stage="Trader", status="halfway")
    for n in range(runs.MAX_PROGRESS_ENTRIES + 5):
        last = apply_run_change("progress", run_id=run["id"], stage=f"s{n}", status="started")
    assert len(last["progress"]) == runs.MAX_PROGRESS_ENTRIES
    assert last["progress"][-1]["stage"] == f"s{runs.MAX_PROGRESS_ENTRIES + 4}"


# ── transitions ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "op, kw",
    [
        ("finish", {"signal": "Hold"}),
        ("progress", {"stage": "Trader", "status": "started"}),
    ],
)
def test_a_queued_run_cannot_skip_to_running_only_ops(op, kw):
    run = _create()
    with pytest.raises(ConflictError):
        apply_run_change(op, run_id=run["id"], **kw)


def test_a_run_starts_once():
    run = _running()
    with pytest.raises(ConflictError):
        apply_run_change("start", run_id=run["id"])


@pytest.mark.parametrize("ending", ["finish", "fail", "cancel"])
@pytest.mark.parametrize("then", ["start", "finish", "fail", "cancel", "progress", "delete_ok"])
def test_a_terminal_status_is_final(ending, then):
    run = _running()
    kw = {"finish": {"signal": "Hold"}, "fail": {"error": "boom"}, "cancel": {}}[ending]
    apply_run_change(ending, run_id=run["id"], **kw)
    if then == "delete_ok":
        apply_run_change("delete", run_id=run["id"])
        return
    extra = {
        "finish": {"signal": "Hold"},
        "fail": {"error": "x"},
        "progress": {"stage": "Trader", "status": "started"},
    }.get(then, {})
    with pytest.raises(ConflictError):
        apply_run_change(then, run_id=run["id"], **extra)


def test_fail_records_a_sanitised_bounded_error_and_may_name_the_stop_reason():
    run = _running()
    stopped = apply_run_change(
        "fail",
        run_id=run["id"],
        status="stopped_budget",
        error="Stopped at the cap. " + "y" * (runs.MAX_ERROR_CHARS + 50),
    )
    assert stopped["status"] == "stopped_budget"
    assert len(stopped["error"]) <= runs.MAX_ERROR_CHARS
    assert stopped["finished_at"]


def test_a_run_that_never_started_can_only_fail_or_be_interrupted():
    queued = _create()
    with pytest.raises(ConflictError):
        apply_run_change("fail", run_id=queued["id"], status="stopped_calls")
    assert apply_run_change("fail", run_id=queued["id"], status="interrupted")["status"] == (
        "interrupted"
    )
    assert apply_run_change("fail", run_id=_create()["id"], error="no model")["status"] == "failed"


@pytest.mark.parametrize("status", ["stopped_budget", "stopped_calls", "timed_out", "interrupted"])
def test_a_running_run_can_end_in_every_stop_status(status):
    run = _running()
    assert apply_run_change("fail", run_id=run["id"], status=status)["status"] == status


def test_a_queued_run_can_be_cancelled():
    run = _create()
    assert apply_run_change("cancel", run_id=run["id"])["status"] == "cancelled"


def test_an_unknown_status_for_fail_is_refused():
    run = _running()
    with pytest.raises(ConflictError):
        apply_run_change("fail", run_id=run["id"], status="done")


# ── delete / lookups ─────────────────────────────────────────────────────────────────────


def test_only_a_finished_run_can_be_deleted(events):
    run = _running()
    with pytest.raises(ConflictError):
        apply_run_change("delete", run_id=run["id"])
    assert TradingAgentsRunStore().get(run["id"]) is not None
    apply_run_change("cancel", run_id=run["id"])
    events.clear()
    deleted = apply_run_change("delete", run_id=run["id"])
    assert deleted["id"] == run["id"]
    assert TradingAgentsRunStore().get(run["id"]) is None
    assert events == [["trading_agents"]]


def test_an_unknown_run_and_an_unknown_op_are_refused(events):
    with pytest.raises(NotFoundError):
        apply_run_change("start", run_id="tar_000000000000")
    with pytest.raises(NotFoundError):
        apply_run_change("cancel", run_id=None)
    with pytest.raises(ValidationError):
        apply_run_change("reopen", run_id="tar_000000000000")
    assert events == []


def test_a_refused_change_publishes_nothing(events):
    run = _create()
    events.clear()
    with pytest.raises(ConflictError):
        apply_run_change("finish", run_id=run["id"], signal="Hold")
    assert events == []


def test_the_store_lists_newest_first_and_filters():
    first = _create(ticker="ACME")
    second = _create(ticker="ACME")
    other = _create(ticker="ZZZ")
    apply_run_change("cancel", run_id=first["id"])
    store = TradingAgentsRunStore()
    assert {r["id"] for r in store.list(ticker="ACME")} == {first["id"], second["id"]}
    assert [r["id"] for r in store.list(statuses=["cancelled"])] == [first["id"]]
    assert store.count(ticker="ZZZ") == 1 and store.count() == 3
    assert store.list(limit=1, offset=0)[0]["id"] in {first["id"], second["id"], other["id"]}
    assert len(store.list(limit=2, offset=2)) == 1
