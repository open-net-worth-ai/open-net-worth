"""Executor-chosen current market subscriptions within the reviewed trading authority."""

import re

from backend.core.errors import ConflictError, ValidationError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import TradingMarketRequest
from backend.services.trading.common import assert_running, event, now, require_bound_run, validated
from backend.services.trading.market import get_market_data, instrument_key


def market_instruments(run):
    mandate = run["workspace_snapshot"]["mandate"]
    candidates = mandate.get("instruments") or [
        *run.get("subscribed_instruments", []),
        *(row["instrument"] for row in run["observations"]),
    ]
    scopes = {(account["venue"], account["currency"]) for account in run["accounts"]}
    allowed = set(mandate["allowed_asset_classes"])
    return list(
        {
            instrument_key(row): row
            for row in candidates
            if row["asset_class"] in allowed and (row["venue"], row["currency"]) in scopes
        }.values()
    )


async def request_market_data(run_id, data):
    import asyncio

    from backend.services.trading.price_feed import refresh_run_market_data

    clean = validated(TradingMarketRequest, data)
    with trading_transaction() as conn:
        require_bound_run(conn, run_id)
        run = TradingStore("runs").required(run_id, conn=conn)
        assert_running(run)
        if run["mode"] == "backtest":
            raise ConflictError("Historical environments cannot request current market data")
        mandate = run["workspace_snapshot"]["mandate"]
        if mandate.get("market_data_source", "supplied") != "yahoo":
            raise ConflictError(
                "The reviewed data source is supplied; current Yahoo requests are not authorized"
            )
        _validate_requested(run, clean["instruments"])
        subscriptions = {instrument_key(row): row for row in run.get("subscribed_instruments", [])}
        subscriptions.update({instrument_key(row): row for row in clean["instruments"]})
        if len(subscriptions) > 100:
            raise ValidationError("A workspace can subscribe to at most 100 instruments")
        run = TradingStore("runs").update_by_id(
            run_id, {"subscribed_instruments": list(subscriptions.values())}, conn=conn
        )
        event(
            conn,
            run,
            "market_subscription_requested",
            {"instruments": clean["instruments"], "source": "yahoo"},
        )
    try:
        refresh = await asyncio.to_thread(refresh_run_market_data, run_id)
        if refresh.get("status") == "unavailable":
            raise ConflictError(refresh["message"])
    except Exception as exc:
        record_environment_failure(run_id, exc)
        raise
    record_environment_success(run_id)
    return {
        "instruments": clean["instruments"],
        "subscribed": len(subscriptions),
        "refresh": refresh,
        "market_data": get_market_data(run_id, limit=100, latest=True),
    }


def _validate_requested(run, instruments):
    mandate = run["workspace_snapshot"]["mandate"]
    allowlist = {instrument_key(row) for row in mandate.get("instruments", [])}
    scopes = {(account["venue"], account["currency"]) for account in run["accounts"]}
    for row in instruments:
        if row["asset_class"] not in mandate["allowed_asset_classes"]:
            raise ConflictError("Requested asset class is outside the mandate")
        if allowlist and instrument_key(row) not in allowlist:
            raise ConflictError("Requested instrument is outside the explicit mandate universe")
        if (row["venue"], row["currency"]) not in scopes:
            raise ConflictError("Requested venue and currency must match an allocated account")
        if row["currency"] != "USD":
            raise ConflictError("The current Yahoo feed supports USD-quoted instruments only")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.^=/_-]{0,31}", row["symbol"]):
            raise ValidationError("Unsupported current market symbol format")


def record_environment_failure(run_id, exc):
    from backend.core.errors import OpenNetWorthError
    from backend.services.trading.work_records import redact_work_text

    message = (
        redact_work_text(exc.message) if isinstance(exc, OpenNetWorthError) else type(exc).__name__
    )
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        TradingStore("runs").update_by_id(
            run_id, {"environment_error": message, "environment_checked_at": now()}, conn=conn
        )
        event(
            conn,
            run,
            "environment_update_failed",
            {
                "error_type": type(exc).__name__,
                "message": message,
                "new_orders": "require_available_market_data",
            },
        )


def record_environment_success(run_id):
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        if run.get("environment_error"):
            TradingStore("runs").update_by_id(
                run_id, {"environment_error": None, "environment_checked_at": now()}, conn=conn
            )
            event(conn, run, "environment_updates_restored")
