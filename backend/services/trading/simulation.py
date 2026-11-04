"""Observation-driven paper/replay matching, with explicit liquidity and slippage."""

from backend.db.trading_store import TradingStore
from backend.services.trading.common import ACTIVE_ORDER_STATES, amount, identity, instant
from backend.services.trading.fills import apply_fill
from backend.services.trading.market import available_observations, instrument_key
from backend.services.trading.quote_authority import quote_is_authorized


def match_orders(conn, run):
    observations = available_observations(run)
    if run["mode"] == "paper":
        latest = {}
        for row in observations:
            key = instrument_key(row["instrument"])
            if key not in latest or instant(row["timestamp"]) > instant(latest[key]["timestamp"]):
                latest[key] = row
        observations = list(latest.values())
    orders = TradingStore("orders").list_scope(run["id"], conn=conn)
    fills = TradingStore("fills").list_scope(run["id"], conn=conn)
    for observation in observations:
        used = sum(
            (
                amount(fill["quantity"])
                for fill in fills
                if fill.get("observation_id") == _observation_id(observation)
            ),
            amount(0),
        )
        for order in orders:
            if not _eligible(run, order, observation):
                continue
            order, consumed = _match(conn, run, order, observation, used)
            used += consumed
            orders = [order if row["id"] == order["id"] else row for row in orders]


def _observation_id(row):
    return identity(instrument_key(row["instrument"]), row["timestamp"])


def _eligible(run, order, observation):
    if order["status"] not in ACTIVE_ORDER_STATES or order["instrument_key"] != instrument_key(
        observation["instrument"]
    ):
        return False
    if not quote_is_authorized(run, observation["instrument"], observation):
        return False
    stamp = instant(observation["available_at"] or observation["timestamp"])
    if run["mode"] == "backtest" and (
        stamp <= instant(order["submitted_at"])
        or instant(observation["timestamp"]) <= instant(order["submitted_at"])
    ):
        return False
    return instant(observation["timestamp"]) > instant(
        order.get("last_match_timestamp", "1970-01-01T00:00:00+00:00")
    )


def _match(conn, run, order, observation, used):
    mandate = run["workspace_snapshot"]["mandate"]
    slip = amount(mandate["slippage_bps"]) / 10000
    price = amount(observation["price"]) * (1 + slip if order["side"] == "buy" else 1 - slip)
    limit = order.get("limit_price")
    if limit is not None and (
        (order["side"] == "buy" and price > amount(limit))
        or (order["side"] == "sell" and price < amount(limit))
    ):
        return order, amount(0)
    quantity = amount(order["quantity"]) - amount(order["filled_quantity"])
    if observation["volume"] is not None:
        quantity = min(
            quantity,
            max(
                amount(0),
                amount(observation["volume"]) * amount(mandate["max_participation"]) - used,
            ),
        )
    account = TradingStore("accounts").required(order["account_id"], conn=conn)
    fee_rate = amount(mandate["fee_bps"]) / 10000
    if order["side"] == "buy":
        others = TradingStore("orders").list_scope(run["id"], conn=conn)
        reserved = sum(
            (
                amount(other["reserved_cash"])
                for other in others
                if other["id"] != order["id"]
                and other["account_id"] == account["id"]
                and other["status"] in ACTIVE_ORDER_STATES
            ),
            amount(0),
        )
        quantity = min(
            quantity, max(amount(0), amount(account["cash"]) - reserved) / (price * (1 + fee_rate))
        )
    if quantity <= 0:
        return order, amount(0)
    raw = {
        "execution_id": identity(order["id"], _observation_id(observation)),
        "quantity": str(quantity),
        "price": str(price),
        "fees": str(quantity * price * fee_rate),
        "timestamp": observation["timestamp"] if run["mode"] == "backtest" else run["current_time"],
        "instrument": order["instrument"],
        "side": order["side"],
        "observation_id": _observation_id(observation),
        "broker_order_id": order["id"],
    }
    order = apply_fill(conn, run, order, raw)
    order = TradingStore("orders").update_by_id(
        order["id"], {"last_match_timestamp": observation["timestamp"]}, conn=conn
    )
    return order, quantity
