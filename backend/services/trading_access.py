"""Revocable external-run grants. Only token hashes are persisted."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from backend.core.errors import ValidationError
from backend.db.trading_store import TradingStore, trading_transaction


def issue_access(run_id: str, expires_in_seconds: int = 3600) -> dict:
    if not 60 <= expires_in_seconds <= 86400:
        raise ValidationError("Access expiry must be between 60 and 86400 seconds")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    with trading_transaction() as conn:
        store = TradingStore("runs")
        run = store.required(run_id, conn=conn)
        if run["state"] in {"stopped", "completed", "failed"}:
            raise ValidationError("Cannot authorize a terminal trading run")
        TradingStore("grants").upsert(
            {
                "id": run_id,
                "scope_id": run_id,
                "token_hash": token_digest(token),
                "expires_at": expires_at.isoformat(),
            },
            conn=conn,
        )
    return {
        "run_id": run_id,
        "token": token,
        "expires_at": expires_at.isoformat(),
        "endpoint": f"/api/trading/gateway/{run_id}",
    }


def revoke_access(run_id: str) -> dict:
    with trading_transaction() as conn:
        store = TradingStore("runs")
        store.required(run_id, conn=conn)
        TradingStore("grants").upsert(
            {"id": run_id, "scope_id": run_id, "token_hash": None, "expires_at": None}, conn=conn
        )
    return {"run_id": run_id, "revoked": True}


def authenticate_access(run_id: str, token: str) -> bool:
    if not token or len(token) > 256:
        return False
    run = TradingStore("runs").find_by_id(run_id)
    if not run or run.get("state") in {"stopped", "completed", "failed"}:
        return False
    grant = TradingStore("grants").find_by_id(run_id) or {}
    expected = grant.get("token_hash")
    expires = grant.get("expires_at")
    if not isinstance(expected, str) or not isinstance(expires, str):
        return False
    try:
        alive = datetime.fromisoformat(expires) > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False
    return alive and hmac.compare_digest(token_digest(token), expected)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
