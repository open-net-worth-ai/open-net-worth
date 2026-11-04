"""Current run-local observations from the app's existing public quote service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.core.errors import ConflictError
from backend.db.trading_store import TradingStore
from backend.models.trading import Observation
from backend.services import prices
from backend.services.trading import ingest_observations, record_activity
from backend.services.trading.common import instant
from backend.services.trading.market_requests import market_instruments


def _feed_snapshot(run_id: str) -> dict:
    return TradingStore("runs").required(run_id)


def refresh_run_market_data(run_id: str) -> dict:
    run = _feed_snapshot(run_id)
    if run["mode"] == "backtest":
        raise ConflictError("Historical runs cannot access current market feeds")
    mandate = run["workspace_snapshot"]["mandate"]
    if mandate.get("market_data_source", "supplied") != "yahoo":
        return {"received": 0, "source": "supplied"}
    instruments = market_instruments(run)
    universe = {
        tuple(row[k] for k in ("symbol", "venue", "asset_class", "currency")): row
        for row in instruments
    }
    if not universe:
        return {"received": 0, "source": "yahoo", "status": "awaiting_executor_selection"}
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
                "received_at": received_at,
                "source": "yahoo",
            }
        )
    if missing:
        record_activity(run_id, "market_data_unavailable", {"source": "yahoo", "symbols": missing})
    has_current_quotes = bool(observations)
    bars, revisions = _history_bars(run_id, run, universe, symbols, received_at)
    quote_keys = {(tuple(row["instrument"].values()), row["timestamp"]) for row in observations}
    observations.extend(
        row
        for row in bars
        if (tuple(row["instrument"].values()), row["timestamp"]) not in quote_keys
    )
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
            if Decimal(str(prior[key]["price"])) == Decimal(str(row["price"])):
                row.clear()
                row.update(Observation.model_validate(prior[key]).model_dump(mode="json"))
                continue
            for field in ("available_at", "received_at"):
                row[field] = prior[key].get(field)
    result = {"received": 0}
    for index in range(0, len(observations), 2000):
        result["received"] += ingest_observations(run_id, observations[index : index + 2000])[
            "received"
        ]
    record_activity(
        run_id,
        "market_feed_refreshed",
        {
            "source": "yahoo",
            "received": len(observations),
            "missing": missing,
            "bars_received": len(bars),
            "bar_revisions_retained_original": revisions,
        },
    )
    return {
        **result,
        "source": "yahoo",
        "missing": missing,
        **(
            {
                "status": "unavailable",
                "message": "The current market source returned no usable quotes for subscribed instruments",
            }
            if not has_current_quotes
            else {}
        ),
    }


def _history_bars(run_id, run, universe, symbols, received_at):
    result, revisions = [], 0
    prior = {
        (
            tuple(row["instrument"][k] for k in ("symbol", "venue", "asset_class", "currency")),
            row["timestamp"],
        ): row
        for row in run["observations"]
    }
    cutoff = instant(received_at) - timedelta(minutes=5)
    for key, symbol in symbols.items():
        try:
            history = prices.fetch_history_result(symbol, "1D", "yahoo")
        except Exception as exc:
            record_activity(
                run_id,
                "market_bars_unavailable",
                {"source": "yahoo", "symbol": symbol, "error_type": type(exc).__name__},
            )
            continue
        for point in history.get("points", [])[-500:]:
            if not all(point.get(field) is not None for field in ("open", "high", "low", "close")):
                continue
            try:
                stamp = datetime.fromisoformat(point["date"].replace("Z", "+00:00"))
                if stamp.utcoffset() is None or stamp > cutoff:
                    continue
                row = Observation.model_validate(
                    {
                        "instrument": universe[key],
                        "timestamp": stamp.isoformat(),
                        "available_at": received_at,
                        "received_at": received_at,
                        "price": point["close"],
                        "volume": point.get("volume"),
                        **{field: point[field] for field in ("open", "high", "low", "close")},
                        "source": "yahoo",
                        "source_version": "1D-intraday-provider-bars",
                        "interval": "provider_intraday",
                        "adjustment": "provider_default_unspecified",
                    }
                ).model_dump(mode="json")
            except (ValueError, KeyError, TypeError):
                continue
            existing = prior.get((key, row["timestamp"]))
            if existing:
                revisions += any(
                    existing.get(field) != row[field]
                    for field in ("open", "high", "low", "close", "volume")
                )
                continue
            result.append(row)
    return result, revisions
