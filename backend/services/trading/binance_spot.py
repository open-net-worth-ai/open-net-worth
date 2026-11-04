"""Binance Spot execution for an exclusively allocated, entirely funded wallet."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from backend.core.errors import ConflictError
from backend.services.trading.binance_rest import number, object_row, rows

JsonCall = Callable[[str, str, dict], Awaitable[object]]
_STATES = {
    "NEW": "open",
    "PENDING_NEW": "open",
    "PARTIALLY_FILLED": "partially_filled",
    "FILLED": "filled",
    "CANCELED": "canceled",
    "PENDING_CANCEL": "cancel_pending",
    "REJECTED": "rejected",
    "EXPIRED": "expired",
    "EXPIRED_IN_MATCH": "expired",
}


class BinanceSpotAdapter:
    def __init__(self, call: JsonCall, config: dict):
        self.call, self.config = call, dict(config)
        self.environment = config["environment"]
        self.venue = "binance-testnet" if self.environment == "testnet" else "binance"

    async def inspect_account(self, account: dict) -> dict:
        if self.config.get("isolated_account") is not True or self.config.get(
            "account_scope"
        ) not in {"dedicated_account", "subaccount"}:
            raise ConflictError("Binance requires an exclusively allocated whole Spot wallet")
        if account["venue"] != self.venue or account["currency"] != self.config["quote_currency"]:
            raise ConflictError(
                "Binance environment, venue or quote currency differs from allocation"
            )
        if self.environment == "testnet" and account.get("book_account_id"):
            raise ConflictError("Binance Spot Testnet executions cannot enter the real book")
        info = object_row(await self.call("GET", "/api/v3/account", {}))
        if (
            info.get("accountType") != "SPOT"
            or info.get("canTrade") is not True
            or set(info.get("permissions", [])) != {"SPOT"}
        ):
            raise ConflictError("Binance requires an active unleveraged Spot-only account")
        uid = str(info.get("uid") or "")
        if not uid or (
            self.config.get("broker_account_id") and uid != self.config["broker_account_id"]
        ):
            raise ConflictError("Binance account identity differs from the configured wallet")
        if account.get("broker_account_id") and uid != account["broker_account_id"]:
            raise ConflictError("Binance account identity differs from the authorized allocation")
        if self.environment == "live":
            await self._permissions()
        balances, cash, free = {}, Decimal(0), Decimal(0)
        for row in rows(info.get("balances")):
            asset = str(row.get("asset") or "")
            available, locked = number(row.get("free")), number(row.get("locked"))
            if not asset or asset in balances:
                raise ConflictError("Binance balance has an invalid or repeated asset identity")
            balances[asset] = available + locked
            if asset == account["currency"]:
                cash, free = available + locked, available
        holdings = {}
        for asset, quantity in balances.items():
            if asset == account["currency"] or not quantity:
                continue
            instrument = await self._instrument(asset, account["currency"])
            key = ":".join(instrument[k] for k in ("venue", "asset_class", "symbol", "currency"))
            holdings[key] = {"instrument": instrument, "quantity": str(quantity)}
        open_orders = rows(await self.call("GET", "/api/v3/openOrders", {}))
        return {
            "provider_id": self.venue,
            "broker_account_id": uid,
            "currency": account["currency"],
            "cash": str(cash),
            "available_cash": str(free),
            "positions": holdings,
            "allocation_isolated": True,
            "capabilities": ["client_order_id", "lookup_order", "fills", "cancel"],
            "environment": self.environment,
            "open_orders": [
                {
                    "client_order_id": row.get("clientOrderId"),
                    "broker_order_id": str(row.get("orderId", "")),
                }
                for row in open_orders
            ],
        }

    async def _permissions(self):
        permission = object_row(await self.call("GET", "/sapi/v1/account/apiRestrictions", {}))
        prohibited = (
            "enableWithdrawals",
            "enableInternalTransfer",
            "enableMargin",
            "enableFutures",
            "permitsUniversalTransfer",
            "enableVanillaOptions",
            "enablePortfolioMarginTrading",
        )
        if (
            permission.get("enableReading") is not True
            or permission.get("enableSpotAndMarginTrading") is not True
            or any(permission.get(key) is not False for key in prohibited)
        ):
            raise ConflictError(
                "Binance API key must allow Spot reads/trades and disable withdrawals, transfers and leverage"
            )

    async def _instrument(self, base, quote):
        data = object_row(await self.call("GET", "/api/v3/exchangeInfo", {"symbol": base + quote}))
        symbols = rows(data.get("symbols"))
        if len(symbols) != 1:
            raise ConflictError("Binance did not establish a unique Spot instrument")
        row = symbols[0]
        if (
            row.get("symbol") != base + quote
            or row.get("baseAsset") != base
            or row.get("quoteAsset") != quote
            or row.get("isSpotTradingAllowed") is not True
            or row.get("status") != "TRADING"
        ):
            raise ConflictError(
                "Binance instrument is not active Spot trading in the allocation currency"
            )
        return {
            "symbol": base + "/" + quote,
            "currency": quote,
            "asset_class": "crypto",
            "venue": self.venue,
        }

    async def _order_symbol(self, account, order):
        instrument = order["instrument"]
        parts = instrument["symbol"].split("/")
        if len(parts) != 2 or instrument["asset_class"] != "crypto":
            raise ConflictError("Binance requires an explicit BASE/QUOTE crypto instrument")
        expected = await self._instrument(*parts)
        if instrument != expected or instrument["currency"] != account["currency"]:
            raise ConflictError("Binance instrument differs from the authorized allocation")
        if order["time_in_force"] != "gtc":
            raise ConflictError("Binance Spot execution supports GTC only")
        return "".join(parts)

    async def submit(self, account, order):
        await self.inspect_account(account)
        symbol = await self._order_symbol(account, order)
        args = {
            "symbol": symbol,
            "side": order["side"].upper(),
            "type": order["order_type"].upper(),
            "quantity": str(order["quantity"]),
            "newClientOrderId": order["id"],
            "newOrderRespType": "RESULT",
        }
        if order["order_type"] == "limit":
            args.update(price=str(order["limit_price"]), timeInForce="GTC")
        raw = object_row(await self.call("POST", "/api/v3/order", args))
        return await self._receipt(order, raw)

    async def cancel(self, account, order):
        await self.inspect_account(account)
        symbol = await self._order_symbol(account, order)
        if not order.get("broker_order_id"):
            raise ConflictError("Reconcile Binance order identity before cancellation")
        args = {
            "symbol": symbol,
            "orderId": order["broker_order_id"],
            "newClientOrderId": "c" + order["id"][1:],
        }
        await self.call("DELETE", "/api/v3/order", args)
        raw = object_row(await self.call("GET", "/api/v3/order", self._lookup(order, symbol)))
        return await self._receipt(order, raw)

    async def reconcile(self, account, orders):
        receipts = []
        await self.inspect_account(account)
        for order in orders:
            symbol = await self._order_symbol(account, order)
            raw = object_row(await self.call("GET", "/api/v3/order", self._lookup(order, symbol)))
            receipts.append({"client_order_id": order["id"], **await self._receipt(order, raw)})
        return {"account": await self.inspect_account(account), "orders": receipts}

    @staticmethod
    def _lookup(order, symbol):
        identity = (
            {"orderId": order["broker_order_id"]}
            if order.get("broker_order_id")
            else {"origClientOrderId": order["id"]}
        )
        return {"symbol": symbol, **identity}

    async def _receipt(self, order, raw):
        symbol = order["instrument"]["symbol"].replace("/", "")
        cancellation = (
            raw.get("status") == "CANCELED"
            and order.get("broker_order_id")
            and raw.get("clientOrderId") == "c" + order["id"][1:]
        )
        if (
            (raw.get("clientOrderId") != order["id"] and not cancellation)
            or not raw.get("orderId")
            or raw.get("symbol") != symbol
            or raw.get("side") != order["side"].upper()
            or number(raw.get("origQty")) != number(order["quantity"])
            or raw.get("type") != order["order_type"].upper()
            or (order.get("broker_order_id") and str(raw["orderId"]) != order["broker_order_id"])
        ):
            raise ConflictError("Binance order receipt differs from the immutable submitted intent")
        if order["order_type"] == "limit" and number(raw.get("price")) != number(
            order["limit_price"]
        ):
            raise ConflictError("Binance limit price differs from the submitted intent")
        status = _STATES.get(raw.get("status"))
        if status is None:
            raise ConflictError("Binance order status requires manual reconciliation")
        fills = (
            await self._fills(order, str(raw["orderId"])) if number(raw.get("executedQty")) else []
        )
        if sum((number(fill["quantity"]) for fill in fills), Decimal(0)) != number(
            raw.get("executedQty")
        ):
            raise ConflictError("Binance execution history is incomplete; reconciliation required")
        return {"broker_order_id": str(raw["orderId"]), "status": status, "fills": fills}

    async def _fills(self, order, broker_id):
        symbol = order["instrument"]["symbol"].replace("/", "")
        fills, seen, cursor = [], set(), None
        for _ in range(100):
            args = {"symbol": symbol, "orderId": broker_id, "limit": 1000}
            if cursor is not None:
                args["fromId"] = cursor
            trades = rows(await self.call("GET", "/api/v3/myTrades", args))
            for row in trades:
                if str(row.get("orderId")) != broker_id or row.get("symbol") != symbol:
                    raise ConflictError("Binance returned a trade outside the requested order")
                trade_id = row.get("id")
                if not isinstance(trade_id, int) or isinstance(trade_id, bool) or trade_id in seen:
                    raise ConflictError(
                        "Binance returned an invalid or repeated execution identity"
                    )
                seen.add(trade_id)
                fills.append(self._fill(order, broker_id, row))
            if len(trades) < 1000:
                return fills
            next_cursor = max(seen) + 1
            if cursor is not None and next_cursor <= cursor:
                break
            cursor = next_cursor
        raise ConflictError("Binance execution history exceeds the bounded reconciliation window")

    @staticmethod
    def _fill(order, broker_id, row):
        base, quote = order["instrument"]["symbol"].split("/")
        fee, price, quantity = (
            number(row.get("commission")),
            number(row.get("price")),
            number(row.get("qty")),
        )
        fee_asset = row.get("commissionAsset")
        if fee_asset not in {base, quote}:
            raise ConflictError("Binance third-asset commission requires explicit reconciliation")
        if row.get("isBuyer") is not (order["side"] == "buy") or not price or not quantity:
            raise ConflictError("Binance trade side, price or quantity differs from the order")
        try:
            timestamp = datetime.fromtimestamp(int(row["time"]) / 1000, timezone.utc).isoformat()
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise ConflictError("Binance execution timestamp is invalid") from exc
        return {
            "execution_id": f"{row['symbol']}:{row['id']}",
            "broker_order_id": broker_id,
            "instrument": order["instrument"],
            "side": order["side"],
            "quantity": str(quantity),
            "price": str(price),
            "fees": str(fee if fee_asset == quote else Decimal(0)),
            "fee_value_quote": str(fee * price if fee_asset == base else fee),
            "fee_asset": fee_asset,
            "fee_quantity": str(fee),
            "fees_complete": True,
            "timestamp": timestamp,
        }
