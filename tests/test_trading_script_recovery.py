"""Retry correlation preserves the first execution identity and its authority."""

import asyncio

import pytest

from backend.core.errors import ConflictError
from backend.services import tool_permissions, trading, trading_scripts


def test_script_retry_keeps_original_decision_and_reservation(db_isolated, monkeypatch):
    workspace = trading.create_workspace({"name": "Synthetic retry workspace"})
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
            "start_at": "2025-01-01T00:00:00Z",
            "end_at": "2025-01-02T00:00:00Z",
            "accounts": [{"capital": "1000"}],
            "observations": [
                {"instrument": instrument, "timestamp": "2025-01-01T00:00:00Z", "price": "100"}
            ],
        },
    )
    asyncio.run(trading.apply_run_action(run["id"], {"action": "start"}))
    account = trading.get_account(run["id"])["items"][0]
    intent = {
        "account_id": account["id"],
        "instrument": instrument,
        "side": "buy",
        "quantity": "1",
        "request_id": "synthetic-retry",
    }
    receipt = {"ok": True, "result": {"summary": "Synthetic retry analysis", "orders": [intent]}}
    monkeypatch.setattr(trading_scripts, "run_trading_script", lambda *_args, **_kwargs: receipt)
    tool_permissions.set_tool_permission("core", "submit_trading_order", "always_allow")
    first = asyncio.run(trading_scripts.execute_strategy(run["id"], "print('synthetic')"))[
        "orders"
    ][0]
    assert first["decision_id"]
    second = asyncio.run(trading_scripts.execute_strategy(run["id"], "print('synthetic')"))[
        "orders"
    ][0]
    assert first["id"] == second["id"]
    assert first["decision_id"] == second["decision_id"]
    assert trading.list_orders(run["id"])["total"] == 1
    assert trading.get_account(run["id"])["items"][0]["available_cash"] == "900"
    receipt["result"]["orders"][0] = {**intent, "quantity": "2"}
    with pytest.raises(ConflictError, match="different order"):
        asyncio.run(trading_scripts.execute_strategy(run["id"], "print('changed')"))
