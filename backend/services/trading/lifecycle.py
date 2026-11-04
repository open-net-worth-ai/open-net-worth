"""Server-owned mandate lifecycle and bounded environment advancement."""

from backend.core.errors import ConflictError, ValidationError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import RunAction
from backend.services.trading.common import (
    ACTIVE_ORDER_STATES,
    assert_running,
    event,
    instant,
    now,
    uses_broker_execution,
    validated,
)
from backend.services.trading.execution import cancel_order
from backend.services.trading.market import environment_time
from backend.services.trading.performance import snapshot_performance
from backend.services.trading.providers import get_live_adapter
from backend.services.trading.simulation import match_orders
from backend.services.trading.workspaces import public_run


async def apply_run_action(run_id, data):
    action = validated(RunAction, data)
    name = action["action"]
    if name == "reconcile":
        from backend.services.trading.reconciliation import reconcile_run

        return await reconcile_run(run_id)
    if name in {"start", "resume"}:
        return await _start(run_id, name)
    if name in {"pause", "stop"}:
        return await _stop(run_id, name)
    return _advance(run_id, action["until"])


async def _start(run_id, action):
    run = TradingStore("runs").required(run_id)
    expected = "ready" if action == "start" else "paused"
    if run["state"] != expected:
        raise ConflictError(f"Run must be {expected} to {action}")
    if run["mode"] != "backtest" and not (
        instant(run["start_at"]) <= instant(now()) < instant(run["end_at"])
    ):
        raise ConflictError("Current time is outside the trading mandate period")
    verified = await _verify_live(run, initial=action == "start") if uses_broker_execution(run) else {}
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        if run["state"] != expected:
            raise ConflictError("Run state changed during activation")
        for account_id, actual in verified.items():
            TradingStore("accounts").update_by_id(
                account_id, {"provider_id": actual["provider_id"]}, conn=conn
            )
        _assert_exclusive_account(conn, run)
        run = TradingStore("runs").update_by_id(
            run_id,
            {
                "state": "running",
                "desired_state": "running",
                "current_time": environment_time(run).isoformat(),
                "updated_at": now(),
            },
            conn=conn,
        )
        event(conn, run, "run_started" if action == "start" else "run_resumed")
        return public_run(snapshot_performance(conn, run))


async def _verify_live(run, *, initial):
    if run["mode"] == "live" and not run["live_authorized"]:
        raise ConflictError("Human authorization is required for a live run")
    verified = {}
    for account in TradingStore("accounts").list_scope(run["id"]):
        if not account.get("broker_account_id") or not account.get("connector_id"):
            raise ConflictError("Live account requires a broker identity and connector")
        actual = await get_live_adapter(account).inspect_account(account)
        if not isinstance(actual.get("provider_id"), str) or not actual["provider_id"]:
            raise ConflictError("Broker adapter did not establish a canonical provider identity")
        required = {"client_order_id", "lookup_order", "fills", "cancel"}
        if not actual.get("allocation_isolated") or not required <= set(
            actual.get("capabilities", [])
        ):
            raise ConflictError("Broker account cannot enforce isolated managed execution")
        if (
            actual.get("broker_account_id") != account["broker_account_id"]
            or actual.get("currency") != account["currency"]
        ):
            raise ConflictError("Broker account identity or currency does not match allocation")
        from backend.services.trading.reconciliation import account_matches

        if not account_matches(account, actual):
            raise ConflictError(
                "Broker state differs from allocated account; reconciliation required"
            )
        if initial and (actual.get("positions") or actual.get("open_orders")):
            raise ConflictError(
                "Start live management with a dedicated cash-only funded account without open orders"
            )
        managed = {
            row["id"]
            for row in TradingStore("orders").list_scope(run["id"])
            if row["account_id"] == account["id"] and row["status"] in ACTIVE_ORDER_STATES
        }
        if any(row.get("client_order_id") not in managed for row in actual.get("open_orders", [])):
            raise ConflictError("Broker has an open order outside this managed mandate")
        verified[account["id"]] = actual
    return verified


def _assert_exclusive_account(conn, run):
    if not uses_broker_execution(run):
        return
    active = {
        row["id"]
        for row in TradingStore("runs").all(conn=conn)
        if row["id"] != run["id"]
        and uses_broker_execution(row)
        and row["state"] not in {"stopped", "completed", "ready"}
    }
    own_accounts = TradingStore("accounts").list_scope(run["id"], conn=conn)
    own = {(row["provider_id"], row["broker_account_id"]) for row in own_accounts}
    if len(own) != len(own_accounts):
        raise ConflictError("A broker account cannot be allocated twice in the same run")
    for account in TradingStore("accounts").all(conn=conn):
        if (
            account["run_id"] in active
            and (account.get("provider_id"), account.get("broker_account_id")) in own
        ):
            raise ConflictError("Broker account is already managed by another run")


async def _stop(run_id, action):
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        if run["state"] in {"stopped", "completed"}:
            return public_run(run)
        target = "paused" if action == "pause" else "stopped"
        run = TradingStore("runs").update_by_id(
            run_id,
            {
                "state": "paused" if action == "pause" else "stopping",
                "desired_state": target,
                "current_time": environment_time(run).isoformat(),
                "updated_at": now(),
            },
            conn=conn,
        )
        event(conn, run, "run_pause_requested" if action == "pause" else "run_stop_requested")
        orders = [
            row
            for row in TradingStore("orders").list_scope(run_id, conn=conn)
            if row["status"] in ACTIVE_ORDER_STATES
        ]
    for order in orders:
        await cancel_order(run_id, order["id"])
    from backend.services.trading.book_projection import (
        has_unresolved_book_fills,
        project_live_fills,
    )

    if run["mode"] == "live":
        project_live_fills(run_id)
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        outstanding = any(
            row["status"] in ACTIVE_ORDER_STATES
            for row in TradingStore("orders").list_scope(run_id, conn=conn)
        )
        state = (
            ("needs_reconciliation" if uses_broker_execution(run) else "stopping")
            if outstanding or has_unresolved_book_fills(run_id, conn=conn)
            else target
        )
        run = TradingStore("runs").update_by_id(run_id, {"state": state}, conn=conn)
        event(conn, run, "run_state_changed", {"state": state, "positions_policy": "retain"})
        return public_run(snapshot_performance(conn, run))


def _advance(run_id, until):
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        assert_running(run)
        target = _advance_time(run, until)
        times = {instant(target)}
        if run["mode"] == "backtest":
            times.update(
                instant(row["available_at"] or row["timestamp"])
                for row in run["observations"]
                if instant(run["current_time"])
                < instant(row["available_at"] or row["timestamp"])
                <= instant(target)
            )
        for stamp in sorted(times):
            run = TradingStore("runs").update_by_id(
                run_id,
                {
                    "current_time": stamp.isoformat(),
                    "clock_cursor": run["clock_cursor"] + 1,
                    "updated_at": now(),
                },
                conn=conn,
            )
            match_orders(conn, run)
            run = snapshot_performance(conn, run)
        if instant(target) >= instant(run["end_at"]):
            _expire_simulated_orders(conn, run)
            TradingStore("runs").update_by_id(run_id, {"state": "completed"}, conn=conn)
        event(conn, run, "clock_advanced", {"current_time": target})
        return public_run(TradingStore("runs").required(run_id, conn=conn))


def _advance_time(run, until):
    if uses_broker_execution(run):
        raise ConflictError("Broker clocks advance through broker reconciliation")
    if run["mode"] == "paper":
        if until is not None:
            raise ValidationError("Paper clock follows current time")
        return environment_time(run).isoformat()
    candidates = [
        instant(row["available_at"] or row["timestamp"])
        for row in run["observations"]
        if instant(row["available_at"] or row["timestamp"]) > instant(run["current_time"])
    ]
    target = instant(until) if until else min(candidates, default=instant(run["end_at"]))
    if not instant(run["current_time"]) < target <= instant(run["end_at"]):
        raise ValidationError("Backtest clock must move forward within the mandate")
    return target.isoformat()


def _expire_simulated_orders(conn, run):
    for order in TradingStore("orders").list_scope(run["id"], conn=conn):
        if order["status"] in ACTIVE_ORDER_STATES:
            TradingStore("orders").update_by_id(
                order["id"],
                {"status": "expired", "reserved_cash": "0", "reserved_quantity": "0"},
                conn=conn,
            )
            event(conn, run, "order_expired", {"order_id": order["id"]})


async def expire_runs():
    runs = TradingStore("runs").all()
    expired = []
    for run in runs:
        if (
            run["mode"] != "backtest"
            and run["state"] in {"running", "paused", "needs_reconciliation"}
            and instant(now()) >= instant(run["end_at"])
        ):
            await _stop(run["id"], "stop")
            expired.append(run["id"])
    return {"expired": expired}
