"""IBKR preparation tools never establish managed execution authority."""

import pytest

from backend.core.errors import ConflictError
from backend.services.trading.ibkr_mcp import (
    OFFICIAL_ENDPOINT,
    IbkrMcpAdapter,
    connection_preflight,
    register_configured_ibkr_adapters,
)


def server(**changes):
    return {
        "id": "synthetic-ibkr",
        "transport": "http",
        "url": OFFICIAL_ENDPOINT,
        "enabled": True,
        "auth_type": "oauth",
        "oauth": {"connected": True, "needs_reauth": False},
        "last_status": "ok",
        "discovered_tools": [
            {"name": name}
            for name in (
                "get_flow_type",
                "get_account_positions",
                "get_account_trades",
                "create_order_instruction",
                "submit_order",
                "cancel_order",
            )
        ],
        **changes,
    }


@pytest.mark.parametrize(
    ("connection", "status"),
    [
        (None, "connection_required"),
        (server(enabled=False), "connection_disabled"),
        (server(oauth={"connected": False}), "oauth_required"),
        (server(oauth={"connected": True, "needs_reauth": True}), "oauth_required"),
        (server(auth_type="bearer"), "oauth_required"),
        (server(last_status="auth_error"), "oauth_required"),
        (server(last_status="error"), "connection_check_required"),
        (server(), "schema_review_required"),
    ],
)
def test_preflight_explains_next_connection_step(connection, status):
    result = connection_preflight(connection)
    assert result["status"] == status
    assert result["execution_available"] is False
    assert result["allocation_isolated"] is False
    assert result["capabilities"] == []


def test_advertised_submit_tool_does_not_imply_execution_or_partition_authority():
    result = connection_preflight(server())
    assert result["advertised_read_tools"] == [
        "get_account_positions",
        "get_account_trades",
        "get_flow_type",
    ]
    assert "instructions require your submission" in result["message"]
    assert result["execution_available"] is False


async def test_inspection_returns_safe_preflight_without_fabricating_account_snapshot():
    connection = server(oauth={"connected": False}, token="synthetic-private-value")
    with pytest.raises(ConflictError, match="Sign in directly") as error:
        await IbkrMcpAdapter(lambda: connection).inspect_account({})
    assert error.value.context["preflight"]["status"] == "oauth_required"
    assert "synthetic-private-value" not in str(error.value.context)
    assert "cash" not in error.value.context["preflight"]


@pytest.mark.parametrize("operation", ["submit", "cancel", "reconcile"])
async def test_execution_operations_refuse_without_any_broker_call(operation):
    def never_read_connection():
        raise AssertionError("No transport or connection read is needed to refuse execution")

    adapter = IbkrMcpAdapter(never_read_connection)
    with pytest.raises(ConflictError, match="unavailable"):
        await getattr(adapter, operation)({}, [] if operation == "reconcile" else {})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/v1/api/mcp-public",
        OFFICIAL_ENDPOINT + "?alternate=1",
        "https://api.ibkr.com.example.invalid/v1/api/mcp-public",
        "http://api.ibkr.com/v1/api/mcp-public",
    ],
)
def test_registration_does_not_trust_a_connector_label_or_similar_endpoint(monkeypatch, url):
    import backend.db.mcp_client_store as store
    import backend.services.trading.providers as providers

    monkeypatch.setattr(store.McpClientStore, "public", lambda self: [server(url=url)])
    registered = []
    monkeypatch.setattr(providers, "register_live_adapter", lambda *args: registered.append(args))
    register_configured_ibkr_adapters()
    assert registered == []


async def test_registered_adapter_rereads_public_connection_state(monkeypatch):
    import backend.db.mcp_client_store as store
    import backend.services.trading.providers as providers

    connection = server()
    monkeypatch.setattr(store.McpClientStore, "public", lambda self: [connection])
    monkeypatch.setattr(store.McpClientStore, "public_one", lambda self, sid: connection)
    registered = {}
    monkeypatch.setattr(providers, "register_live_adapter", registered.__setitem__)
    register_configured_ibkr_adapters()
    connection["enabled"] = False
    with pytest.raises(ConflictError, match="Enable the official"):
        await registered["mcp:synthetic-ibkr"].inspect_account({})
