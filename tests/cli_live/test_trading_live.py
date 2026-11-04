"""Opt-in provider-backed trading acceptance on synthetic capital, never a real broker."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.ai.models import codex_app_server
from backend.ai.trading_runner_adapter import run_lucius_cycle
from backend.db.model_config_store import ModelConfigStore
from backend.services import tool_permissions, trading

MODELS = os.environ.get(
    "ONW_TRADING_LIVE_MODELS", "claude_cli:claude-sonnet-4-6,codex_cli:gpt-6.1-sol"
).split(",")


@pytest.mark.parametrize("model_ref", MODELS)
@pytest.mark.timeout(210)
async def test_real_lucius_authors_strategy_and_books_synthetic_fill(
    db_isolated, model_ref, project_cli_cache
):
    provider_key, _, _ = model_ref.partition(":")
    selected_home = os.environ.get(
        "CODEX_HOME" if provider_key == "codex_cli" else "CLAUDE_CONFIG_DIR"
    )
    assert selected_home and Path(selected_home).is_dir(), (
        "Select a project-owned login home explicitly"
    )
    config = ModelConfigStore()
    config.update_provider(provider_key, {"enabled": True})
    config.update({"default_model": model_ref})
    workspace = trading.create_workspace(
        {
            "name": "Provider acceptance with synthetic capital",
            "agent_kind": "lucius",
            "instructions": (
                "This is a synthetic acceptance exercise. Use get_trading_environment, get_trading_account "
                "and get_trading_market_data to inspect the run. You MUST author and call run_trading_strategy "
                "with Python source that reads JSON from stdin and emits exactly one BUY order for one unit "
                "of the supplied ACME instrument, using the first account ID, market order, GTC and "
                "request_id='provider-strategy-1'. The input contains accounts.items and observations.items. "
                "Emit JSON {'orders':[the_order], 'summary':'Synthetic acceptance purchase'}. "
                "Then use list_trading_orders and record_trading_decision to report what actually happened. "
                "Do not place extra orders, do not merely describe code, and do not use another tool. "
                "The environment will advance later to produce the simulated fill. Finish this single cycle."
            ),
            "mandate": {"max_model_cost_usd": "2", "max_order_notional": "200"},
        }
    )
    instrument = {
        "symbol": "ACME",
        "venue": "simulation",
        "asset_class": "stock",
        "currency": "USD",
    }
    run = trading.create_run(
        workspace["id"],
        {
            "mode": "backtest",
            "start_at": "2026-01-05T14:30:00+00:00",
            "end_at": "2026-01-05T15:00:00+00:00",
            "accounts": [{"name": "Synthetic provider account", "capital": "1000"}],
            "observations": [
                {
                    "instrument": instrument,
                    "timestamp": "2026-01-05T14:30:00+00:00",
                    "price": "100",
                    "volume": "10",
                },
                {
                    "instrument": instrument,
                    "timestamp": "2026-01-05T14:31:00+00:00",
                    "price": "101",
                    "volume": "10",
                },
            ],
            "data_version": "synthetic-provider-acceptance-v1",
            "agent_version": model_ref,
        },
    )
    for name in ("run_trading_strategy", "submit_trading_order", "record_trading_decision"):
        tool_permissions.set_tool_permission("core", name, "always_allow")
    await trading.apply_run_action(run["id"], {"action": "start"})
    result = None
    error = None
    try:
        result = await run_lucius_cycle(run["id"], model=model_ref, effort="low")
        assert result["status"] == "completed", result
        orders = trading.list_orders(run["id"])["items"]
        assert len(orders) == 1 and orders[0]["request_id"] == "provider-strategy-1", orders
        assert orders[0]["status"] == "open"
        await trading.apply_run_action(run["id"], {"action": "advance"})
        assert trading.list_fills(run["id"])["total"] == 1
        assert trading.get_account(run["id"])["items"][0]["cash"] == "899"
        final = trading.get_run(run["id"])
        assert final["usage_state"] == "settled", final.get("usage_state")
        events = trading.list_events(run["id"], limit=500)["items"]
        assert any(row["kind"] == "script_finished" for row in events)
        assert any(row["kind"] == "agent_usage" for row in events)
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        _write_receipt(run["id"], model_ref, result, error)
        for client in list(codex_app_server._clients.values()):
            await client.aclose()
        codex_app_server._clients.clear()


def _write_receipt(run_id: str, model_ref: str, result, error) -> None:
    output = os.environ.get("ONW_TRADING_LIVE_RECEIPT_DIR")
    if not output:
        return
    run = trading.get_run(run_id)
    events = trading.list_events(run_id, limit=500)["items"]
    receipt = {
        "model": model_ref,
        "run_id": run_id,
        "mode": run["mode"],
        "result": result,
        "error_type": error,
        "usage_state": run.get("usage_state"),
        "model_cost_usd": run["model_cost_usd"],
        "orders": trading.list_orders(run_id)["items"],
        "fills": trading.list_fills(run_id)["items"],
        "performance": trading.get_performance(run_id),
        "activity_kinds": [row["kind"] for row in events],
        "tool_activity": [row["payload"] for row in events if row["kind"] == "agent_activity"],
        "script_artifacts": [
            {"sha256": row["payload"]["artifact"]["sha256"]}
            for row in events
            if row["kind"] == "script_started"
        ],
        "usage_receipts": [row["payload"] for row in events if row["kind"] == "agent_usage"],
    }
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (model_ref.replace(":", "-") + "-" + run_id + ".json")
    destination.write_text(json.dumps(receipt, indent=2, default=str) + "\n")
