"""Shared trading validation and durable activity records."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import ValidationError as SchemaError

from backend.core.data_events import publish
from backend.core.errors import ConflictError, ValidationError
from backend.core.ids import new_id
from backend.core.trading_runtime import trading_context
from backend.db.engine import on_commit
from backend.db.trading_store import TradingStore

ACTIVE_ORDER_STATES = {
    "pending",
    "submitting",
    "open",
    "partially_filled",
    "cancel_pending",
    "submission_unknown",
}


def uses_broker_execution(run: dict) -> bool:
    return run["mode"] == "live" or run.get("external_execution") == "binance_testnet"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def amount(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValidationError("Amount must be finite")
    return result


def validated(model, data: dict) -> dict:
    try:
        return model.model_validate(data).model_dump(mode="json")
    except SchemaError as exc:
        raise ValidationError(
            "Invalid trading input", errors=exc.errors(include_context=False)
        ) from exc


def fingerprint(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def identity(*parts: str) -> str:
    return "tr_" + fingerprint({"parts": parts})[:32]


def event(conn, run: dict, kind: str, payload: dict | None = None) -> dict:
    sequence = TradingStore("events").count_scope(run["id"], conn=conn) + 1
    row = {
        "id": new_id("te"),
        "scope_id": run["id"],
        "kind": kind,
        "run_id": run["id"],
        "sequence": sequence,
        "environment_time": run["current_time"],
        "created_at": now(),
        "payload": payload or {},
    }
    TradingStore("events").insert(row, conn=conn)
    on_commit(lambda: publish(("trading",)))
    return row


def assert_running(run: dict) -> None:
    if run["state"] != "running":
        raise ConflictError("Trading run is not running", state=run["state"])
    if run["mode"] != "backtest" and instant(now()) >= instant(run["end_at"]):
        raise ConflictError("Trading mandate has expired")
    if instant(run["current_time"]) >= instant(run["end_at"]):
        raise ConflictError("Trading mandate has expired")


def require_bound_run(conn, run_id):
    context = trading_context()
    if context is None:
        return
    if context.run_id != run_id:
        raise ConflictError("Trading principal is bound to another run")
    if context.grant_digest is not None:
        grant = TradingStore("grants").find_by_id(run_id, conn=conn)
        if not grant or not grant.get("token_hash") or not grant.get("expires_at"):
            raise ConflictError("Trading grant is revoked")
        if not hmac.compare_digest(grant["token_hash"], context.grant_digest) or instant(
            grant["expires_at"]
        ) <= instant(now()):
            raise ConflictError("Trading grant is invalid or expired")


def collection(items: list, *, limit: int = 100, offset: int = 0) -> dict:
    limit = max(1, min(limit, 500))
    offset = max(offset, 0)
    return {"items": items[offset : offset + limit], "total": len(items)}
