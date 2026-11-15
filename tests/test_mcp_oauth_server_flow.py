"""Inbound MCP OAuth server (flag ``mcp_oauth``) — the flow end to end and every refusal.

Design: .claude/rfcs/remote-mcp-oauth.md. ADR:
docs/adr/2026-11-16-hosted-ai-apps-connect-to-mcp-through-the-apps-own-oauth-server.md.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import backend.core.config as config
from tests.mcp_oauth_helpers import (
    API_TOKEN,
    CLAUDE_CALLBACK,
    CLAUDE_CLIENT,
    MCP_URL,
    PUBLIC,
    approve,
    authorize_params,
    build_client,
    claude_doc,
    connect,
    pkce,
    query,
    reset_env,
    rpc,
    seed_auth,
    shift_clock,
    start,
)

pytestmark = pytest.mark.flags(mcp_oauth=True)


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch):
    from backend.api import mcp_oauth_server
    from backend.core import ratelimit
    from backend.services.mcp_oauth import clients

    seed_auth()
    reset_env(monkeypatch, ONW_PUBLIC_URL=PUBLIC)
    clients.reset_cache_for_tests()
    monkeypatch.setattr(clients, "_fetch_cimd", lambda client_id: claude_doc())
    ratelimit.login_limiter.clear()
    mcp_oauth_server.token_limiter.clear()
    yield
    config._settings = None


@pytest.fixture
def client():
    return build_client()


# ── discovery ────────────────────────────────────────────────────────────────────


def test_discovery_documents_come_from_public_url_not_the_host_header(client):
    spoof = {"Host": "evil.example", "X-Forwarded-Host": "evil.example"}
    prm = client.get("/.well-known/oauth-protected-resource/mcp", headers=spoof).json()
    assert prm["resource"] == MCP_URL
    assert prm["authorization_servers"] == [PUBLIC]
    asm = client.get("/.well-known/oauth-authorization-server", headers=spoof).json()
    assert asm["issuer"] == PUBLIC
    assert asm["token_endpoint"] == PUBLIC + "/oauth/token"
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["client_id_metadata_document_supported"] is True
    assert "none" in asm["token_endpoint_auth_methods_supported"]
    assert asm["authorization_response_iss_parameter_supported"] is True
    assert "registration_endpoint" not in asm
    assert "evil.example" not in str(prm) + str(asm)


def test_the_401_names_the_protected_resource_metadata(client):
    res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert res.status_code == 401
    header = res.headers["www-authenticate"]
    assert f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/mcp"' in header
    assert 'scope="read write"' in header


def test_the_static_api_token_keeps_full_access(client):
    res = rpc(client, API_TOKEN, "tools/list")
    names = {t["name"] for t in res.json()["result"]["tools"]}
    assert "add_transactions" in names and "get_net_worth" in names


# ── authorize: untrusted client/redirect → error page, never a redirect ─────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "https://evil.example/claude.json"},
        {"client_id": "https://claude.ai.evil.example/x"},
        {"client_id": "http://claude.ai/oauth/x"},
        {"client_id": "https://claude.ai/"},
        {"client_id": "https://claude.ai/a/../b"},
        {"client_id": "some-random-id"},
        {"redirect_uri": "https://evil.example/callback"},
        {"redirect_uri": CLAUDE_CALLBACK + "/extra"},
    ],
)
def test_an_untrusted_client_or_redirect_gets_an_error_page(client, overrides):
    _, challenge = pkce()
    res = client.get("/oauth/authorize", params=authorize_params(challenge, **overrides))
    assert res.status_code == 400
    assert "location" not in res.headers
    assert "text/html" in res.headers["content-type"]


def test_a_metadata_document_naming_another_client_is_refused(client, monkeypatch):
    from backend.services.mcp_oauth import clients

    monkeypatch.setattr(clients, "_fetch_cimd", lambda cid: claude_doc(client_id="https://x"))
    _, challenge = pkce()
    assert client.get("/oauth/authorize", params=authorize_params(challenge)).status_code == 400


def test_a_confidential_metadata_document_is_refused(client, monkeypatch):
    from backend.services.mcp_oauth import clients

    monkeypatch.setattr(
        clients, "_fetch_cimd", lambda cid: claude_doc(token_endpoint_auth_method="private_key_jwt")
    )
    _, challenge = pkce()
    assert client.get("/oauth/authorize", params=authorize_params(challenge)).status_code == 400


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge_method": None}, "invalid_request"),
        ({"code_challenge": "short"}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
        ({"scope": "read admin"}, "invalid_scope"),
    ],
)
def test_other_failures_go_back_to_the_app_with_state_and_iss(client, overrides, error):
    _, challenge = pkce()
    res = client.get("/oauth/authorize", params=authorize_params(challenge, **overrides))
    assert res.status_code == 302
    location = res.headers["location"]
    assert location.startswith(CLAUDE_CALLBACK + "?")
    q = query(location)
    assert q["error"] == error and q["state"] == "st-123" and q["iss"] == PUBLIC


# ── consent ──────────────────────────────────────────────────────────────────────


def test_consent_shows_our_name_for_the_app_not_the_documents(client):
    _, challenge = pkce()
    request_id = start(client, challenge)
    details = client.get("/api/oauth/consent", params={"request": request_id}).json()
    assert details["app_name"] == "Claude"
    assert details["app_host"] == "claude.ai"
    assert details["redirect_host"] == "claude.ai"
    assert details["requested_scopes"] == ["read", "write"]


def test_a_wrong_password_mints_nothing_and_counts_as_a_failed_sign_in(client):
    from backend.core import ratelimit

    _, challenge = pkce()
    request_id = start(client, challenge)
    res = approve(client, request_id, password="wrong")
    assert res.status_code == 401
    assert ratelimit.login_limiter.check("testclient") == (True, 0)
    for _ in range(5):
        approve(client, request_id, password="wrong")
    assert approve(client, request_id).status_code == 429


def test_deny_sends_access_denied_and_spends_the_request(client):
    _, challenge = pkce()
    request_id = start(client, challenge)
    res = client.post("/api/oauth/consent", json={"request": request_id, "decision": "deny"})
    q = query(res.json()["redirect"])
    assert q["error"] == "access_denied" and q["state"] == "st-123" and q["iss"] == PUBLIC
    assert approve(client, request_id).status_code == 404


def test_a_request_is_single_use_and_expires(client, monkeypatch):
    _, challenge = pkce()
    request_id = start(client, challenge)
    assert approve(client, request_id).status_code == 200
    assert approve(client, request_id).status_code == 404
    other = start(client, challenge)
    shift_clock(monkeypatch, timedelta(minutes=11))
    assert approve(client, other).status_code == 404


def test_a_cross_site_approval_is_refused(client):
    _, challenge = pkce()
    request_id = start(client, challenge)
    res = client.post(
        "/api/oauth/consent",
        json={"request": request_id, "decision": "allow", "password": "x"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert res.status_code == 403


def test_approval_redirect_carries_code_state_and_iss(client):
    _, challenge = pkce()
    res = approve(client, start(client, challenge))
    q = query(res.json()["redirect"])
    assert q["code"].startswith("onw_ac_") and q["state"] == "st-123" and q["iss"] == PUBLIC


# ── scopes at /mcp ───────────────────────────────────────────────────────────────


def test_read_only_is_the_default_and_hides_every_write_tool(client):
    from backend.mcp.adapter import tool_allowed

    body = connect(client)
    assert body["scope"] == "read"
    tools = rpc(client, body["access_token"], "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "get_net_worth" in names
    assert "add_transactions" not in names and "run_code" not in names
    assert all(tool_allowed(n, ("read",)) for n in names)
    assert all(t["annotations"]["readOnlyHint"] for t in tools)


def test_a_read_token_calling_a_write_tool_is_refused_without_running_it(client, monkeypatch):
    import backend.mcp.adapter as adapter

    ran = []

    async def fake_execute(name, args):
        ran.append(name)
        return '{"ok": true}', None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)
    token = connect(client)["access_token"]
    res = rpc(client, token, "tools/call", {"name": "delete_transactions", "arguments": {}})
    result = res.json()["result"]
    assert result["isError"] is True and "read-only" in result["content"][0]["text"]
    assert ran == []
    rpc(client, token, "tools/call", {"name": "get_net_worth", "arguments": {}})
    assert ran == ["get_net_worth"]


def test_write_is_granted_only_when_ticked(client):
    body = connect(client, write=True)
    assert body["scope"] == "read write"
    names = {
        t["name"] for t in rpc(client, body["access_token"], "tools/list").json()["result"]["tools"]
    }
    assert "add_transactions" in names


def test_write_is_not_granted_when_the_app_only_asked_to_read(client):
    verifier, challenge = pkce()
    request_id = start(client, challenge, scope="read")
    redirect = approve(client, request_id, write=True).json()["redirect"]
    tok = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": query(redirect)["code"],
            "code_verifier": verifier,
            "client_id": CLAUDE_CLIENT,
            "redirect_uri": CLAUDE_CALLBACK,
        },
    )
    assert tok.json()["scope"] == "read"


def test_an_oauth_token_opens_mcp_and_nothing_else(client):
    token = connect(client)["access_token"]
    assert rpc(client, token, "ping").status_code == 200
    assert client.get("/api/ping", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert (
        client.get("/api/mcp/oauth", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )


def test_an_expired_access_token_is_refused(client, monkeypatch):
    token = connect(client)["access_token"]
    shift_clock(monkeypatch, timedelta(hours=1, seconds=1))
    assert rpc(client, token, "ping").status_code == 401


def test_get_and_delete_mcp_need_a_token_while_oauth_is_on(client):
    assert client.get("/mcp").status_code == 401
    assert client.delete("/mcp").status_code == 401
    token = connect(client)["access_token"]
    assert client.get("/mcp", headers={"Authorization": f"Bearer {token}"}).status_code == 405
