"""Broker receipts are execution evidence; account snapshots detect unexplained activity."""

from backend.core.errors import ConflictError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.services.trading.common import ACTIVE_ORDER_STATES, amount, event
from backend.services.trading.fills import apply_receipt
from backend.services.trading.market import environment_time
from backend.services.trading.performance import snapshot_performance
from backend.services.trading.providers import get_live_adapter
from backend.services.trading.workspaces import public_run


def account_matches(expected, actual):
    if (
        actual.get("broker_account_id") != expected["broker_account_id"]
        or actual.get("currency") != expected["currency"]
    ):
        return False
    if abs(amount(actual["cash"]) - amount(expected["cash"])) > amount("0.00000001"):
        return False
    held = {
        key: amount(row["quantity"])
        for key, row in expected["positions"].items()
        if amount(row["quantity"])
    }
    broker = {
        key: amount(row["quantity"])
        for key, row in actual.get("positions", {}).items()
        if amount(row["quantity"])
    }
    return held == broker


async def reconcile_run(run_id):
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        if run["mode"] != "live":
            raise ConflictError("Only live runs use broker reconciliation")
        if run["state"] == "ready":
            raise ConflictError("Start the live mandate before reconciling its executions")
        run = TradingStore("runs").update_by_id(
            run_id, {"state": "needs_reconciliation"}, conn=conn
        )
        accounts = TradingStore("accounts").list_scope(run_id, conn=conn)
        orders = TradingStore("orders").list_scope(run_id, conn=conn)
    results = []
    for account in accounts:
        try:
            results.append(
                (
                    account,
                    await get_live_adapter(account).reconcile(
                        account, [row for row in orders if row["account_id"] == account["id"]]
                    ),
                )
            )
        except Exception as exc:
            with trading_transaction() as conn:
                event(
                    conn,
                    run,
                    "reconciliation_failed",
                    {"account_id": account["id"], "reason": type(exc).__name__},
                )
            return public_run(run)
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        run = TradingStore("runs").update_by_id(
            run_id, {"current_time": environment_time(run).isoformat()}, conn=conn
        )
        clean = True
        for account, result in results:
            clean = _apply_account_result(conn, run, account, result) and clean
        unresolved = any(
            row["status"] in {"submitting", "submission_unknown", "cancel_pending", "pending"}
            for row in TradingStore("orders").list_scope(run_id, conn=conn)
        )
        desired = run.get("desired_state", "paused")
        outstanding = any(
            row["status"] in ACTIVE_ORDER_STATES
            for row in TradingStore("orders").list_scope(run_id, conn=conn)
        )
        state = (
            desired
            if clean and not unresolved and (desired == "running" or not outstanding)
            else "needs_reconciliation"
        )
        run = TradingStore("runs").update_by_id(run_id, {"state": state}, conn=conn)
        event(conn, run, "reconciled" if clean else "outside_account_activity", {"state": state})
        snapshot_performance(conn, run)
    from backend.services.trading.book_projection import project_live_fills

    project_live_fills(run_id)
    if desired != "running":
        await _drive_stop_policy(run_id, clean)
    return public_run(TradingStore("runs").required(run_id))


async def _drive_stop_policy(run_id, account_matched):
    from backend.services.trading.book_projection import (
        has_unresolved_book_fills,
        project_live_fills,
    )
    from backend.services.trading.execution import cancel_order

    orders = TradingStore("orders").list_scope(run_id)
    canceled = False
    for order in orders:
        if order["status"] in {"open", "partially_filled"} and order.get("broker_order_id"):
            await cancel_order(run_id, order["id"])
            canceled = True
    if canceled:
        project_live_fills(run_id)
        for account in TradingStore("accounts").list_scope(run_id):
            try:
                actual = await get_live_adapter(account).inspect_account(account)
            except Exception:
                account_matched = False
                break
            account_matched = account_matched and account_matches(account, actual)
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        if run.get("desired_state") == "running" or not account_matched:
            return
        outstanding = any(
            order["status"] in ACTIVE_ORDER_STATES
            for order in TradingStore("orders").list_scope(run_id, conn=conn)
        )
        unbooked = has_unresolved_book_fills(run_id, conn=conn)
        if not outstanding and not unbooked:
            target = run.get("desired_state", "paused")
            run = TradingStore("runs").update_by_id(run_id, {"state": target}, conn=conn)
            event(conn, run, "stop_policy_settled", {"state": target})


def _apply_account_result(conn, run, account, result):
    for receipt in result.get("orders", []):
        order = TradingStore("orders").find_by_id(receipt.get("client_order_id", ""), conn=conn)
        if order is None or order["account_id"] != account["id"] or order["run_id"] != run["id"]:
            raise ConflictError("Broker returned an order outside this managed account")
        apply_receipt(conn, run, order, receipt)
    expected = TradingStore("accounts").required(account["id"], conn=conn)
    actual = result["account"]
    clean = account_matches(expected, actual)
    managed_ids = {
        row["id"]
        for row in TradingStore("orders").list_scope(run["id"], conn=conn)
        if row["account_id"] == account["id"]
    }
    if any(row.get("client_order_id") not in managed_ids for row in actual.get("open_orders", [])):
        clean = False
    TradingStore("accounts").update_by_id(
        account["id"], {"reconciliation_status": "matched" if clean else "mismatch"}, conn=conn
    )
    observations = actual.get("observations", [])
    if observations:
        from backend.models.trading import Observation
        from backend.services.trading.common import instant, validated

        valid = [validated(Observation, row) for row in observations]
        valid = [
            row
            for row in valid
            if instant(row["timestamp"]) <= environment_time(run)
            and instant(row["available_at"] or row["timestamp"]) <= environment_time(run)
        ]
        by_key = {
            (row["instrument"]["symbol"], row["timestamp"]): row for row in run["observations"]
        }
        by_key.update({(row["instrument"]["symbol"], row["timestamp"]): row for row in valid})
        run["observations"] = list(by_key.values())[-20000:]
        TradingStore("runs").update_by_id(
            run["id"], {"observations": run["observations"]}, conn=conn
        )
    return clean


def recover_runs():
    changed = []
    with trading_transaction() as conn:
        for run in TradingStore("runs").all(conn=conn):
            uncertain = False
            for order in TradingStore("orders").list_scope(run["id"], conn=conn):
                if order["status"] == "submitting":
                    TradingStore("orders").update_by_id(
                        order["id"], {"status": "submission_unknown"}, conn=conn
                    )
                    uncertain = True
            if run["mode"] == "live" and run["state"] in {"running", "stopping"}:
                uncertain = True
            if uncertain:
                TradingStore("runs").update_by_id(
                    run["id"], {"state": "needs_reconciliation"}, conn=conn
                )
                event(conn, run, "recovery_requires_reconciliation")
                changed.append(run["id"])
    return {"needs_reconciliation": changed}
