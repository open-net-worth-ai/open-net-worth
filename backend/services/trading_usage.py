"""Operator reconciliation of an incomplete provider billing receipt."""

from datetime import datetime, timezone

from backend.core.errors import ConflictError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.services import trading
from backend.services.trading.common import event


def reconcile_usage(
    run_id: str, *, receipt_id: str, additional_cost_usd, turn_id: str | None
) -> dict:
    with trading_transaction() as conn:
        store = TradingStore("runs")
        run = store.required(run_id, conn=conn)
        if run.get("usage_state") not in {"pending", "unknown"}:
            raise ConflictError("Model usage does not need reconciliation")
        if run.get("usage_turn_id") != turn_id:
            raise ConflictError("Receipt does not match the interrupted model turn")
        if run.get("agent_lease_expires_at") and datetime.fromisoformat(
            run["agent_lease_expires_at"]
        ) > datetime.now(timezone.utc):
            raise ConflictError("Wait until the active decision cycle has ended")
        result = trading.record_agent_usage(
            run_id,
            {
                "receipt_id": f"operator:{receipt_id}",
                "turn_id": turn_id,
                "model_cost_usd": additional_cost_usd,
                "source": "operator_reconciliation",
            },
            conn=conn,
        )
        store.update_by_id(run_id, {"usage_state": "settled", "last_agent_error": None}, conn=conn)
        event(conn, run, "agent_usage_reconciled", {"receipt_id": receipt_id, "turn_id": turn_id})
    return {"run_id": run_id, **result, "usage_state": "settled"}
