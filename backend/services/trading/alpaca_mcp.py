"""Reviewed Alpaca MCP v2 mapping, behind the shared trading execution contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Awaitable, Callable

from backend.core.errors import ConflictError

JsonCall = Callable[[str, dict], Awaitable[object]]
REQUIRED_TOOLS = frozenset(
    {
        "get_account_info",
        "get_all_positions",
        "get_orders",
        "get_order_by_client_id",
        "get_account_activities_by_type",
        "place_stock_order",
        "place_crypto_order",
        "cancel_order_by_id",
    }
)
_STATES = {
    "new": "open",
    "accepted": "open",
    "pending_new": "open",
    "accepted_for_bidding": "open",
    "pending_cancel": "cancel_pending",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "canceled": "canceled",
    "expired": "canceled",
    "rejected": "rejected",
    "done_for_day": "open",
    "held": "open",
    "stopped": "open",
}


def _object(value: object) -> dict:
    if not isinstance(value, dict) or value.get("error"):
        raise ConflictError("Alpaca returned an invalid structured receipt")
    return value


def _number(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("non-finite")
        return result
    except Exception as exc:
        raise ConflictError("Alpaca returned an invalid monetary value") from exc


def _instrument(raw: dict, venue: str) -> dict:
    asset = "crypto" if raw.get("asset_class") == "crypto" else "stock"
    symbol = str(raw.get("symbol") or "")
    if asset == "crypto" and "/" not in symbol and symbol.endswith("USD"):
        symbol = f"{symbol[:-3]}/USD"
    if not symbol:
        raise ConflictError("Alpaca position lacks an instrument identity")
    return {"symbol": symbol, "venue": venue, "asset_class": asset, "currency": "USD"}


class AlpacaMcpAdapter:
    """Only cash accounts whose entire funded scope is managed are eligible.

    Account isolation additionally requires the run's human authorization, exact starting
    cash and empty holdings, enforced by lifecycle. Broker transport permissions are never
    changed here, and unrecognized schemas/outcomes stop execution for reconciliation.
    """

    def __init__(self, call: JsonCall):
        self.call = call

    async def inspect_account(self, account: dict) -> dict:
        info = _object(await self.call("get_account_info", {}))
        positions = await self.call("get_all_positions", {})
        open_orders = await self.call("get_orders", {"status": "open", "limit": 500})
        if not isinstance(open_orders, list):
            raise ConflictError("Alpaca open-order receipt is not a list")
        if not isinstance(positions, list):
            raise ConflictError("Alpaca positions receipt is not a list")
        if info.get("status") != "ACTIVE" or info.get("trading_blocked"):
            raise ConflictError("Alpaca account is not active for trading")
        if info.get("currency") != "USD" or _number(info.get("multiplier", 0)) != 1:
            raise ConflictError("Managed Alpaca execution requires an unleveraged USD cash account")
        holdings = {}
        for raw in positions:
            row = _object(raw)
            instrument = _instrument(row, account["venue"])
            quantity = _number(row.get("qty"))
            if quantity < 0:
                raise ConflictError("Managed Alpaca account contains a short position")
            key = ":".join(instrument[k] for k in ("venue", "asset_class", "symbol", "currency"))
            holdings[key] = {
                "instrument": instrument,
                "quantity": str(quantity),
                "cost_basis": str(_number(row.get("cost_basis"))),
            }
        return {
            "provider_id": "alpaca",
            "broker_account_id": str(info.get("id") or ""),
            "currency": "USD",
            "cash": str(_number(info.get("cash"))),
            "positions": holdings,
            "open_orders": [
                {
                    "client_order_id": _object(row).get("client_order_id"),
                    "broker_order_id": _object(row).get("id"),
                }
                for row in open_orders
            ],
            "allocation_isolated": True,
            "capabilities": ["client_order_id", "lookup_order", "fills", "cancel"],
        }

    async def submit(self, account: dict, order: dict) -> dict:
        await self._assert_account(account)
        instrument = order["instrument"]
        if instrument["currency"] != "USD":
            raise ConflictError("Alpaca adapter only supports USD-quoted instruments")
        is_crypto = instrument["asset_class"] == "crypto"
        if is_crypto and order["time_in_force"] != "gtc":
            raise ConflictError("Alpaca crypto requires GTC in this trading contract")
        args = {
            "symbol": instrument["symbol"],
            "side": order["side"],
            "qty": str(order["quantity"]),
            "type": order["order_type"],
            "time_in_force": order["time_in_force"],
            "client_order_id": order["id"],
        }
        if order.get("limit_price") is not None:
            args["limit_price"] = str(order["limit_price"])
        tool = "place_crypto_order" if is_crypto else "place_stock_order"
        raw = _object(await self.call(tool, args))
        return await self._receipt(account, order, raw)

    async def cancel(self, account: dict, order: dict) -> dict:
        await self._assert_account(account)
        if not order.get("broker_order_id"):
            raise ConflictError("Reconcile the order identity before cancellation")
        await self.call("cancel_order_by_id", {"order_id": order["broker_order_id"]})
        raw = await self.call("get_order_by_client_id", {"client_order_id": order["id"]})
        return await self._receipt(account, order, _object(raw))

    async def reconcile(self, account: dict, orders: list[dict]) -> dict:
        snapshot = await self.inspect_account(account)
        self._identity(account, snapshot)
        receipts = []
        for order in orders:
            raw = _object(
                await self.call("get_order_by_client_id", {"client_order_id": order["id"]})
            )
            receipts.append(
                {"client_order_id": order["id"], **await self._receipt(account, order, raw)}
            )
        return {"account": snapshot, "orders": receipts}

    async def _assert_account(self, account: dict) -> None:
        self._identity(account, await self.inspect_account(account))

    @staticmethod
    def _identity(account: dict, snapshot: dict) -> None:
        if (
            not account.get("broker_account_id")
            or account["broker_account_id"] != snapshot["broker_account_id"]
        ):
            raise ConflictError("Connected Alpaca account differs from the authorized account")

    async def _receipt(self, account: dict, order: dict, raw: dict) -> dict:
        if raw.get("client_order_id") != order["id"] or not raw.get("id"):
            raise ConflictError("Alpaca order identity does not match the submitted intent")
        if raw.get("symbol", "").replace("/", "") != order["instrument"]["symbol"].replace("/", ""):
            raise ConflictError("Alpaca returned an order for a different instrument")
        if raw.get("side") != order["side"] or _number(raw.get("qty")) != _number(
            order["quantity"]
        ):
            raise ConflictError("Alpaca returned an order with a different side or quantity")
        state = _STATES.get(raw.get("status"))
        if state is None:
            raise ConflictError("Alpaca order state requires manual reconciliation")
        fills = (
            await self._fills(order, str(raw["id"])) if _number(raw.get("filled_qty", 0)) else []
        )
        total = sum((_number(fill["quantity"]) for fill in fills), Decimal(0))
        if total != _number(raw.get("filled_qty", 0)):
            raise ConflictError("Alpaca execution history is incomplete; reconciliation required")
        return {"broker_order_id": str(raw["id"]), "status": state, "fills": fills}

    async def _fills(self, order: dict, broker_id: str) -> list[dict]:
        fills, token = [], None
        for _ in range(100):
            args = {"activity_type": "FILL", "direction": "asc", "page_size": 100}
            if token:
                args["page_token"] = token
            rows = await self.call("get_account_activities_by_type", args)
            if not isinstance(rows, list):
                raise ConflictError("Alpaca execution history is not a list")
            for raw in rows:
                row = _object(raw)
                if row.get("order_id") != broker_id:
                    continue
                if not row.get("id") or not row.get("transaction_time"):
                    raise ConflictError("Alpaca fill lacks execution identity or timestamp")
                fills.append(
                    {
                        "execution_id": str(row["id"]),
                        "broker_order_id": broker_id,
                        "instrument": order["instrument"],
                        "side": order["side"],
                        "quantity": str(_number(row.get("qty"))),
                        "price": str(_number(row.get("price"))),
                        "fees": str(_number(row.get("commission", 0))),
                        "fees_complete": "commission" in row,
                        "timestamp": row["transaction_time"],
                    }
                )
            if len(rows) < 100:
                return fills
            next_token = rows[-1].get("id")
            if not next_token or next_token == token:
                break
            token = next_token
        raise ConflictError("Alpaca execution history exceeds the bounded reconciliation window")


def register_configured_alpaca_adapters() -> None:
    """Register only configured MCPs advertising the complete reviewed v2 toolset."""
    from backend.db.mcp_client_store import McpClientStore
    from backend.mcp.client_manager import get_manager
    from backend.services.trading.providers import register_live_adapter

    for server in McpClientStore().public():
        tools = {item.get("name") for item in server.get("discovered_tools", [])}
        if not server.get("enabled") or not REQUIRED_TOOLS <= tools:
            continue
        server_id = server["id"]
        config = McpClientStore().find(server_id)
        # The official v2 server defaults to paper. A live label requires an explicit
        # live transport setting; never infer it from an account-shaped response.
        if config is None or str(config.env.get("ALPACA_PAPER_TRADE", "true")).lower() != "false":
            continue

        async def call(tool: str, args: dict, sid=server_id):
            current = McpClientStore().find(sid)
            if (
                current is None
                or str(current.env.get("ALPACA_PAPER_TRADE", "true")).lower() != "false"
            ):
                raise ConflictError(
                    "Alpaca transport is no longer explicitly bound to live execution"
                )
            return await get_manager().call_structured_tool(sid, tool, args)

        register_live_adapter("mcp:" + server_id, AlpacaMcpAdapter(call))
