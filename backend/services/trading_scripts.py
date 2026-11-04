"""Execute isolated strategy code and route its intents through the trading choke."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError as SchemaError

from backend.core.errors import ValidationError
from backend.core.trading_runtime import trading_context
from backend.db.trading_store import TradingStore
from backend.models.trading import Decision, OrderIntent
from backend.sandbox.trading import run_trading_script
from backend.services import tool_permissions, trading
from backend.services.trading.common import identity
from backend.services.trading.work_records import write_workspace_file


async def execute_strategy(run_id: str, source: str) -> dict:
    run = trading.get_run(run_id)
    if run["state"] != "running":
        raise ValidationError("A strategy can execute only in a running trading run")
    context = trading_context()
    parent_lease = context.cycle_lease_id if context and context.run_id == run_id else None
    if parent_lease is not None:
        if run.get("agent_lease_id") != parent_lease or datetime.fromisoformat(
            run["agent_lease_expires_at"]
        ) <= datetime.now(timezone.utc):
            raise ValidationError("The parent decision lease is no longer valid")
        lease = None
    else:
        lease = trading.claim_agent_cycle(run_id, paid=False)
    try:
        return await _execute(run_id, source)
    except Exception as exc:
        trading.record_activity(run_id, "script_failed", {"error_type": type(exc).__name__})
        raise
    finally:
        if lease is not None:
            trading.release_agent_cycle(run_id, lease["lease_id"])


async def _execute(run_id: str, source: str) -> dict:
    source_digest = hashlib.sha256(source.encode()).hexdigest()
    source_file = write_workspace_file(run_id, "strategy.py", source)
    trading.update_runtime(
        run_id, {"agent_script_source": source, "agent_script_sha256": source_digest}
    )
    snapshot = {
        "environment": trading.get_environment(run_id),
        "accounts": trading.get_account(run_id),
        "observations": trading.get_market_data(run_id, latest=True),
    }
    artifact = {
        "kind": "strategy",
        "source": source_file["payload"]["content"],
        "sha256": source_file["payload"]["sha256"],
        "file_event_id": source_file["id"],
        "version": source_file["payload"]["version"],
        "redacted": source_file["payload"]["redacted"],
    }
    trading.record_activity(run_id, "script_started", {"artifact": artifact})
    cancellation = threading.Event()
    execution = asyncio.create_task(
        asyncio.to_thread(run_trading_script, source, snapshot, cancel=cancellation)
    )
    try:
        receipt = await asyncio.shield(execution)
    except asyncio.CancelledError:
        cancellation.set()
        try:
            await asyncio.wait_for(asyncio.shield(execution), timeout=15)
        except (TimeoutError, asyncio.CancelledError):
            pass
        raise
    if not receipt.get("ok"):
        trading.record_activity(run_id, "script_failed", receipt)
        return {"run_id": run_id, **receipt}
    output = receipt["result"]
    summary = str(output.get("summary") or "Strategy completed")[:4000]
    decision = {"summary": summary, "artifacts": [artifact]}
    if output.get("wake_after_seconds") is not None:
        decision["wake_after_seconds"] = output["wake_after_seconds"]
    try:
        orders = [
            OrderIntent.model_validate(item).model_dump(mode="json")
            for item in output.get("orders", [])
        ]
        decision = Decision.model_validate(decision).model_dump(mode="json")
    except SchemaError as exc:
        raise ValidationError("Strategy output contains invalid order or decision data") from exc
    if (
        orders
        and tool_permissions.get_tool_permission("core", "submit_trading_order") != "always_allow"
    ):
        raise ValidationError(
            "The owner must allow submit_trading_order before a script can place orders"
        )
    recorded = trading.record_decision(run_id, decision)
    results = []
    for intent in orders:
        results.append(await trading.submit_order(run_id, _linked_intent(run_id, intent, recorded)))
    trading.record_activity(run_id, "script_finished", {**receipt, "orders": results})
    fresh = trading.get_run(run_id)
    next_wake = (
        datetime.now(timezone.utc).isoformat()
        if fresh["mode"] == "backtest"
        else fresh.get("agent_wake_at")
        or (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    )
    trading.update_runtime(run_id, {"next_wake_at": next_wake})
    return {"run_id": run_id, **receipt, "orders": results}


def _linked_intent(run_id: str, intent: dict, decision: dict) -> dict:
    existing = TradingStore("orders").find_by_id(
        identity(run_id, intent["account_id"], intent["request_id"])
    )
    decision_id = existing.get("decision_id") if existing else decision["id"]
    return {**intent, "decision_id": decision_id}
