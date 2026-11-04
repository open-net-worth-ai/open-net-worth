"""IBKR connection preflight; autonomous execution awaits a reviewed OAuth schema.

IBKR's standard MCP flow prepares instructions for the client to submit. Autonomous
execution additionally requires a funded AI Partition. Tool names alone establish neither
that authority nor the immutable order/execution identities this workspace requires.
"""

from __future__ import annotations

from collections.abc import Callable

from backend.core.errors import ConflictError

OFFICIAL_ENDPOINT = "https://api.ibkr.com/v1/api/mcp-public"
_READ_TOOLS = frozenset(
    {
        "get_flow_type",
        "get_account_summary",
        "get_account_balances",
        "get_account_positions",
        "get_account_orders",
        "get_order_status",
        "get_account_trades",
    }
)
_SCHEMA_BLOCKER = (
    "IBKR managed execution is unavailable: the authenticated AI Partition tool schemas "
    "and broker receipts for client order identity, lookup, fills and cancellation "
    "have not been verified. Standard IBKR trade instructions require your submission."
)


def is_official_connection(server: dict) -> bool:
    return (
        server.get("transport") == "http"
        and isinstance(server.get("url"), str)
        and server["url"].rstrip("/") == OFFICIAL_ENDPOINT
    )


def connection_preflight(server: dict | None) -> dict:
    """Describe setup without exposing credentials or asserting broker execution authority."""
    status = "schema_review_required"
    message = _SCHEMA_BLOCKER
    if server is None or not is_official_connection(server):
        status = "connection_required"
        message = "Add the official IBKR MCP connector and sign in directly with IBKR."
    elif not server.get("enabled"):
        status = "connection_disabled"
        message = "Enable the official IBKR MCP connector in Connectors."
    else:
        oauth = server.get("oauth") or {}
        if (
            server.get("auth_type") != "oauth"
            or not oauth.get("connected")
            or oauth.get("needs_reauth")
            or server.get("last_status") == "auth_error"
        ):
            status = "oauth_required"
            message = "Sign in directly with IBKR through the connector's OAuth setup."
        elif server.get("last_status") != "ok":
            status = "connection_check_required"
            message = "Test the IBKR connector to discover the tools authorized for this account."
    tools = (server or {}).get("discovered_tools") or []
    advertised = {tool.get("name") for tool in tools if isinstance(tool, dict)} & _READ_TOOLS
    return {
        "provider_id": "ibkr",
        "status": status,
        "execution_available": False,
        "allocation_isolated": False,
        "capabilities": [],
        "advertised_read_tools": sorted(advertised),
        "message": message,
    }


class IbkrMcpAdapter:
    """Fail closed until partition authority and the complete execution schema are reviewed."""

    def __init__(self, connection: Callable[[], dict | None]):
        self.connection = connection

    async def inspect_account(self, account: dict) -> dict:
        preflight = connection_preflight(self.connection())
        raise ConflictError(preflight["message"], provider_id="ibkr", preflight=preflight)

    async def submit(self, account: dict, order: dict) -> dict:
        raise ConflictError(_SCHEMA_BLOCKER, provider_id="ibkr")

    async def cancel(self, account: dict, order: dict) -> dict:
        raise ConflictError(
            "IBKR cancellation is unavailable until its authenticated schema is verified. "
            "Check and cancel outstanding orders in IBKR; revoking access does not cancel them.",
            provider_id="ibkr",
        )

    async def reconcile(self, account: dict, orders: list[dict]) -> dict:
        raise ConflictError(
            "IBKR reconciliation is unavailable until broker order and execution receipts "
            "are verified. Check order status and fills directly in IBKR.",
            provider_id="ibkr",
        )


def register_configured_ibkr_adapters() -> None:
    """Give official connections an actionable refusal, never inferred trading authority."""
    from backend.db.mcp_client_store import McpClientStore
    from backend.services.trading.providers import register_live_adapter

    for server in McpClientStore().public():
        if not is_official_connection(server):
            continue
        server_id = server["id"]

        def connection(sid=server_id):
            return McpClientStore().public_one(sid)

        register_live_adapter("mcp:" + server_id, IbkrMcpAdapter(connection))
