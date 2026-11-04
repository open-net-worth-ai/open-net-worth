"""One order choke for direct tools, scripts, and every execution mode."""

from backend.core.errors import ConflictError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import OrderIntent
from backend.services.trading.common import (
    ACTIVE_ORDER_STATES,
    amount,
    assert_running,
    collection,
    event,
    fingerprint,
    identity,
    instant,
    now,
    require_bound_run,
    uses_broker_execution,
    validated,
)
from backend.services.trading.fills import apply_receipt
from backend.services.trading.market import environment_time, instrument_key, latest_quote
from backend.services.trading.performance import account_snapshot, snapshot_performance
from backend.services.trading.providers import get_live_adapter
from backend.services.trading.simulation import match_orders


async def submit_order(run_id, data):
    clean = validated(OrderIntent, data)
    with trading_transaction() as conn:
        require_bound_run(conn, run_id)
        run = TradingStore("runs").required(run_id, conn=conn)
        key = identity(run_id, clean["account_id"], clean["request_id"])
        existing = TradingStore("orders").find_by_id(key, conn=conn)
        if existing:
            if existing["intent_hash"] != fingerprint(clean):
                raise ConflictError("Request ID was already used for a different order")
            return existing
        assert_running(run)
        if not uses_broker_execution(run) and clean["time_in_force"] == "day":
            raise ConflictError(
                "DAY requires a verified session calendar; this simulator supports GTC"
            )
        run = TradingStore("runs").update_by_id(
            run_id, {"current_time": environment_time(run).isoformat()}, conn=conn
        )
        account, reserved_cash, reserved_qty = _reserve(conn, run, clean)
        order = {
            **clean,
            "id": key,
            "scope_id": run_id,
            "run_id": run_id,
            "intent_hash": fingerprint(clean),
            "instrument_key": instrument_key(clean["instrument"]),
            "status": "pending" if uses_broker_execution(run) else "open",
            "filled_quantity": "0",
            "reserved_cash": str(reserved_cash),
            "reserved_quantity": str(reserved_qty),
            "initial_reserved_cash": str(reserved_cash),
            "initial_reserved_quantity": str(reserved_qty),
            "created_at": now(),
            "submitted_at": run["current_time"],
        }
        TradingStore("orders").insert(order, conn=conn)
        event(
            conn,
            run,
            "order_submitted",
            {"order_id": key, "instrument": clean["instrument"], "side": clean["side"]},
        )
        if not uses_broker_execution(run):
            match_orders(conn, run)
            snapshot_performance(conn, run)
            return TradingStore("orders").required(key, conn=conn)
    return await dispatch_live_order(run_id, order["id"])


def _reserve(conn, run, clean):
    account = TradingStore("accounts").required(clean["account_id"], conn=conn)
    if account["run_id"] != run["id"]:
        raise ConflictError("Account is outside the trading run")
    instrument = clean["instrument"]
    mandate = run["workspace_snapshot"]["mandate"]
    if instrument["asset_class"] not in mandate["allowed_asset_classes"]:
        raise ConflictError("Asset class is outside the mandate")
    if mandate["instruments"] and instrument_key(instrument) not in {
        instrument_key(item) for item in mandate["instruments"]
    }:
        raise ConflictError("Instrument is outside the mandate universe")
    if instrument["currency"] != account["currency"] or instrument["venue"] != account["venue"]:
        raise ConflictError("Order currency and venue must match its allocated account")
    quote = latest_quote(run, instrument)
    if quote is None:
        raise ConflictError("No market observation is available at the environment time")
    if (
        run["mode"] != "backtest"
        and (environment_time(run) - instant(quote["timestamp"])).total_seconds()
        > mandate["max_quote_age_seconds"]
    ):
        raise ConflictError("Market observation is stale")
    quantity = amount(clean["quantity"])
    price = amount(clean["limit_price"] or quote["price"])
    if clean["side"] == "sell":
        price = max(price, amount(quote["price"]))
    notional = quantity * price
    if mandate["max_order_notional"] is not None and notional > amount(
        mandate["max_order_notional"]
    ):
        raise ConflictError("Order exceeds the mandate notional limit")
    snapshot = account_snapshot(run, account, conn=conn)
    if clean["side"] == "sell":
        position = snapshot["positions"].get(instrument_key(instrument), {})
        if quantity > amount(position.get("available_quantity", 0)):
            raise ConflictError("Insufficient unreserved position quantity")
        return account, amount(0), quantity
    slip = amount(mandate["slippage_bps"]) / 10000 if clean["order_type"] == "market" else amount(0)
    reserved = notional * (1 + slip) * (1 + amount(mandate["fee_bps"]) / 10000)
    if reserved > amount(snapshot["available_cash"]):
        raise ConflictError("Insufficient unreserved account cash")
    return account, reserved, amount(0)


async def dispatch_live_order(run_id, order_id):
    with trading_transaction() as conn:
        require_bound_run(conn, run_id)
        run = TradingStore("runs").required(run_id, conn=conn)
        order = _owned_order(conn, run_id, order_id)
        if order["status"] != "pending":
            return order
        assert_running(run)
        account = TradingStore("accounts").required(order["account_id"], conn=conn)
        adapter = get_live_adapter(account)
        order = TradingStore("orders").update_by_id(order_id, {"status": "submitting"}, conn=conn)
        event(conn, run, "broker_submission_started", {"order_id": order_id})
    try:
        receipt = await adapter.submit(account, order)
        with trading_transaction() as conn:
            run = TradingStore("runs").required(run_id, conn=conn)
            order = apply_receipt(conn, run, _owned_order(conn, run_id, order_id), receipt)
            event(
                conn, run, "broker_order_updated", {"order_id": order_id, "status": order["status"]}
            )
            snapshot_performance(conn, run)
    except Exception as exc:
        return _unknown(run_id, order_id, "submission_unknown", type(exc).__name__)
    from backend.services.trading.book_projection import project_live_fills

    if run["mode"] == "live":
        project_live_fills(run_id)
    return order


def _unknown(run_id, order_id, state, reason):
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        order = TradingStore("orders").update_by_id(
            order_id, {"status": state, "error": reason}, conn=conn
        )
        TradingStore("runs").update_by_id(run_id, {"state": "needs_reconciliation"}, conn=conn)
        event(conn, run, state, {"order_id": order_id, "reason": reason})
    return order


def _owned_order(conn, run_id, order_id):
    order = TradingStore("orders").required(order_id, conn=conn)
    if order["run_id"] != run_id:
        raise ConflictError("Order is outside this run")
    return order


async def cancel_order(run_id, order_id):
    with trading_transaction() as conn:
        require_bound_run(conn, run_id)
        run = TradingStore("runs").required(run_id, conn=conn)
        order = _owned_order(conn, run_id, order_id)
        if order["status"] not in ACTIVE_ORDER_STATES:
            return order
        if not uses_broker_execution(run) or order["status"] == "pending":
            order = TradingStore("orders").update_by_id(
                order_id,
                {"status": "canceled", "reserved_cash": "0", "reserved_quantity": "0"},
                conn=conn,
            )
            event(conn, run, "order_canceled", {"order_id": order_id})
            return order
        account = TradingStore("accounts").required(order["account_id"], conn=conn)
        adapter = get_live_adapter(account)
        order = TradingStore("orders").update_by_id(
            order_id, {"status": "cancel_pending"}, conn=conn
        )
        event(conn, run, "order_cancel_requested", {"order_id": order_id})
    try:
        receipt = await adapter.cancel(account, order)
        with trading_transaction() as conn:
            run = TradingStore("runs").required(run_id, conn=conn)
            order = apply_receipt(conn, run, _owned_order(conn, run_id, order_id), receipt)
            event(
                conn, run, "broker_order_updated", {"order_id": order_id, "status": order["status"]}
            )
            return order
    except Exception as exc:
        return _unknown(run_id, order_id, "cancel_pending", type(exc).__name__)


def list_orders(run_id, *, limit=100, offset=0):
    TradingStore("runs").required(run_id)
    return collection(TradingStore("orders").list_scope(run_id), limit=limit, offset=offset)


def list_fills(run_id, *, limit=100, offset=0):
    TradingStore("runs").required(run_id)
    return collection(TradingStore("fills").list_scope(run_id), limit=limit, offset=offset)
