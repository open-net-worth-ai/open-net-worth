"""The TradingAgents runner — a real subprocess (tests/fixtures/trading_agents/fake_worker.py) talking
to the real per-run bridge over loopback, in front of a fake ONW provider. No network, no venv, no
TradingAgents. Synthetic data only (ticker ACME)."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.ai.trading_agents import bridge_models, llm_bridge, runner, worker_env, worker_session
from backend.ai.trading_agents.llm_bridge import ALIAS_DEEP, ALIAS_QUICK, BridgeModel
from backend.core import paths, process_tree
from backend.core.errors import (
    BudgetExceededError,
    ConflictError,
    FeatureDisabledError,
    NotInstalledError,
    TooManyRunsError,
)
from backend.db.cost_log_store import CostLogStore
from backend.db.model_config_store import ModelConfigStore
from backend.db.trading_agents_config_store import TradingAgentsConfigStore
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import connector_catalog
from backend.services.trading_agents import budget, install, runs, settings


def estimate_of(median: float) -> budget.Estimate:
    return budget.Estimate(
        median_usd=median,
        low_usd=median,
        high_usd=median,
        calls=10,
        basis=budget.BASIS_STATIC,
        sample_size=0,
        plan_included=False,
        priced=True,
    )


FAKE_WORKER = Path(__file__).parent / "fixtures" / "trading_agents" / "fake_worker.py"


class FakeProvider:
    def __init__(self, cost=0.02, basis=None):
        self.cost, self.basis = cost, basis
        self.calls = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.calls += 1
        usage = {"type": "usage", "input_tokens": 100, "output_tokens": 25, "cost_usd": self.cost}
        if self.basis:
            usage["cost_basis"] = self.basis
        yield {"type": "text_delta", "text": "Hold ACME."}
        yield usage
        yield {"type": "done"}


@pytest.fixture(autouse=True)
def runner_env(db_isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(runner, "get_settings", lambda: SimpleNamespace(onw_qa_mode=False))
    ModelConfigStore().save(
        {
            "default_model": "anthropic:claude-sonnet-4-5",
            "providers": {"anthropic": {"api_key": "test-key"}},
        }
    )
    store = TradingAgentsConfigStore()
    store.write(enabled=True, config={"disclaimer_accepted_at": "2026-11-19T00:00:00+00:00"})
    store.merge_state({"install_status": "ready", "installed_version": install.TA_COMMIT})
    provider = FakeProvider()
    use_provider(monkeypatch, provider)
    runner.set_budget_check_for_tests(lambda **_: estimate_of(0.4))
    worker("normal", tmp_path)
    yield SimpleNamespace(provider=provider, tmp=tmp_path, monkeypatch=monkeypatch)
    runner.set_worker_cmd_for_tests(None)
    runner.set_budget_check_for_tests(None)


def use_provider(monkeypatch, provider):
    def resolve(models):
        def model(alias, ref):
            return BridgeModel(
                alias, ref, ref.partition(":")[2], "Provider A", "anthropic", provider
            )

        return {
            ALIAS_DEEP: model(ALIAS_DEEP, models.deep),
            ALIAS_QUICK: model(ALIAS_QUICK, models.quick),
        }

    monkeypatch.setattr(llm_bridge, "resolve_bridge_models", resolve)


def worker(mode: str, tmp_path: Path, pid_file: bool = False):
    command = [sys.executable, "-I", str(FAKE_WORKER), mode]
    if pid_file:
        command.append(str(tmp_path / "worker.pid"))
    runner.set_worker_cmd_for_tests(command)


async def settle():
    """Wait for the queue to drain, then stop the runner."""
    while runner._pump is not None and not runner._pump.done():
        await asyncio.wait({runner._pump}, timeout=30)
    assert not runner._queue and runner._session is None


async def submit(**kw) -> str:
    kw.setdefault("context_level", "ticker")
    return await runner.submit("acme", **kw)


def run_of(run_id: str) -> dict:
    row = TradingAgentsRunStore().get(run_id)
    assert row is not None
    return row


async def until(condition, timeout=20.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out waiting"
        await asyncio.sleep(0.05)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture(autouse=True)
async def stop_runner():
    yield
    await runner.shutdown()


real_resolve = settings.resolve_models

pytestmark = pytest.mark.flags(trading_agents=True)


# ── a normal run ─────────────────────────────────────────────────────────────────────────


async def test_a_run_ends_done_with_its_result_progress_and_attributed_cost():
    run_id = await submit()
    assert run_of(run_id)["status"] in ("queued", "running")
    await settle()
    row = run_of(run_id)
    assert row["status"] == "done" and row["signal"] == "Hold"
    assert row["decision_text"].startswith("Hold ACME")
    assert row["reports"]["fields"] == {
        "market_report": "ACME market report",
        "investment_plan": "ACME plan",
        "bull_history": "bulls",
        "bear_history": "bears",
        "neutral_history": "neutral",
    }
    assert [(p["stage"], p["status"]) for p in row["progress"]] == [
        ("Market Analyst", "started"),
        ("Market Analyst", "finished"),
        ("Research Manager", "started"),
        ("Research Manager", "finished"),
    ]
    assert row["llm_calls"] == 4 and row["input_tokens"] == 400 and row["output_tokens"] == 100
    assert row["cost_usd"] == pytest.approx(0.08)
    roles = {r: v["calls"] for r, v in row["cost_by_role"].items()}
    assert roles == {"analyst_market": 1, "research_manager": 1, "other": 2}
    assert set(row["cost_by_model"]) == {"anthropic:claude-sonnet-4-5"}
    assert row["estimate_usd"] == 0.4 and row["cap_usd"] == 2.0
    assert row["config_snapshot"]["deep"] == "anthropic:claude-sonnet-4-5"
    assert row["context_sent"]["portfolio"] is None
    assert row["started_at"] is not None and row["finished_at"] is not None


async def test_each_call_is_one_cost_row_labelled_for_the_run():
    run_id = await submit()
    await settle()
    rows = CostLogStore().all()
    assert len(rows) == 4
    assert {(r["source"], r["session_id"]) for r in rows} == {("trading_agents", run_id)}


async def test_a_finished_run_notifies_in_the_app_only(runner_env):
    sent = []

    async def notify(message, channels=None, title=None):
        sent.append((message, channels, title))

    runner_env.monkeypatch.setattr("backend.services.notify.notify", notify)
    await submit()
    await settle()
    assert sent == [("TradingAgents finished ACME: Hold", ["in_app"], "TradingAgents")]


async def test_a_finished_run_asks_once_for_the_take_on_its_ticker_to_be_refreshed(runner_env):
    from backend.core import take_refresh

    seen = []

    async def refresher(run_id):
        seen.append(run_id)

    take_refresh.register(refresher)
    run_id = await submit()
    await settle()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    take_refresh.register(None)
    assert seen == [run_id]


async def test_a_run_that_does_not_finish_asks_for_no_refresh(runner_env):
    from backend.core import take_refresh

    seen = []

    async def refresher(run_id):
        seen.append(run_id)

    take_refresh.register(refresher)
    worker("crash", runner_env.tmp)
    await submit()
    await settle()
    await asyncio.sleep(0)
    take_refresh.register(None)
    assert seen == []


async def test_the_connector_remembers_when_a_run_last_started():
    await submit()
    await settle()
    assert TradingAgentsConfigStore().read()["state"]["last_run_at"]


async def test_plan_included_calls_are_counted_not_charged(runner_env):
    use_provider(runner_env.monkeypatch, FakeProvider(basis="plan_included"))
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["plan_included_calls"] == 4 and row["cost_usd"] == 0 and row["unpriced_calls"] == 0


async def test_a_metered_call_with_no_price_and_no_reported_cost_is_counted_unpriced(runner_env):
    """A provider emits 0.0 — not nothing — for a model it cannot price."""
    runner_env.monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: None)
    use_provider(runner_env.monkeypatch, FakeProvider(cost=0.0))
    TradingAgentsConfigStore().write(
        config={"per_run_cap_usd": 50.0}
    )  # clears the dearest worst case
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["unpriced_calls"] == 4 and row["cost_usd"] == 0 and row["plan_included_calls"] == 0


async def test_a_priced_call_and_a_call_the_endpoint_priced_are_not_unpriced(runner_env):
    run_id = await submit()
    await settle()
    assert run_of(run_id)["unpriced_calls"] == 0
    runner_env.monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: None)
    provider = FakeProvider(cost=0.01)
    original = provider.stream_chat

    async def reported(*args, **kwargs):
        async for event in original(*args, **kwargs):
            yield {**event, "cost_reported": True} if event["type"] == "usage" else event

    provider.stream_chat = reported
    use_provider(runner_env.monkeypatch, provider)
    second = await submit()
    await settle()
    assert run_of(second)["unpriced_calls"] == 0


async def test_garbage_between_valid_lines_is_ignored():
    worker("badline", Path("."))
    run_id = await submit()
    await settle()
    assert run_of(run_id)["status"] == "done"


# ── the worker's environment ─────────────────────────────────────────────────────────────

_VENDOR_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "AUTH_PASSWORD")


def test_the_worker_env_holds_no_llm_vendor_key(monkeypatch):
    for name in _VENDOR_KEYS:
        monkeypatch.setenv(name, "leak-" + name)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    TradingAgentsConfigStore().write(secrets={"fred_api_key": "fred-123"})
    env = worker_env.worker_env("bridge-token", "ACME Research Bot", "tar_0123456789ab")
    assert not set(_VENDOR_KEYS) & set(env)
    assert not any("leak-" in v for v in env.values())
    secretish = {k for k in env if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASS"))}
    assert secretish == {"OPENAI_COMPATIBLE_API_KEY", "FRED_API_KEY"}
    assert env["OPENAI_COMPATIBLE_API_KEY"] == "bridge-token"
    assert env["FRED_API_KEY"] == "fred-123"
    assert env["TRADINGAGENTS_NO_UPDATE_CHECK"] == "1"
    assert env["SEC_EDGAR_USER_AGENT"] == "ACME Research Bot"
    for name in ("TRADINGAGENTS_RESULTS_DIR", "TRADINGAGENTS_CACHE_DIR"):
        assert str(paths.DATA_DIR) in env[name]
    assert "127.0.0.1" in env["NO_PROXY"].split(",")


def test_the_worker_env_points_every_home_at_the_runs_own_folder(monkeypatch):
    for name in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, "/real/owner/home")
    monkeypatch.setenv("HOMEDRIVE", "C:")
    env = worker_env.worker_env("bridge-token", "ACME Research Bot", "tar_0123456789ab")
    home = str(install.run_dir("tar_0123456789ab") / "home")
    for name in worker_env.HOME_VARIABLES:
        assert env[name] == home
    assert "HOMEDRIVE" not in env and "HOMEPATH" not in env
    assert "/real/owner/home" not in env.values()
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["TRADINGAGENTS_MEMORY_LOG_PATH"].startswith(str(install.run_dir("tar_0123456789ab")))


async def test_the_spawned_process_gets_that_env_and_the_bridges_token(runner_env):
    seen = {}
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen.update(kwargs["env"])
        return await real(*args, **kwargs)

    runner_env.monkeypatch.setenv("ANTHROPIC_API_KEY", "leak-anthropic")
    runner_env.monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    await submit()
    await settle()
    assert "ANTHROPIC_API_KEY" not in seen
    assert len(seen["OPENAI_COMPATIBLE_API_KEY"]) >= 32


# ── the ways a run ends ──────────────────────────────────────────────────────────────────


async def test_a_worker_that_crashes_fails_the_run_and_keeps_what_it_cost(runner_env):
    worker("crash", runner_env.tmp)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "failed" and "exit 3" in row["error"]
    assert row["llm_calls"] == 1 and row["cost_by_role"]["trader"]["calls"] == 1


async def test_a_run_past_the_timeout_is_killed_and_timed_out(runner_env):
    worker("hang", runner_env.tmp, pid_file=True)
    runner_env.monkeypatch.setattr(worker_session, "RUN_TIMEOUT_SECONDS", 1.5)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "timed_out" and "30 minutes" in row["error"]
    assert not alive(int((runner_env.tmp / "worker.pid").read_text()))


async def test_the_spend_cap_stops_the_run_and_kills_the_process(runner_env):
    worker("overcap", runner_env.tmp, pid_file=True)
    use_provider(runner_env.monkeypatch, FakeProvider(cost=0.04))
    runner_env.monkeypatch.setattr(
        bridge_models, "list_price_rates", lambda *a, **k: (0.001, 0.001)
    )
    TradingAgentsConfigStore().write(config={"per_run_cap_usd": 0.10})
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "stopped_budget" and "spend cap" in row["error"]
    assert row["cap_usd"] == 0.10 and 0.10 < row["cost_usd"] < 0.20
    assert not alive(int((runner_env.tmp / "worker.pid").read_text()))


async def test_a_result_that_arrived_before_the_timeout_does_not_make_the_run_done(runner_env):
    worker("resulthang", runner_env.tmp, pid_file=True)
    runner_env.monkeypatch.setattr(worker_session, "RUN_TIMEOUT_SECONDS", 1.5)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "timed_out" and row["signal"] is None


async def test_what_is_left_of_the_month_is_a_live_limit_on_the_run(runner_env):
    worker("overcap", runner_env.tmp)
    use_provider(runner_env.monkeypatch, FakeProvider(cost=0.02))
    runner_env.monkeypatch.setattr(
        bridge_models, "list_price_rates", lambda *a, **k: (0.001, 0.001)
    )
    TradingAgentsConfigStore().write(config={"monthly_cap_usd": 0.05})
    earlier = runs.apply_run_change(
        "create",
        ticker="ACME",
        analysts=["market"],
        config_snapshot={},
        context_level="ticker",
        cap_usd=2.0,
    )["id"]
    runs.apply_run_change("start", run_id=earlier)
    runs.apply_run_change("usage", run_id=earlier, llm_calls=1, cost_usd=0.04)
    runs.apply_run_change("finish", run_id=earlier, signal="Hold")
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "stopped_budget" and "monthly cap" in row["error"]
    assert row["llm_calls"] == 1 and row["cost_usd"] == pytest.approx(0.02)


async def test_an_all_subscription_run_is_not_held_to_the_monthly_cap(runner_env):
    use_provider(runner_env.monkeypatch, FakeProvider(basis="plan_included"))
    TradingAgentsConfigStore().write(config={"monthly_cap_usd": 0.0})
    runner_env.monkeypatch.setattr(
        settings,
        "resolve_models",
        lambda cfg: replace(
            real_resolve(cfg), deep_transport="claude_cli", quick_transport="claude_cli"
        ),
    )
    run_id = await submit()
    await settle()
    assert run_of(run_id)["status"] == "done"


async def test_the_call_cap_stops_the_run(runner_env):
    worker("overcap", runner_env.tmp)
    use_provider(runner_env.monkeypatch, FakeProvider(basis="plan_included"))
    TradingAgentsConfigStore().write(config={"max_llm_calls": 20})
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "stopped_calls" and "20 model calls" in row["error"]
    assert row["llm_calls"] == 20 and row["plan_included_calls"] == 20


async def test_a_line_past_the_size_limit_fails_the_run(runner_env):
    worker("bigline", runner_env.tmp)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "failed" and "too large" in row["error"]


async def test_a_line_between_the_new_and_the_old_limit_fails_the_run(runner_env):
    worker("midline", runner_env.tmp)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "failed" and "too large" in row["error"]


async def test_progress_for_an_unknown_stage_or_a_repeat_is_not_written(runner_env):
    worker("spoof", runner_env.tmp)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert [(p["stage"], p["status"]) for p in row["progress"]] == [("Market Analyst", "started")]


async def test_a_call_id_the_bridge_never_issued_is_not_attributed(runner_env):
    worker("spoof", runner_env.tmp)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["llm_calls"] == 1 and set(row["cost_by_role"]) == {"analyst_market"}


# ── cancelling ───────────────────────────────────────────────────────────────────────────


async def test_cancelling_a_running_run_kills_its_tree(runner_env):
    worker("hang", runner_env.tmp, pid_file=True)
    run_id = await submit()
    await until(lambda: run_of(run_id)["llm_calls"] >= 1)
    row = await runner.cancel(run_id)
    assert row["status"] == "cancelled"
    await settle()
    assert not alive(int((runner_env.tmp / "worker.pid").read_text()))
    assert run_of(run_id)["llm_calls"] == 1


async def test_cancelling_a_queued_run_removes_it_without_running_it(runner_env):
    worker("hang", runner_env.tmp)
    first = await submit()
    second = await submit()
    await until(lambda: run_of(first)["status"] == "running")
    assert (await runner.cancel(second))["status"] == "cancelled"
    assert run_of(second)["started_at"] is None
    await runner.cancel(first)
    await settle()
    assert run_of(second)["status"] == "cancelled"


async def test_cancelling_a_finished_run_is_a_conflict():
    run_id = await submit()
    await settle()
    with pytest.raises(ConflictError):
        await runner.cancel(run_id)


# ── the queue ────────────────────────────────────────────────────────────────────────────


async def test_one_run_goes_at_a_time_and_a_fourth_is_refused(runner_env):
    worker("hang", runner_env.tmp)
    ids = [await submit() for _ in range(3)]
    with pytest.raises(TooManyRunsError):
        await submit()
    assert TradingAgentsRunStore().count() == 3
    await until(lambda: run_of(ids[0])["status"] == "running")
    assert [run_of(i)["status"] for i in ids[1:]] == ["queued", "queued"]
    for run_id in ids:
        await runner.cancel(run_id)
    await settle()


async def test_queued_runs_start_in_order_after_the_first_finishes():
    first, second = await submit(), await submit()
    await settle()
    assert run_of(first)["finished_at"] <= run_of(second)["started_at"]
    assert run_of(second)["status"] == "done"


# ── lifecycle ────────────────────────────────────────────────────────────────────────────


async def test_a_run_whose_start_cannot_be_written_ends_failed_not_stuck(runner_env):
    real = runs.apply_run_change
    calls = []

    def flaky(op, **kw):
        calls.append(op)
        if op == "start":
            raise RuntimeError("database is locked")
        return real(op, **kw)

    runner_env.monkeypatch.setattr(worker_session.runs, "apply_run_change", flaky)
    run_id = await submit()
    await settle()
    assert run_of(run_id)["status"] == "failed" and calls[-2:] == ["start", "fail"]


async def test_a_failed_last_run_note_never_fails_or_orphans_the_run(runner_env):
    def boom(self, changes):
        raise RuntimeError("connector store unavailable")

    runner_env.monkeypatch.setattr(TradingAgentsConfigStore, "merge_state", boom)
    run_id = await submit()
    await settle()
    assert run_of(run_id)["status"] == "done"


async def test_a_job_whose_command_cannot_be_built_fails_and_the_queue_goes_on(runner_env):
    command = runner._worker_cmd
    runner.set_worker_cmd_for_tests(None)
    attempts = []

    def command_for_run():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("no interpreter")
        return command

    runner_env.monkeypatch.setattr(worker_session, "worker_command", command_for_run)
    first, second = await submit(), await submit()
    await settle()
    assert run_of(first)["status"] == "failed" and "could not be started" in run_of(first)["error"]
    assert run_of(second)["status"] == "done"


async def test_a_missing_venv_python_is_a_not_installed_failure(runner_env):
    runner.set_worker_cmd_for_tests(["/nonexistent/venv/bin/python", "worker.py"])
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "failed" and "environment is missing" in row["error"]


async def test_the_worker_tree_is_killed_off_the_event_loop(runner_env):
    worker("hang", runner_env.tmp, pid_file=True)
    threads = []
    real = process_tree.kill_tree

    def spy(pid):
        threads.append(threading.current_thread())
        real(pid)

    runner_env.monkeypatch.setattr(process_tree, "kill_tree", spy)
    run_id = await submit()
    await until(lambda: run_of(run_id)["llm_calls"] >= 1)
    await runner.cancel(run_id)
    await settle()
    assert threads and all(t is not threading.main_thread() for t in threads)


async def test_a_runs_folder_is_gone_when_it_ends_and_its_memory_was_its_own(runner_env):
    seen = {}
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen.update(kwargs["env"])
        return await real(*args, **kwargs)

    runner_env.monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    run_id = await submit()
    await settle()
    assert run_id in seen["TRADINGAGENTS_MEMORY_LOG_PATH"]
    assert run_id in seen["TRADINGAGENTS_RESULTS_DIR"] and run_id in seen["HOME"]
    assert not install.run_dir(run_id).exists()


async def test_a_run_that_fails_or_is_cancelled_also_removes_its_folder(runner_env):
    worker("crash", runner_env.tmp)
    crashed = await submit()
    await settle()
    assert run_of(crashed)["status"] == "failed" and not install.run_dir(crashed).exists()
    worker("hang", runner_env.tmp)
    hung = await submit()
    await until(lambda: run_of(hung)["llm_calls"] >= 1)
    await runner.cancel(hung)
    await settle()
    assert not install.run_dir(hung).exists()


def test_deleting_a_run_removes_its_folder_and_boot_removes_every_one(runner_env):
    def finished():
        row = runs.apply_run_change(
            "create",
            ticker="ACME",
            analysts=["market"],
            config_snapshot={},
            context_level="ticker",
            cap_usd=1.0,
        )
        runs.apply_run_change("start", run_id=row["id"])
        runs.apply_run_change("finish", run_id=row["id"], signal="Hold")
        install.scratch(row["id"]).memory_log.write_text("ACME decision")
        return row["id"]

    from backend.services.trading_agents import queries

    first, second = finished(), finished()
    queries.delete_run(first)
    assert not install.run_dir(first).exists() and install.run_dir(second).exists()
    runner.recover()
    assert not install.run_dir(second).exists()


async def test_submit_does_not_block_the_event_loop_on_the_book(runner_env):
    def slow_build(symbol, level):
        time.sleep(0.4)
        return None, {"level": level}

    runner_env.monkeypatch.setattr(runner.context, "build", slow_build)
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        await submit()
    finally:
        beat.cancel()
    assert ticks >= 10
    await settle()


async def test_concurrent_submits_cannot_overfill_the_queue(runner_env):
    worker("hang", runner_env.tmp)
    results = await asyncio.gather(*[submit() for _ in range(6)], return_exceptions=True)
    assert sum(isinstance(r, str) for r in results) == runner.MAX_PENDING
    assert sum(isinstance(r, TooManyRunsError) for r in results) == 6 - runner.MAX_PENDING
    assert TradingAgentsRunStore().count() == runner.MAX_PENDING
    for run_id in [r for r in results if isinstance(r, str)]:
        await runner.cancel(run_id)
    await settle()


async def test_the_environment_cannot_be_rebuilt_or_removed_under_a_pending_run(runner_env):
    worker("hang", runner_env.tmp)
    run_id = await submit()
    with pytest.raises(ConflictError) as raised:
        install.refuse_if_busy()
    assert raised.value.context["status"] == "runs_pending"
    with pytest.raises(ConflictError):
        install.remove_environment()
    with pytest.raises(ConflictError):
        connector_catalog.kind_of("trading_agents").remove("trading_agents")
    await runner.cancel(run_id)
    await settle()
    install.refuse_if_busy()
    install.remove_environment()


# ── refusals ─────────────────────────────────────────────────────────────────────────────


async def test_a_run_is_refused_until_the_environment_is_installed():
    TradingAgentsConfigStore().merge_state({"install_status": "failed"})
    with pytest.raises(NotInstalledError):
        await submit()
    assert TradingAgentsRunStore().count() == 0


async def test_a_run_is_refused_until_the_disclaimer_is_accepted():
    TradingAgentsConfigStore().write(config={"disclaimer_accepted_at": None})
    with pytest.raises(FeatureDisabledError) as raised:
        await submit()
    assert raised.value.context["status"] == "disclaimer_required"


async def test_the_budget_check_can_refuse_before_any_row_exists():
    def over(**_):
        raise BudgetExceededError("Month spend 19.80 of 20.00; this run is estimated at 0.60.")

    runner.set_budget_check_for_tests(over)
    with pytest.raises(BudgetExceededError):
        await submit()
    assert TradingAgentsRunStore().count() == 0


async def test_the_budget_check_receives_the_resolved_run(runner_env):
    seen = {}
    runner.set_budget_check_for_tests(lambda **kw: seen.update(kw) or estimate_of(1.25))
    run_id = await submit(analysts=["market", "news"])
    await settle()
    assert seen["analysts"] == ["market", "news"]
    assert (
        seen["models"].deep == "anthropic:claude-sonnet-4-5" and seen["cfg"].per_run_cap_usd == 2.0
    )
    assert run_of(run_id)["estimate_usd"] == 1.25


async def test_a_qa_process_runs_nothing_unless_a_test_worker_was_injected(runner_env):
    runner_env.monkeypatch.setattr(
        runner, "get_settings", lambda: SimpleNamespace(onw_qa_mode=True)
    )
    await submit()
    runner.set_worker_cmd_for_tests(None)
    with pytest.raises(FeatureDisabledError) as raised:
        await submit()
    assert raised.value.context["status"] == "qa_mode"
    await settle()


@pytest.mark.flag_both("trading_agents")
async def test_the_flag_gates_submitting():
    if flags_on():
        assert await submit()
        await settle()
        return
    with pytest.raises(FeatureDisabledError):
        await submit()
    assert TradingAgentsRunStore().count() == 0


def flags_on() -> bool:
    from backend.services import trading_agents

    return trading_agents.flag_on()


async def test_the_chosen_analysts_and_level_are_frozen_into_the_row():
    run_id = await submit(analysts=["fundamentals"], context_level="ticker", requested_by="lucius")
    await settle()
    row = run_of(run_id)
    assert row["analysts"] == ["fundamentals"] and row["requested_by"] == "lucius"
    cfg = settings.load()
    assert row["config_snapshot"] == budget.config_snapshot(cfg, settings.resolve_models(cfg))
    assert "api_key" not in str(row["config_snapshot"])
    assert settings.load().context_level == "investments"


# ── boot recovery ────────────────────────────────────────────────────────────────────────


def test_recover_marks_what_a_stopped_app_left_going_as_interrupted():
    def create():
        return runs.apply_run_change(
            "create",
            ticker="ACME",
            analysts=["market"],
            config_snapshot={},
            context_level="ticker",
            cap_usd=1.0,
        )["id"]

    queued, running, done = create(), create(), create()
    runs.apply_run_change("start", run_id=running)
    runs.apply_run_change("start", run_id=done)
    runs.apply_run_change("finish", run_id=done, signal="Hold")
    TradingAgentsConfigStore().merge_state({"install_status": "installing"})
    assert runner.recover() == 2
    assert run_of(queued)["status"] == "interrupted"
    assert run_of(running)["status"] == "interrupted"
    assert run_of(done)["status"] == "done"
    assert TradingAgentsConfigStore().read()["state"]["install_status"] == "failed"
    assert runner.recover() == 0


async def test_shutting_down_ends_the_run_in_flight_as_interrupted(runner_env):
    worker("hang", runner_env.tmp, pid_file=True)
    run_id = await submit()
    await until(lambda: run_of(run_id)["llm_calls"] >= 1)
    await runner.shutdown()
    row = run_of(run_id)
    assert row["status"] == "interrupted"
    assert not alive(int((runner_env.tmp / "worker.pid").read_text()))


async def test_the_default_budget_check_prices_the_run_and_stores_its_median(runner_env):
    runner.set_budget_check_for_tests(None)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    cfg = settings.load()
    expected = budget.estimate(cfg, settings.resolve_models(cfg), cfg.analysts, runner.rate_for_ref)
    assert row["estimate_usd"] == expected.median_usd
    assert row["context_sent"] == {
        "ticker": "ACME",
        "portfolio": None,
        "disclosure": row["context_sent"]["disclosure"],
    }
    assert row["context_sent"]["disclosure"]["level"] == "ticker"


# ── what a worker says never reaches the row unredacted ───────────────────────────────────

_VENDOR_SECRET = "fred-vendor-secret-0451"


@pytest.mark.parametrize("mode", ["leak", "leakstderr"])
async def test_a_workers_error_text_and_stderr_are_redacted_before_they_are_stored(
    runner_env, mode
):
    TradingAgentsConfigStore().write(secrets={"fred_api_key": _VENDOR_SECRET})
    worker(mode, runner_env.tmp)
    seen: list[str] = []
    real_start = llm_bridge.LlmBridge.start

    async def start(self):
        await real_start(self)
        seen.append(self.token)

    runner_env.monkeypatch.setattr(llm_bridge.LlmBridge, "start", start)
    run_id = await submit()
    await settle()
    row = run_of(run_id)
    assert row["status"] == "failed"
    assert _VENDOR_SECRET not in row["error"] and seen[0] not in row["error"]
    assert "apikey=" in row["error"] and "***" in row["error"]


def test_known_secrets_are_masked_by_value_and_by_shape():
    text = "HTTPError for https://x.example/q?apikey=abcdef123456 and abc and plain-secret-9 here"
    out = worker_env.scrub_secrets(text, ["plain-secret-9", "abc", ""])
    assert "abcdef123456" not in out and "plain-secret-9" not in out
    assert " and abc and " in out  # too short to be treated as a secret by value
