"""Two accounts at one broker = two OAuth sign-ins to the SAME MCP URL ("Add another",
2026-11-19). Run against a FAKE authorization server: discovery, registration and the code
exchange are monkeypatched exactly as in ``test_mcp_oauth_flow``; the DB is real.

What has to hold: each sign-in becomes its own server with its own token; finishing one never
touches the other; and Lucius is offered each one's tools under its own name and namespace."""

from __future__ import annotations

import pytest

from backend.db.mcp_client_store import McpClientStore
from backend.mcp import oauth, oauth_flow, oauth_pending

BASE_URL = "https://onw.example.test:8443"
MCP_URL = "https://mcp.acme-broker.test/v1/mcp"


@pytest.fixture(autouse=True)
def _reset():
    oauth_pending._reset_for_tests()
    yield
    oauth_pending._reset_for_tests()


@pytest.fixture()
def fake_vendor(monkeypatch):
    """A fake broker: one authorization server; each sign-in's code yields its own token, the
    way two different logins would."""
    challenge = oauth.WwwAuthChallenge(resource_metadata_url=None, scope="mcp:tools")
    prm = oauth.ProtectedResourceMetadata(
        authorization_servers=["https://as.acme-broker.test/"],
        resource=MCP_URL,
        scopes_supported=["mcp:tools"],
    )
    as_meta = oauth.AuthServerMetadata(
        issuer="https://as.acme-broker.test/",
        authorization_endpoint="https://as.acme-broker.test/authorize",
        token_endpoint="https://as.acme-broker.test/token",
        registration_endpoint="https://as.acme-broker.test/register",
        revocation_endpoint="https://as.acme-broker.test/revoke",
    )
    reg = oauth.ClientRegistration(
        client_id="dcr-client", client_secret="", token_endpoint_auth_method="none"
    )
    monkeypatch.setattr(oauth, "discover_www_authenticate", lambda url: challenge)
    monkeypatch.setattr(oauth, "fetch_protected_resource_metadata", lambda url, ch: prm)
    monkeypatch.setattr(oauth, "fetch_authorization_server_metadata", lambda a, u: as_meta)
    monkeypatch.setattr(oauth, "register_client", lambda endpoint, url, redirects: reg)

    def exchange(as_meta, client, mcp_url, *, code, code_verifier, redirect_uri, resource):
        return oauth.TokenResponse(
            access_token=f"access-for-{code}",
            refresh_token=f"refresh-for-{code}",
            expires_in=3600,
            scope="mcp:tools",
        )

    monkeypatch.setattr(oauth, "exchange_code_for_token", exchange)


def _sign_in(code: str) -> str:
    begun = oauth_flow.begin(url=MCP_URL, request_base_url=BASE_URL)
    assert begun["ok"] is True, begun
    done = oauth_flow.complete(state=begun["flow_id"], code=code)
    assert done["ok"] is True
    return done["server_id"]


def _finish(store: McpClientStore, server_id: str, name: str) -> None:
    """What the add flow's last click does: name the dormant row and switch it on."""
    store.update(server_id, {"name": name, "enabled": True})


def test_two_sign_ins_to_one_url_are_two_servers_with_their_own_tokens(db_isolated, fake_vendor):
    first = _sign_in("alice-login")
    second = _sign_in("bob-login")

    assert first != second
    assert second == f"{first}-2"
    store = McpClientStore()
    a, b = store.find(first), store.find(second)
    assert a.url == b.url == MCP_URL
    assert a.oauth.access_token == "access-for-alice-login"
    assert b.oauth.access_token == "access-for-bob-login"


def test_finishing_the_second_account_leaves_the_first_alone(db_isolated, fake_vendor):
    store = McpClientStore()
    first = _sign_in("alice-login")
    _finish(store, first, "ACME Broker")
    second = _sign_in("bob-login")
    _finish(store, second, "ACME Broker joint")

    a, b = store.find(first), store.find(second)
    assert (a.name, a.enabled, a.oauth.access_token) == (
        "ACME Broker",
        True,
        "access-for-alice-login",
    )
    assert (b.name, b.enabled, b.oauth.access_token) == (
        "ACME Broker joint",
        True,
        "access-for-bob-login",
    )


def test_lucius_sees_each_accounts_tools_under_its_own_name(db_isolated, fake_vendor):
    from backend.mcp.client_manager import McpClientManager

    store = McpClientStore()
    first = _sign_in("alice-login")
    second = _sign_in("bob-login")
    for server_id, name in ((first, "ACME Broker"), (second, "ACME Broker joint")):
        _finish(store, server_id, name)
        store.record_discovery(
            server_id, [{"name": "get_positions", "description": "Open positions."}], status="ok"
        )

    schemas = {s["name"]: s for s in McpClientManager(store=store).external_tool_schemas()}
    one, two = f"mcp__{first}__get_positions", f"mcp__{second}__get_positions"
    assert {one, two} <= set(schemas)
    assert "ACME Broker" in schemas[one]["description"]
    assert "ACME Broker joint" in schemas[two]["description"]
    assert "ACME Broker joint" not in schemas[one]["description"]
