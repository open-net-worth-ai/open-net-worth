"""End-to-end synthetic order accounting through the run's simulated clock."""

import asyncio
from decimal import Decimal

import pytest

from backend.core.errors import ConflictError
from backend.db.account_store import AccountStore
from backend.db.trading_store import TradingStore
from backend.services.trading import execution, lifecycle, performance, workspaces

T0 = "2026-01-05T14:30:00+00:00"
T1 = "2026-01-05T14:31:00+00:00"
T2 = "2026-01-05T14:32:00+00:00"
END = "2026-01-05T15:00:00+00:00"
INSTRUMENT = {"symbol": "ACME", "venue": "simulation", "asset_class": "stock", "currency": "USD"}


def call(coro):
    return asyncio.run(coro)


def setup_run():
    workspace = workspaces.create_workspace({"name": "Synthetic"})
    run = workspaces.create_run(
        workspace["id"],
        {
            "mode": "backtest",
            "start_at": T0,
            "end_at": END,
            "accounts": [{"capital": "1000"}],
            "observations": [
                {"instrument": INSTRUMENT, "timestamp": T0, "price": "100", "volume": "10"},
                {"instrument": INSTRUMENT, "timestamp": T1, "price": "110", "volume": "2"},
                {"instrument": INSTRUMENT, "timestamp": T2, "price": "120", "volume": "10"},
            ],
        },
    )
    call(lifecycle.apply_run_action(run["id"], {"action": "start"}))
    account = performance.get_account(run["id"])["items"][0]
    return run["id"], account["id"]


def order(account_id, request_id="synthetic-order", quantity="3"):
    return {
        "account_id": account_id,
        "instrument": INSTRUMENT,
        "side": "buy",
        "quantity": quantity,
        "request_id": request_id,
    }


def test_replay_reserves_cash_fills_in_steps_and_never_writes_real_book(db_isolated):
    real_accounts = AccountStore().all()
    run_id, account_id = setup_run()
    submitted = call(execution.submit_order(run_id, order(account_id)))
    assert submitted["status"] == "open"
    assert Decimal(performance.get_account(run_id)["items"][0]["available_cash"]) == Decimal("700")
    assert execution.list_fills(run_id)["total"] == 0
    call(lifecycle.apply_run_action(run_id, {"action": "advance"}))
    assert execution.list_orders(run_id)["items"][0]["filled_quantity"] == "2"
    call(lifecycle.apply_run_action(run_id, {"action": "advance"}))
    assert execution.list_orders(run_id)["items"][0]["status"] == "filled"
    assert execution.list_fills(run_id)["total"] == 2
    account = performance.get_account(run_id)["items"][0]
    assert Decimal(account["cash"]) == Decimal("660")
    assert performance.get_performance(run_id)["equity"] == 1020
    assert AccountStore().all() == real_accounts


def test_request_identity_and_reservation_prevent_duplicate_or_overspending(db_isolated):
    run_id, account_id = setup_run()
    first = call(execution.submit_order(run_id, order(account_id)))
    assert call(execution.submit_order(run_id, order(account_id)))["id"] == first["id"]
    with pytest.raises(ConflictError):
        call(execution.submit_order(run_id, order(account_id, quantity="4")))
    with pytest.raises(ConflictError):
        call(execution.submit_order(run_id, order(account_id, request_id="second", quantity="8")))
    assert execution.list_orders(run_id)["total"] == 1


def test_pause_cancels_open_orders_and_releases_reservation(db_isolated):
    run_id, account_id = setup_run()
    call(execution.submit_order(run_id, order(account_id)))
    paused = call(lifecycle.apply_run_action(run_id, {"action": "pause"}))
    assert paused["state"] == "paused"
    assert execution.list_orders(run_id)["items"][0]["status"] == "canceled"
    assert Decimal(performance.get_account(run_id)["items"][0]["available_cash"]) == Decimal("1000")


def test_live_run_cannot_be_started_or_dispatched_in_intermediate_branch(db_isolated):
    workspace = workspaces.create_workspace({"name": "Synthetic"})
    run = workspaces.create_run(
        workspace["id"], {"mode": "live", "start_at": T0, "live_authorized": True}
    )
    with pytest.raises(ConflictError):
        call(lifecycle.apply_run_action(run["id"], {"action": "start"}))
    with pytest.raises(ConflictError):
        call(execution.dispatch_live_order(run["id"], "not-an-order"))
    TradingStore("runs").update_by_id(run["id"], {"state": "running"})
    account_id = TradingStore("accounts").list_scope(run["id"])[0]["id"]
    with pytest.raises(ConflictError):
        call(execution.submit_order(run["id"], order(account_id)))
    assert TradingStore("orders").all() == []
