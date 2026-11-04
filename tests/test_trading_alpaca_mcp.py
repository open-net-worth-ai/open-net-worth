from types import SimpleNamespace

import pytest

from backend.core.errors import ConflictError
from backend.services.trading.alpaca_mcp import AlpacaMcpAdapter


def account():
    return {"broker_account_id": "synthetic-account", "currency": "USD", "venue": "alpaca"}


def order():
    return {
        "id": "synthetic-order",
        "quantity": "2",
        "side": "buy",
        "order_type": "market",
        "time_in_force": "day",
        "instrument": {
            "symbol": "ACME",
            "venue": "alpaca",
            "asset_class": "stock",
            "currency": "USD",
        },
    }


def broker_call(*, identity="synthetic-account", timeout=False, fill_qty="2"):
    calls = []

    async def call(name, args):
        calls.append((name, args))
        if name == "get_account_info":
            return {
                "id": identity,
                "currency": "USD",
                "status": "ACTIVE",
                "cash": "1000",
                "multiplier": "1",
            }
        if name == "get_all_positions":
            return []
        if name == "get_orders":
            return []
        if name == "place_stock_order":
            if timeout:
                raise TimeoutError("synthetic transport timeout")
            return {
                "id": "broker-order",
                "client_order_id": args["client_order_id"],
                "symbol": "ACME",
                "side": "buy",
                "qty": "2",
                "filled_qty": "2",
                "status": "filled",
            }
        if name == "get_account_activities_by_type":
            return [
                {
                    "id": "execution-1",
                    "order_id": "broker-order",
                    "qty": fill_qty,
                    "price": "10",
                    "commission": "0.02",
                    "transaction_time": "2026-01-05T15:00:01Z",
                }
            ]
        raise AssertionError(name)

    return call, calls


async def test_receipt_uses_broker_execution_id_and_client_order_identity():
    call, calls = broker_call()
    receipt = await AlpacaMcpAdapter(call).submit(account(), order())
    assert receipt["status"] == "filled"
    assert receipt["fills"][0]["execution_id"] == "execution-1"
    assert receipt["fills"][0]["fees"] == "0.02"
    args = next(args for name, args in calls if name == "place_stock_order")
    assert args["client_order_id"] == "synthetic-order"


async def test_changed_broker_identity_refuses_before_submission():
    call, calls = broker_call(identity="different-account")
    with pytest.raises(ConflictError, match="differs"):
        await AlpacaMcpAdapter(call).submit(account(), order())
    assert not any(name.startswith("place_") for name, _ in calls)


async def test_ambiguous_transport_failure_propagates_without_resubmission():
    call, calls = broker_call(timeout=True)
    with pytest.raises(TimeoutError):
        await AlpacaMcpAdapter(call).submit(account(), order())
    assert len([name for name, _ in calls if name == "place_stock_order"]) == 1


async def test_incomplete_fill_history_is_not_a_successful_execution():
    call, _ = broker_call(fill_qty="1")
    with pytest.raises(ConflictError, match="incomplete"):
        await AlpacaMcpAdapter(call).submit(account(), order())


async def test_structured_transport_keeps_existing_permission_authority(monkeypatch):
    from backend.mcp import client_manager as module

    server = SimpleNamespace(
        id="synthetic",
        discovered_tools=[
            {
                "name": "get_account_info",
                "inputSchema": {"type": "object", "additionalProperties": False},
            }
        ],
    )
    manager = module.McpClientManager(store=SimpleNamespace(find=lambda _: server))
    monkeypatch.setattr(manager, "_dispatch_refusal", lambda *_: None)
    monkeypatch.setattr(module.tool_permissions, "get_tool_permission", lambda *_: "needs_approval")
    monkeypatch.setattr(manager, "_call_tool_blocking", lambda *_: pytest.fail("must not dispatch"))
    with pytest.raises(ConflictError, match="standing permission"):
        await manager.call_structured_tool("synthetic", "get_account_info", {})


def test_trading_history_and_grants_cannot_be_rewound_or_exported_as_secrets():
    from backend.ai.checkpoint._policy import _EXCLUDED_TABLES
    from backend.core.sensitive import (
        NON_RESTORABLE_TABLES,
        SECRET_TABLES,
        TRADING_OPERATIONAL_TABLES,
    )
    from backend.db.sql_console import DENIED_TABLES

    assert TRADING_OPERATIONAL_TABLES <= NON_RESTORABLE_TABLES
    assert TRADING_OPERATIONAL_TABLES <= _EXCLUDED_TABLES
    assert "trading_grants" in SECRET_TABLES & DENIED_TABLES
    assert "trading_fills" not in SECRET_TABLES
