"""Broker-confirmed executions enter the real book only through existing lot chokes."""

from datetime import timedelta

from backend.core.errors import ConflictError
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.trading_store import TradingStore, trading_transaction
from backend.db.transaction_store import TransactionStore
from backend.services import lots, positions
from backend.services.trading.common import event, instant, now
from backend.services.trading.fees import book_execution_terms


def execution_external_ref(fill):
    """Importers must share this exact broker execution identity; price/time matching is unsafe."""
    return {"source": "trading_execution", "ref": fill["id"]}


def has_unresolved_book_fills(run_id, *, conn):
    mapped = {
        a["id"]
        for a in TradingStore("accounts").list_scope(run_id, conn=conn)
        if a.get("book_account_id")
    }
    return any(
        fill["account_id"] in mapped and fill.get("book_status") != "booked"
        for fill in TradingStore("fills").list_scope(run_id, conn=conn)
    )


def project_live_fills(run_id):
    run = TradingStore("runs").required(run_id)
    if run["mode"] != "live":
        raise ConflictError("Simulated executions cannot enter the real book")
    results = []
    for fill in TradingStore("fills").list_scope(run_id):
        account = TradingStore("accounts").required(fill["account_id"])
        if not account.get("book_account_id"):
            continue
        results.append(_project_one(run, account, fill))
    return {"items": results, "total": len(results)}


def _project_one(run, account, fill):
    with trading_transaction() as conn:
        current = TradingStore("fills").required(fill["id"], conn=conn)
        lease = current.get("projection_lease_until")
        if lease and instant(lease) > instant(now()):
            return {"fill_id": fill["id"], "status": "in_progress"}
        TradingStore("fills").update_by_id(
            fill["id"],
            {"projection_lease_until": (instant(now()) + timedelta(seconds=180)).isoformat()},
            conn=conn,
        )
    try:
        result = _book(account, fill)
        if result.get("error"):
            raise ConflictError(result["error"])
        status, reason = "booked", None
    except Exception as exc:
        result, status, reason = {}, "needs_reconciliation", type(exc).__name__
    with trading_transaction() as conn:
        TradingStore("fills").update_by_id(
            fill["id"],
            {
                "book_status": status,
                "book_result": result,
                "book_error": reason,
                "projection_lease_until": None,
            },
            conn=conn,
        )
        event(
            conn,
            run,
            "book_projection",
            {"fill_id": fill["id"], "status": status, "reason": reason},
        )
        if status == "needs_reconciliation":
            TradingStore("runs").update_by_id(
                run["id"], {"state": "needs_reconciliation"}, conn=conn
            )
    return {"fill_id": fill["id"], "status": status, "result": result}


def _book(account, fill):
    if not fill.get("fees_complete", True):
        raise ConflictError("Broker commissions are not yet known")
    book_account = AccountStore().find_by_id(account["book_account_id"])
    if (
        not book_account
        or book_account["currency"] != account["currency"]
        or book_account.get("archived")
    ):
        raise ConflictError("Book account binding is missing, archived or has another currency")
    existing = (
        LotStore().find_by_idempotency_key(fill["id"])
        if fill["side"] == "buy"
        else RealizedEventStore().find_by_idempotency_key(fill["id"])
    )
    ref = execution_external_ref(fill)
    cash = TransactionStore().by_external_ref(ref["source"], ref["ref"])
    if existing:
        if cash is None or existing.get("txn_id") != cash["id"]:
            raise ConflictError("Book fill and cash record disagree after restore")
        return {"idempotent": True, "cash_leg_id": cash["id"]}
    if cash is not None:
        raise ConflictError("Execution cash is already imported; reconcile its lot before booking")
    instrument = _book_instrument(fill["instrument"])
    quantity, price, fees = book_execution_terms(fill)
    change = {
        "idempotency_key": fill["id"],
        "quantity": float(quantity),
        "source": "automation",
        "external_refs": [ref],
        "allow_duplicate": True,
    }
    if fill["side"] == "buy":
        change.update(
            type="acquire",
            kind="buy",
            instrument_id=instrument["id"],
            account_id=book_account["id"],
            unit_cost=float(price),
            cost_currency=account["currency"],
            fees=float(fees),
            acquired_at=fill["timestamp"][:10],
            funding_account_id=book_account["id"],
        )
    else:
        position = positions.find_active_liquid_position(instrument["id"], book_account["id"])
        if position is None:
            raise ConflictError("Sold instrument has no managed book position")
        change.update(
            type="dispose",
            position_id=position["id"],
            method="fifo",
            disposed_at=fill["timestamp"][:10],
            proceeds_per_unit=float(price),
            proceeds_currency=account["currency"],
            disposal_fees=float(fees),
            proceeds_account_id=book_account["id"],
        )
    return lots.apply_lot_change(change)


def _book_instrument(instrument):
    asset = "equity" if instrument["asset_class"] == "stock" else "crypto"
    matches = [
        row
        for row in InstrumentStore().all()
        if row.get("symbol") == instrument["symbol"]
        and row.get("asset_class") == asset
        and row.get("quote_currency") == instrument["currency"]
    ]
    if len(matches) != 1:
        raise ConflictError(
            "Book instrument identity requires a unique existing instrument mapping"
        )
    return matches[0]
