"""Current run-local observations from the app's existing public quote service."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.core.errors import ConflictError
from backend.db.trading_store import TradingStore
from backend.models.trading import Observation
from backend.services import prices
from backend.services.trading import ingest_observations, record_activity


def _feed_snapshot(run_id: str) -> dict:
    return TradingStore("runs").required(run_id)


def refresh_run_market_data(run_id: str) -> dict:
    run = _feed_snapshot(run_id)
    if run["mode"] == "backtest":
        raise ConflictError("Historical runs cannot access current market feeds")
    mandate = run["workspace_snapshot"]["mandate"]
    if mandate.get("market_data_source", "supplied") != "yahoo":
        return {"received": 0, "source": "supplied"}
    instruments = mandate.get("instruments") or [row["instrument"] for row in run["observations"]]
    universe = {
        tuple(row[k] for k in ("symbol", "venue", "asset_class", "currency")): row
        for row in instruments
    }
    if not universe:
        raise ConflictError("Current market data requires an explicit instrument universe")
    if any(row["currency"] != "USD" for row in universe.values()):
        raise ConflictError("The Yahoo trading feed currently requires USD-quoted instruments")
    symbols = {
        key: row["symbol"].replace("/", "-") if row["asset_class"] == "crypto" else row["symbol"]
        for key, row in universe.items()
    }
    quotes = prices.fetch_yahoo_quotes(list(dict.fromkeys(symbols.values())))
    observations, missing = [], []
    received_at = datetime.now(timezone.utc).isoformat()
    for key, symbol in symbols.items():
        price, timestamp = quotes.get("prices", {}).get(symbol), quotes.get("as_of", {}).get(symbol)
        if price is None or not timestamp:
            missing.append(symbol)
            continue
        value = Decimal(str(price))
        if not value.is_finite() or value <= 0:
            missing.append(symbol)
            continue
        observations.append(
            {
                "instrument": universe[key],
                "price": str(value),
                "timestamp": timestamp,
                "available_at": received_at,
            }
        )
    if missing:
        record_activity(run_id, "market_data_unavailable", {"source": "yahoo", "symbols": missing})
    observations = [Observation.model_validate(row).model_dump(mode="json") for row in observations]
    # Preserve the original availability instant when a provider repeats a quote.
    prior = {
        (
            tuple(row["instrument"][k] for k in ("symbol", "venue", "asset_class", "currency")),
            row["timestamp"],
        ): row
        for row in run["observations"]
    }
    for row in observations:
        key = (
            tuple(row["instrument"][k] for k in ("symbol", "venue", "asset_class", "currency")),
            row["timestamp"],
        )
        if key in prior:
            row["available_at"] = prior[key]["available_at"]
    result = ingest_observations(run_id, observations) if observations else {"received": 0}
    record_activity(
        run_id,
        "market_feed_refreshed",
        {"source": "yahoo", "received": len(observations), "missing": missing},
    )
    return {**result, "source": "yahoo", "missing": missing}
