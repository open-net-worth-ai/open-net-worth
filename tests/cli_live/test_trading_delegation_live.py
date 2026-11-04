"""Real provider proves persistent work across automatic scheduler decision cycles."""

import json
import os
from pathlib import Path

import pytest

from backend.ai.models import codex_app_server
from backend.ai.trading_runner_adapter import run_due_trading_agents
from backend.core.trading_runtime import TRADING_TOOLS
from backend.db.model_config_store import ModelConfigStore
from backend.services import tool_permissions, trading
from backend.services.trading.delegation import create_delegation, workspace_overview
from backend.services.trading.work_records import workspace_files


@pytest.mark.timeout(150)
async def test_real_lucius_conversation_previews_unsaved_workspace_for_shared_review(
    db_isolated, project_cli_cache
):
    from backend.ai import assistant, turns
    from backend.ai.tools import _skill_scope

    model = os.environ.get("ONW_DELEGATION_MODEL", "codex_cli:gpt-6.1-sol")
    assert os.environ.get("CODEX_HOME"), "Select the project's own CLI login"
    config = ModelConfigStore()
    config.update_provider(model.partition(":")[0], {"enabled": True})
    config.update({"default_model": model})
    session = "synthetic-delegation-conversation"
    manifest = [
        {
            "name": "trading.review_draft",
            "route": "trading",
            "live": True,
            "params": [{"name": "delegation", "type": "object", "required": True}],
        }
    ]
    assistant.upsert_session_meta(session, screen_commands=manifest)
    names = {"preview_trading_workspace", "list_trading_workspaces", "run_screen_command"}
    for name in names:
        tool_permissions.set_tool_permission("core", name, "always_allow")
    mandate = "Research opportunities, write and test strategies, trade or wait within this synthetic allocation."
    message = (
        "Help me set up an UNSAVED trading workspace named Synthetic conversational allocation. "
        f"Preserve my exact mandate: {mandate} "
        "Use managed Lucius; Paper with 1000 USD in one simulation account, "
        "start 2026-11-05T00:00:00Z and end 2026-12-05T00:00:00Z, USD reporting with USD FX1. "
        "Allow stock and crypto, no fixed instrument list, Yahoo market data. "
        "Maximum order 200 USD and agent usage budget 1 USD. No live authorization. "
        "Use preview_trading_workspace, then run_screen_command name trading.review_draft "
        "with args.delegation equal to the returned delegation. Do not create or start anything."
    )
    events = []

    async def drive(turn_id):
        async for event in assistant.run(
            session,
            [{"role": "user", "content": message}],
            "You are Lucius. Translate the user's stated delegation into the shared unsaved review contract. Use the supplied tools and preserve the exact original mandate.",
            model=model,
            effort="low",
            turn_id=turn_id,
        ):
            events.append(event)
            yield event

    snapshot = _skill_scope.snapshot_scope()
    try:
        _skill_scope.set_offer_scope(names)
        status = await turns.run_inline(
            session,
            "chat",
            drive,
            error_event=lambda exc: {"type": "error", "message": str(exc)},
            cost_surface="web",
        )
        commands = [event for event in events if event.get("type") == "screen_command"]
        assert status == "ok", [event for event in events if event.get("type") == "error"]
        assert commands and commands[-1]["status"] == "dispatched", events
        draft = commands[-1]["command"]["args"]["delegation"]
        assert draft["workspace"]["instructions"] == mandate
        assert draft["environment"]["live_authorized"] is False
        assert trading.list_workspaces()["items"] == []
        output = os.environ.get("ONW_TRADING_LIVE_RECEIPT_DIR")
        if output:
            Path(output, "conversation-review.json").write_text(
                json.dumps(
                    {
                        "model": model,
                        "status": "passed",
                        "command": commands[-1],
                        "saved": False,
                        "tool_names": [
                            e.get("name") for e in events if e.get("type") == "tool_executing"
                        ],
                        "usage": [e.get("usage") for e in events if e.get("type") == "turn_done"],
                    },
                    indent=2,
                    default=str,
                )
                + "\n"
            )
    finally:
        _skill_scope.restore_scope(snapshot)
        for client in list(codex_app_server._clients.values()):
            await client.aclose()
        codex_app_server._clients.clear()


@pytest.mark.timeout(330)
async def test_real_provider_resumes_workspace_files_and_fills_without_manual_cycles(
    db_isolated, project_cli_cache
):
    model = os.environ.get("ONW_DELEGATION_MODEL", "codex_cli:gpt-6.1-sol")
    provider = model.partition(":")[0]
    selected = os.environ.get("CODEX_HOME" if provider == "codex_cli" else "CLAUDE_CONFIG_DIR")
    assert selected and Path(selected).is_dir(), "Select the project's own CLI login"
    config = ModelConfigStore()
    config.update_provider(provider, {"enabled": True})
    config.update({"default_model": model})
    instructions = (
        "Manage this synthetic 1000 USD account for an acceptance exercise. You may analyze, "
        "write code, trade or wait. On your first wake: inspect environment, account and "
        "market data. Save a concise research.md via write_trading_file describing your "
        "analysis of the supplied ACME data. Author and execute a short Python strategy "
        "via run_trading_strategy to demonstrate exactly one market GTC BUY of one ACME "
        "unit, request_id='delegated-provider-1', using the first account and supplied "
        "instrument. Source reads JSON stdin (accounts.items, observations.items) and "
        "prints JSON with orders and summary. Record your actual decision using "
        "record_trading_decision with wake_after_seconds=60. Finish, do not poll. "
        "On subsequent wakes: read saved research.md and inspect account and fills. Do "
        "not submit another order or re-run the purchase strategy. Record a hold decision "
        "with wake_after_seconds=60 and finish. No real broker or real funds are involved."
    )
    instrument = {
        "symbol": "ACME",
        "venue": "simulation",
        "asset_class": "stock",
        "currency": "USD",
    }
    for name in TRADING_TOOLS:
        tool_permissions.set_tool_permission("core", name, "always_allow")
    result = await create_delegation(
        {
            "workspace": {
                "name": "Persistent provider workspace acceptance",
                "instructions": instructions,
                "mandate": {"max_order_notional": "200", "max_model_cost_usd": "2"},
            },
            "environment": {
                "mode": "backtest",
                "start_at": "2026-01-05T14:30:00Z",
                "end_at": "2026-01-05T14:35:00Z",
                "accounts": [{"name": "Synthetic account", "capital": "1000"}],
                "agent_version": model,
                "data_version": "persistent-provider-bars-v1",
                "observations": [
                    {
                        "instrument": instrument,
                        "timestamp": f"2026-01-05T14:3{minute}:00Z",
                        "available_at": f"2026-01-05T14:3{minute}:00Z",
                        "price": str(100 + minute),
                        "open": str(99 + minute),
                        "high": str(101 + minute),
                        "low": str(98 + minute),
                        "close": str(100 + minute),
                        "volume": "10",
                        "interval": "1m",
                        "source": "synthetic-fixture",
                    }
                    for minute in range(3)
                ],
            },
            "start": True,
        }
    )
    run_id = result["run"]["id"]
    receipt = {"model": model, "run_id": run_id, "status": "incomplete"}
    try:
        assert result["started"], result
        first_tick = await run_due_trading_agents()
        assert first_tick["items"] and first_tick["items"][0]["status"] == "completed", first_tick
        first = trading.get_run(run_id)
        original = workspace_overview(result["workspace"]["id"])["original_mandate"]
        assert instructions in json.dumps(original)
        research = workspace_files(run_id, name="research.md")
        assert research["content"]
        assert any(f["name"] == "strategy.py" for f in workspace_files(run_id)["items"])
        orders = trading.list_orders(run_id)["items"]
        assert len(orders) == 1 and orders[0]["request_id"] == "delegated-provider-1", orders
        assert orders[0]["decision_id"], "Strategy execution must retain its recorded decision"
        second_tick = await run_due_trading_agents()
        assert second_tick["items"] and second_tick["items"][0]["status"] == "completed", (
            second_tick
        )
        second = trading.get_run(run_id)
        assert first["agent_session_id"] == second["agent_session_id"]
        assert first["current_time"] < second["current_time"]
        assert len(trading.list_orders(run_id)["items"]) == 1
        assert trading.list_fills(run_id)["total"] == 1
        assert trading.get_account(run_id)["items"][0]["cash"] == "899"
        events = trading.list_events(run_id, limit=500)["items"]
        assert any(e["kind"] == "agent_input" for e in events)
        assert any(e["kind"] == "agent_output" for e in events)
        assert any(e["kind"] == "agent_tool_call" for e in events)
        assert any(e["kind"] == "agent_tool_result" for e in events)
        assert second["usage_state"] == "settled"
        receipt.update(
            status="passed",
            session_id=second["agent_session_id"],
            scheduler_ticks=[first_tick, second_tick],
            clock=[first["current_time"], second["current_time"]],
            order_count=1,
            fill_count=1,
            research_version=research["version"],
            research_sha256=research["sha256"],
            model_cost_usd=second["model_cost_usd"],
            activity_kinds=sorted({e["kind"] for e in events}),
        )
    finally:
        await trading.apply_run_action(run_id, {"action": "stop"})
        for client in list(codex_app_server._clients.values()):
            await client.aclose()
        codex_app_server._clients.clear()
        output = os.environ.get("ONW_TRADING_LIVE_RECEIPT_DIR")
        if output:
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"delegation-{run_id}.json").write_text(
                json.dumps(receipt, indent=2, default=str) + "\n"
            )
