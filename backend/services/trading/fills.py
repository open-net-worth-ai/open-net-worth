"""Idempotent fill application, shared by simulation and live reconciliation."""

from backend.core.errors import ConflictError, ValidationError
from backend.db.trading_store import TradingStore
from backend.services.trading.common import amount, event, identity, instant
from backend.services.trading.market import instrument_key


def apply_fill(conn, run, order, raw):
    if not isinstance(raw.get("execution_id"), str) or not raw["execution_id"]:
        raise ValidationError("Broker fill needs a stable execution identity")
    account = TradingStore("accounts").required(order["account_id"], conn=conn)
    fill_id = identity(
        account.get("provider_id") or account["venue"],
        account.get("broker_account_id") or account["id"],
        str(raw["execution_id"]),
    )
    existing = TradingStore("fills").find_by_id(fill_id, conn=conn)
    if existing:
        if existing["order_id"] != order["id"]:
            raise ConflictError("Execution identity already belongs to another order")
        for name in ("quantity", "price", "fees"):
            if amount(existing[name]) != amount(raw.get(name, 0)):
                raise ConflictError("Execution correction requires explicit reconciliation")
        return order
    quantity, price, fees = (
        amount(raw["quantity"]),
        amount(raw["price"]),
        amount(raw.get("fees", 0)),
    )
    remaining = amount(order["quantity"]) - amount(order["filled_quantity"])
    if quantity <= 0 or quantity > remaining or price <= 0 or fees < 0:
        raise ValidationError("Invalid fill quantity, price or fee")
    if instrument_key(raw["instrument"]) != order["instrument_key"] or raw["side"] != order["side"]:
        raise ConflictError("Broker fill does not match the order instrument and side")
    if instant(raw["timestamp"]) < instant(order["created_at"]) and run["mode"] == "live":
        raise ConflictError("Broker fill predates the order")
    gross = quantity * price
    positions = dict(account["positions"])
    position = dict(
        positions.get(
            order["instrument_key"],
            {"instrument": order["instrument"], "quantity": "0", "cost_basis": "0"},
        )
    )
    held, basis = amount(position["quantity"]), amount(position["cost_basis"])
    cash = amount(account["cash"])
    if order["side"] == "buy":
        if gross + fees > cash:
            raise ConflictError("Fill exceeds account cash; reconciliation required")
        position.update(quantity=str(held + quantity), cost_basis=str(basis + gross + fees))
        cash -= gross + fees
    else:
        if quantity > held or fees > gross:
            raise ConflictError("Fill exceeds held quantity or proceeds")
        position.update(
            quantity=str(held - quantity), cost_basis=str(basis * (held - quantity) / held)
        )
        cash += gross - fees
    positions[order["instrument_key"]] = position
    TradingStore("accounts").update_by_id(
        account["id"], {"cash": str(cash), "positions": positions}, conn=conn
    )
    filled = amount(order["filled_quantity"]) + quantity
    fraction = (amount(order["quantity"]) - filled) / amount(order["quantity"])
    order = TradingStore("orders").update_by_id(
        order["id"],
        {
            "filled_quantity": str(filled),
            "status": "filled" if not fraction else "partially_filled",
            "reserved_cash": str(amount(order["initial_reserved_cash"]) * fraction),
            "reserved_quantity": str(amount(order["initial_reserved_quantity"]) * fraction),
        },
        conn=conn,
    )
    fill = {
        **raw,
        "id": fill_id,
        "scope_id": run["id"],
        "run_id": run["id"],
        "account_id": account["id"],
        "order_id": order["id"],
        "quantity": str(quantity),
        "price": str(price),
        "fees": str(fees),
        "book_status": "pending"
        if run["mode"] == "live" and account.get("book_account_id")
        else "not_applicable",
    }
    TradingStore("fills").insert(fill, conn=conn)
    event(
        conn,
        run,
        "order_fill",
        {
            "order_id": order["id"],
            "fill_id": fill_id,
            "quantity": str(quantity),
            "price": str(price),
        },
    )
    return order


def apply_receipt(conn, run, order, receipt):
    allowed = {
        "open",
        "partially_filled",
        "filled",
        "rejected",
        "canceled",
        "expired",
        "cancel_pending",
    }
    if receipt.get("status") not in allowed or not receipt.get("broker_order_id"):
        raise ValidationError("Invalid broker order receipt")
    order = TradingStore("orders").update_by_id(
        order["id"], {"broker_order_id": receipt["broker_order_id"]}, conn=conn
    )
    for raw in receipt.get("fills", []):
        order = apply_fill(conn, run, order, raw)
    status = receipt["status"]
    if status == "filled" and amount(order["filled_quantity"]) != amount(order["quantity"]):
        raise ConflictError("Broker reports filled without complete execution records")
    if order["status"] == "filled":
        status = "filled"
    elif amount(order["filled_quantity"]) > 0 and status == "open":
        status = "partially_filled"
    patch = {"status": status}
    if status in {"filled", "rejected", "canceled", "expired"}:
        patch.update(reserved_cash="0", reserved_quantity="0")
    return TradingStore("orders").update_by_id(order["id"], patch, conn=conn)
