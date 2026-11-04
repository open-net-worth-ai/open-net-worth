"""Execution fee units: quote cash and base-asset debits remain separate facts."""

from backend.core.errors import ConflictError
from backend.services.trading.common import amount


def base_fee_quantity(fill):
    if "fee_asset" not in fill:
        if "fee_quantity" in fill or "fee_value_quote" in fill:
            raise ConflictError("Execution fee has no asset identity")
        return amount(0)
    instrument = fill["instrument"]
    parts = instrument["symbol"].split("/")
    if instrument["asset_class"] != "crypto" or len(parts) != 2:
        raise ConflictError("Asset-denominated execution fees require a BASE/QUOTE crypto instrument")
    base, quote = parts
    quantity = amount(fill["fee_quantity"])
    cash_fee, value = amount(fill["fees"]), amount(fill["fee_value_quote"])
    if quantity < 0 or value < 0 or quote != instrument["currency"]:
        raise ConflictError("Invalid execution fee units")
    if fill["fee_asset"] == quote and quantity == cash_fee == value:
        return amount(0)
    if fill["fee_asset"] == base and cash_fee == 0 and value == quantity * amount(fill["price"]):
        return quantity
    raise ConflictError("Unsupported commission asset or inconsistent execution fee valuation")


def book_execution_terms(fill):
    base_fee = base_fee_quantity(fill)
    gross_quantity, price = amount(fill["quantity"]), amount(fill["price"])
    quantity = gross_quantity - base_fee if fill["side"] == "buy" else gross_quantity + base_fee
    if quantity <= 0:
        raise ConflictError("Execution commission consumes the acquired quantity")
    return quantity, gross_quantity * price / quantity, amount(fill["fees"])
