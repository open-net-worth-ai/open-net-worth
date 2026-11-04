"""Run-local market observations; historical data never escapes its availability clock."""

from __future__ import annotations

from backend.core.errors import ConflictError, ValidationError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import Instrument, Observation
from backend.services.trading.common import collection, event, instant, now, validated


def environment_time(run):
    if run["mode"] == "backtest" or run["state"] in {"stopped", "completed", "failed"}:
        return instant(run["current_time"])
    return min(instant(now()), instant(run["end_at"]))


def instrument_key(instrument):
    return Instrument.model_validate(instrument).key


def available_observations(run, *, instrument=None):
    cutoff = environment_time(run)
    key = instrument_key(instrument) if instrument else None
    return [
        row
        for row in run["observations"]
        if instant(row["available_at"] or row["timestamp"]) <= cutoff
        and instant(row["timestamp"]) <= cutoff
        and (key is None or instrument_key(row["instrument"]) == key)
    ]


def latest_quote(run, instrument):
    rows = available_observations(run, instrument=instrument)
    return max(rows, key=lambda row: instant(row["timestamp"])) if rows else None


def get_market_data(run_id, instrument=None, *, limit=100, offset=0, latest=False):
    run = TradingStore("runs").required(run_id)
    rows = available_observations(run, instrument=instrument)
    page = collection(rows, limit=limit, offset=offset)
    if latest:
        end = max(0, len(rows) - max(offset, 0))
        page = {"items": rows[max(0, end - min(max(limit, 1), 500)) : end], "total": len(rows)}
    return {
        **page,
        "current_time": environment_time(run).isoformat(),
        "data_version": run["data_version"],
        "schema_version": 1,
    }


def ingest_observations(run_id, observations):
    if not observations or len(observations) > 2000:
        raise ValidationError("Supply between 1 and 2000 observations")
    clean = [validated(Observation, row) for row in observations]
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        if run["mode"] == "backtest":
            raise ConflictError("A backtest dataset is immutable")
        cutoff = instant(now())
        for row in clean:
            if (
                instant(row["timestamp"]) > cutoff
                or instant(row["available_at"] or row["timestamp"]) > cutoff
            ):
                raise ValidationError("Current market feeds cannot publish future observations")
            if row["instrument"]["currency"] not in run["fx_rates"]:
                raise ValidationError("Missing quote currency FX rate")
        keyed = {
            (instrument_key(row["instrument"]), row["timestamp"]): row
            for row in run["observations"]
        }
        for row in clean:
            key = instrument_key(row["instrument"]), row["timestamp"]
            if key in keyed and keyed[key] != row:
                raise ConflictError("An observation timestamp cannot be silently rewritten")
            keyed[key] = row
        retained = sorted(keyed.values(), key=lambda r: instant(r["timestamp"]))[-20000:]
        run = TradingStore("runs").update_by_id(
            run_id,
            {
                "observations": retained,
                "current_time": environment_time(run).isoformat(),
            },
            conn=conn,
        )
        event(conn, run, "market_data_received", {"count": len(clean), "observations": clean})
    return {"received": len(clean)}


def get_environment(run_id):
    from backend.services.trading.common import uses_broker_execution

    run = TradingStore("runs").required(run_id)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "mode": run["mode"],
        "external_execution": run.get("external_execution"),
        "state": run["state"],
        "current_time": environment_time(run).isoformat(),
        "start_at": run["start_at"],
        "end_at": run["end_at"],
        "mandate": run["workspace_snapshot"]["mandate"],
        "data_version": run["data_version"],
        "capabilities": {
            "asset_classes": run["workspace_snapshot"]["mandate"]["allowed_asset_classes"],
            "order_types": ["market", "limit"],
            "time_in_force": ["day", "gtc"] if run["mode"] == "live" else ["gtc"],
            "session_calendar": "provider" if uses_broker_execution(run) else "observation_availability",
            "history_window": 20000,
            "shorting": False,
            "leverage": False,
            "market_data": "versioned_observations",
            "fill_model": "next_observation_volume_capped"
            if run["mode"] == "backtest"
            else "current_quote_or_broker",
            "corporate_actions": False,
            "historical_fx": "supplied_fixed_rates",
        },
    }
