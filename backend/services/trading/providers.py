"""Normalized live execution contract. Providers never receive authority from an agent."""

from __future__ import annotations

from typing import Protocol

from backend.core.errors import ConflictError


class LiveAdapter(Protocol):
    async def inspect_account(self, account: dict) -> dict:
        """Return account identity, currency, cash, positions, capabilities and isolation proof.

        Required keys: provider_id (adapter-owned canonical identity), broker_account_id, currency, cash, positions (instrument-key ->
        {quantity, cost_basis, instrument}), capabilities (list), allocation_isolated (bool).
        Required capabilities: client_order_id, lookup_order, fills, cancel.
        """

    async def submit(self, account: dict, order: dict) -> dict:
        """Return {broker_order_id,status,fills:[...]}; use order.id as broker client id.

        A fill has execution_id, quantity, price, fees, timestamp, instrument (normalized),
        side, broker_order_id. Status: open/partially_filled/filled/rejected/canceled.
        Timeout/transport errors are raised, never asserted as a rejected order.
        """

    async def cancel(self, account: dict, order: dict) -> dict:
        """Return the same receipt shape; cancellation is not assumed from request success."""

    async def reconcile(self, account: dict, orders: list[dict]) -> dict:
        """Return {account:inspect_account_result,orders:[{client_order_id,...receipt}]}.

        Must query ambiguous submissions by the immutable order.id client identifier.
        """


_adapters: dict[str, LiveAdapter] = {}


def register_live_adapter(connector_id: str, adapter: LiveAdapter | None):
    if adapter is None:
        _adapters.pop(connector_id, None)
    else:
        _adapters[connector_id] = adapter


def get_live_adapter(account: dict) -> LiveAdapter:
    connector_id = account.get("connector_id")
    adapter = _adapters.get(connector_id or "")
    if adapter is None and (connector_id or "").startswith("mcp:"):
        from backend.services.trading.alpaca_mcp import register_configured_alpaca_adapters

        register_configured_alpaca_adapters()
        adapter = _adapters.get(connector_id)
    if adapter is None:
        raise ConflictError(
            "No verified live trading adapter is configured", connector_id=connector_id
        )
    return adapter
