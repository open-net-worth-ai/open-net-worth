"""``get_connector_status`` reports EVERY connector the Connectors screen lists — not only the
bank/broker aggregators — so Lucius checks whether email is connected instead of asking.

Each row is ``{id, name, enabled, status}`` plus the redacted ``error`` while a connector is
failing; a connector's config (hosts, addresses, URLs, MCP commands) never reaches the model.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.models.mcp_client import McpServerConfig, mcp_connector_id

_IMAP_HOST = "imap.config-marker.example"
_IMAP_USER = "config-marker-user@example.com"
_IMAP_PASSWORD = "SENTINEL-IMAP-PASSWORD"
_HOOK_URL = "https://hook.config-marker.example/x"
_MCP_COMMAND = "config-marker-cmd"


def _call(**inp) -> dict:
    text, proposed = asyncio.run(execute_tool("get_connector_status", inp))
    assert proposed is None
    return json.loads(text)


@pytest.fixture()
def email_connected(db_isolated):
    from backend.db.email_config_store import EmailConfigStore

    EmailConfigStore().update(
        {
            "enabled": True,
            "imap_host": _IMAP_HOST,
            "address": _IMAP_USER,
            "password": _IMAP_PASSWORD,
        }
    )


@pytest.fixture()
def everything_configured(email_connected):
    from backend.db.channel_config_store import ChannelConfigStore
    from backend.db.mcp_client_store import McpClientStore

    ChannelConfigStore().update(
        {"enabled": True, "url": _HOOK_URL, "secret": "SENTINEL-WEBHOOK-SECRET"}
    )
    McpClientStore().create(
        McpServerConfig(
            id="marker-srv",
            name="Marker server",
            transport="stdio",
            command=_MCP_COMMAND,
            args=["--dsn", "postgresql://u:SENTINEL-MCP-DSN@h/db"],
            enabled=True,
        )
    )


def test_an_enabled_email_is_reported_active(email_connected):
    email = _call(connectors=["email"])["connectors"]["email"]
    assert email == {"id": "email", "name": "Email", "enabled": True, "status": "active"}


def test_omitting_connectors_reports_every_kind(everything_configured):
    rows = _call()["connectors"]
    for cid in ("email", "telegram", "webhook", "plaid", "yahoo_finance", "coingecko"):
        assert cid in rows, cid
    assert mcp_connector_id("marker-srv") in rows
    assert "yodlee" not in rows  # DISABLED_CONNECTORS: neither offered nor accepted
    assert "ibkr" not in rows  # retired IBKR Flex — the same


def test_an_aggregator_row_is_the_same_uniform_shape(db_isolated):
    plaid = _call(connectors=["plaid"])["connectors"]["plaid"]
    assert plaid == {"id": "plaid", "name": "Plaid", "enabled": False, "status": "not_configured"}


def test_a_failing_connector_reports_its_redacted_error(db_isolated):
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update({"enabled": True, "client_id": "c", "secret": "s", "access_token": "t"})
    store.record_status(
        "connect_error", error="GET https://user:SENTINEL-URL-PW@bank.test/x failed"
    )
    plaid = _call(connectors=["plaid"])["connectors"]["plaid"]
    assert plaid["status"] == "error"
    assert "bank.test" in plaid["error"]
    assert "SENTINEL-URL-PW" not in json.dumps(plaid)

    store.update({"enabled": False})
    assert "error" not in _call(connectors=["plaid"])["connectors"]["plaid"]


def test_a_local_mcp_servers_error_text_is_withheld(db_isolated):
    """A stdio server's last error can trace back to the spawned child's own stderr."""
    from backend.db.mcp_client_store import McpClientStore

    store = McpClientStore()
    store.create(
        McpServerConfig(id="local", transport="stdio", command="npx", args=["x"], enabled=True)
    )
    store.record_discovery("local", [], status="connect_error", error="child said SENTINEL-STDERR")
    row = _call(connectors=[mcp_connector_id("local")])["connectors"][mcp_connector_id("local")]
    assert row["status"] == "error"
    assert "error" not in row
    assert "SENTINEL-STDERR" not in json.dumps(row)


def test_a_remote_mcp_servers_error_is_reported_and_sanitized(db_isolated):
    from backend.db.mcp_client_store import McpClientStore

    store = McpClientStore()
    store.create(McpServerConfig(id="remote", url="https://mcp.test", enabled=True))
    store.record_discovery("remote", [], status="connect_error", error="cannot reach the server")
    row = _call(connectors=[mcp_connector_id("remote")])["connectors"][mcp_connector_id("remote")]
    assert row["error"] == "cannot reach the server"


def test_no_capability_config_or_secret_reaches_the_model(everything_configured):
    blob = json.dumps(_call())
    for marker in (
        _IMAP_HOST,
        _IMAP_USER,
        _IMAP_PASSWORD,
        _HOOK_URL,
        "SENTINEL-WEBHOOK-SECRET",
        _MCP_COMMAND,
        "SENTINEL-MCP-DSN",
    ):
        assert marker not in blob, marker


def test_unknown_id_names_it_and_lists_the_known_ones(db_isolated):
    body = _call(connectors=["email", "monzo"])
    assert "monzo" in body["error"]
    assert "email" in body["known_connector_ids"]
    assert "yodlee" not in body["known_connector_ids"]


def test_the_old_providers_param_still_works(db_isolated):
    body = _call(providers=["plaid"])
    assert set(body["connectors"]) == {"plaid"}
