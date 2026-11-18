"""Average-cost realized results from the same execution units used by fill application."""

from backend.services.trading.common import amount
from backend.services.trading.fees import base_fee_quantity
from backend.services.trading.market import instrument_key, latest_quote


def unrealized_result(run, positions):
    total = amount(0)
    for position in positions.values():
        quantity = amount(position["quantity"])
        if not quantity:
            continue
        quote = latest_quote(run, position["instrument"])
        if quote is None:
            return None
        total += quantity * amount(quote["price"]) - amount(position["cost_basis"])
    return total


def execution_ledger(account, fills):
    positions = {}
    cash, realized, basis_complete = amount(account["capital"]), amount(0), True
    movements = []
    for fill in fills:
        key = instrument_key(fill["instrument"])
        held, basis = positions.get(key, (amount(0), amount(0)))
        quantity, price, fee = amount(fill["quantity"]), amount(fill["price"]), amount(fill["fees"])
        gross, base_fee = quantity * price, base_fee_quantity(fill)
        if fill["side"] == "buy":
            positions[key] = (held + quantity - base_fee, basis + gross + fee)
            movement = -gross - fee
        else:
            if held <= 0 or quantity + base_fee > held:
                basis_complete = False
                disposed_basis = amount(0)
                remaining_basis = basis
            else:
                disposed_basis = basis * (quantity + base_fee) / held
                remaining_basis = basis * (held - quantity - base_fee) / held
            positions[key] = (held - quantity - base_fee, remaining_basis)
            realized += gross - fee - disposed_basis
            movement = gross - fee
        cash += movement
        movements.append(
            {
                "id": fill["id"],
                "timestamp": fill["timestamp"],
                "kind": fill["side"],
                "amount": str(movement),
                "gross": str(gross),
                "cash_fee": str(fee),
                "fee_asset": fill.get("fee_asset"),
                "fee_quantity": fill.get("fee_quantity"),
                "order_id": fill["order_id"],
                "fill_id": fill["id"],
            }
        )
    reconciled = (
        basis_complete
        and cash == amount(account["cash"])
        and all(
            held == amount(account["positions"].get(key, {}).get("quantity", 0))
            and basis == amount(account["positions"].get(key, {}).get("cost_basis", 0))
            for key, (held, basis) in positions.items()
        )
    )
    reconciled = reconciled and all(
        key in positions or amount(position["quantity"]) == 0
        for key, position in account["positions"].items()
    )
    return {
        "realized_pnl": realized if reconciled else None,
        "expected_cash": str(cash),
        "reconciled": reconciled,
        "movements": movements,
    }
