"""Bounded agent summaries and runtime-owned usage receipts."""

from datetime import timedelta

from backend.core.errors import ValidationError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import Decision
from backend.services.trading.common import amount, collection, event, validated
from backend.services.trading.market import environment_time


def record_activity(run_id, kind, payload):
    if not isinstance(kind, str) or len(kind) > 80:
        raise ValidationError("Invalid trading activity kind")
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        return event(conn, run, kind, payload)


def record_decision(run_id, data):
    clean = validated(Decision, data)
    clean.pop("model_cost_usd", None)
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        wake = clean.get("wake_after_seconds")
        TradingStore("runs").update_by_id(
            run_id,
            {
                "last_decision_time": run["current_time"],
                "agent_wake_at": (environment_time(run) + timedelta(seconds=wake)).isoformat()
                if wake
                else None,
            },
            conn=conn,
        )
        return event(conn, run, "agent_decision", clean)


def record_agent_usage(run_id, data, *, conn=None):
    cost = amount(data.get("model_cost_usd", 0))
    receipt_id = data.get("receipt_id") or data.get("turn_id")
    if cost < 0 or not receipt_id:
        raise ValidationError("Usage needs a unique receipt ID and nonnegative cost")
    if conn is None:
        with trading_transaction() as connection:
            return record_agent_usage(run_id, data, conn=connection)
    run = TradingStore("runs").required(run_id, conn=conn)
    receipts = run.get("usage_receipt_ids", [])
    if receipt_id in receipts:
        return {"idempotent": True, "model_cost_usd": run["model_cost_usd"]}
    total = amount(run["model_cost_usd"]) + cost
    patch = {"model_cost_usd": str(total), "usage_receipt_ids": [*receipts, receipt_id]}
    if total >= amount(run["workspace_snapshot"]["mandate"]["max_model_cost_usd"]):
        patch.update(agent_state="budget_exhausted")
    TradingStore("runs").update_by_id(run_id, patch, conn=conn)
    event(conn, run, "agent_usage", {**data, "model_cost_usd": str(cost)})
    return {"model_cost_usd": str(total)}


def list_events(run_id, *, limit=100, offset=0, after=None):
    TradingStore("runs").required(run_id)
    events = TradingStore("events").list_scope(run_id)
    if after is not None:
        events = [row for row in events if row["sequence"] > after]
    page = collection(events, limit=limit, offset=offset)
    return {**page, "next_cursor": page["items"][-1]["sequence"] if page["items"] else after}
