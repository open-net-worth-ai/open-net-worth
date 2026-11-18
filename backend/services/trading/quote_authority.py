"""Per-instrument current-source proof used at the order reservation boundary."""

from backend.core.errors import ConflictError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.services.trading.common import amount, event, identity, instant
from backend.services.trading.market import instrument_key


def record_quote_authority(run_id, instruments, quotes, observations, checked_at, rejected):
    authority = {}
    for instrument, symbol in instruments:
        key = instrument_key(instrument)
        authority[key] = {
            "source": "yahoo",
            "status": "unavailable",
            "checked_at": checked_at,
            "currency": quotes.get("currencies", {}).get(symbol),
            "reason": rejected.get(symbol, "No verified current quote was returned"),
        }
    for row in observations:
        if row.get("source_version") != "yahoo-quote-currency-and-time-verified":
            continue
        key = instrument_key(row["instrument"])
        authority[key] = {
            "source": "yahoo",
            "status": "verified",
            "checked_at": checked_at,
            "currency": row["instrument"]["currency"],
            "price": row["price"],
            "timestamp": row["timestamp"],
            "observation_id": identity(key, row["timestamp"]),
        }
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        TradingStore("runs").update_by_id(run_id, {"market_quote_authority": authority}, conn=conn)
        event(conn, run, "market_quote_authority", {"instruments": authority})
    return authority


def assert_quote_authority(run, instrument, quote):
    if not quote_is_authorized(run, instrument, quote):
        raise ConflictError(
            "This instrument has no verified current-source authority matching the latest quote's currency, time and price"
        )


def quote_is_authorized(run, instrument, quote):
    mandate = run["workspace_snapshot"]["mandate"]
    if run["mode"] == "backtest" or mandate.get("market_data_source", "supplied") != "yahoo":
        return True
    proof = run.get("market_quote_authority", {}).get(instrument_key(instrument), {})
    if proof.get("status") != "verified" or proof.get("currency") != instrument["currency"]:
        return False
    return instant(proof["timestamp"]) == instant(quote["timestamp"]) and amount(
        proof["price"]
    ) == amount(quote["price"])
