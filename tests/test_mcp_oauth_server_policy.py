"""Inbound MCP OAuth server (flag ``mcp_oauth``) — when it exists, what a read scope may run, and
what revokes a connection. The flag-off path must leave /mcp exactly as it was."""

from __future__ import annotations

import pytest

import backend.core.config as config
from backend.core.feature_flags import override
from tests.mcp_oauth_helpers import (
    API_TOKEN,
    PUBLIC,
    authorize_params,
    build_client,
    claude_doc,
    connect,
    pkce,
    reset_env,
    rpc,
    seed_auth,
)


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch):
    from backend.core import ratelimit
    from backend.services.mcp_oauth import clients

    seed_auth()
    reset_env(monkeypatch, ONW_PUBLIC_URL=PUBLIC)
    clients.reset_cache_for_tests()
    monkeypatch.setattr(clients, "_fetch_cimd", lambda client_id: claude_doc())
    ratelimit.login_limiter.clear()
    yield
    config._settings = None


# ── flag off: today's behaviour, untouched ───────────────────────────────────────


@pytest.mark.flags(mcp_oauth=False)
def test_flag_off_leaves_mcp_exactly_as_it_was():
    client = build_client()
    no_token = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert no_token.status_code == 401
    assert no_token.headers["www-authenticate"] == "Bearer"
    bad = rpc(client, "onw_at_not-a-token", "ping")
    assert bad.status_code == 401 and bad.headers["www-authenticate"] == "Bearer"
    assert rpc(client, API_TOKEN, "ping").status_code == 200
    assert client.get("/mcp", headers={"Authorization": f"Bearer {API_TOKEN}"}).status_code == 405


@pytest.mark.flags(mcp_oauth=False)
def test_flag_off_every_oauth_route_is_absent():
    client = build_client()
    _, challenge = pkce()
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 404
    assert client.get("/.well-known/oauth-authorization-server").status_code == 404
    assert client.get("/oauth/authorize", params=authorize_params(challenge)).status_code == 404
    assert client.post("/oauth/token", data={"grant_type": "x"}).status_code == 404
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    assert client.get("/api/mcp/oauth", headers=headers).status_code == 404


def test_turning_the_flag_off_stops_honouring_tokens_already_issued():
    client = build_client()
    with override(mcp_oauth=True):
        token = connect(client)["access_token"]
        assert rpc(client, token, "ping").status_code == 200
    with override(mcp_oauth=False):
        assert rpc(client, token, "ping").status_code == 401


# ── availability ─────────────────────────────────────────────────────────────────


@pytest.mark.flags(mcp_oauth=True)
@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda mp: seed_auth(password=""), "no_password"),
        (lambda mp: seed_auth(trust_loopback=True), "loopback_trusted"),
        (lambda mp: reset_env(mp), "no_public_url"),
        (
            lambda mp: reset_env(mp, ONW_PUBLIC_URL="http://wealth.example.com"),
            "invalid_public_url",
        ),
        (lambda mp: reset_env(mp, ONW_PUBLIC_URL=PUBLIC, DEMO_MODE="1"), "demo"),
        (lambda mp: reset_env(mp, ONW_PUBLIC_URL=PUBLIC, ONW_GATEWAY_TOKEN="g" * 40), "gateway"),
        (lambda mp: reset_env(mp, ONW_PUBLIC_URL=PUBLIC, ONW_DESKTOP_MODE="1"), "desktop"),
        (lambda mp: reset_env(mp, ONW_PUBLIC_URL=PUBLIC, AUTH_DISABLED="true"), "auth_disabled"),
    ],
)
def test_the_server_refuses_to_exist_unless_every_condition_holds(monkeypatch, setup, reason):
    from backend.services.mcp_oauth import settings

    setup(monkeypatch)
    state = settings.availability()
    assert not state.available and reason in state.reasons


@pytest.mark.flags(mcp_oauth=True)
def test_an_unavailable_server_publishes_nothing():
    seed_auth(trust_loopback=True)
    client = build_client()
    assert client.get("/.well-known/oauth-authorization-server").status_code == 404


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Wealth.Example.com/", "https://wealth.example.com"),
        ("https://wealth.example.com:8443", "https://wealth.example.com:8443"),
        ("https://wealth.example.com:443", "https://wealth.example.com"),
    ],
)
def test_public_url_normalises_to_a_bare_origin(value, expected):
    from backend.services.mcp_oauth.settings import normalize_public_url

    assert normalize_public_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://wealth.example.com",
        "https://wealth.example.com/onw",
        "https://wealth.example.com?x=1",
        "https://user:pw@wealth.example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://192.168.1.10",
        "https://100.64.1.2",
        "wealth.example.com",
    ],
)
def test_public_url_rejects_anything_but_a_public_https_origin(value):
    from backend.core.errors import ValidationError
    from backend.services.mcp_oauth.settings import normalize_public_url

    with pytest.raises(ValidationError):
        normalize_public_url(value)


# ── what a read scope may run ────────────────────────────────────────────────────


def test_read_scope_allows_exactly_the_tools_both_classifiers_call_read_only():
    from backend.ai.tools import TOOL_SCHEMAS
    from backend.ai.tools.registry import may_change_something
    from backend.mcp.adapter import _annotations_for, tool_allowed

    for schema in TOOL_SCHEMAS:
        name = schema["name"]
        expected = _annotations_for(name).get("readOnlyHint") is True and not may_change_something(
            name
        )
        assert tool_allowed(name, ("read",)) is expected, name
        assert tool_allowed(name, ("read", "write")) is True


@pytest.mark.parametrize(
    "name",
    [
        "run_code",
        "run_script",
        "email_send_message",
        "run_screen_command",
        "find_tools",
        "activate_skill",
    ],
)
def test_side_effect_tools_are_never_read(name):
    from backend.mcp.adapter import tool_allowed

    assert tool_allowed(name, ("read",)) is False


def test_a_tool_the_registry_does_not_know_is_never_read():
    from backend.mcp.adapter import tool_allowed

    assert tool_allowed("mcp__broker__place_order", ("read",)) is False
    assert tool_allowed("rest__acme__create_order", ("read",)) is False


def test_external_mcp_tools_are_not_offered_inbound():
    from backend.mcp.adapter import mcp_tool_descriptors

    assert not [d for d in mcp_tool_descriptors() if d["name"].startswith("mcp__")]


# ── what revokes every connection ────────────────────────────────────────────────


@pytest.mark.flags(mcp_oauth=True)
@pytest.mark.parametrize("change", ["set", "replace", "remove"])
def test_any_password_change_revokes_every_connected_app(change):
    from backend.core.security import hash_password
    from backend.db.auth_store import AuthStore
    from backend.services.mcp_oauth import grants

    client = build_client()
    token = connect(client)["access_token"]
    assert len(grants.list_connected_apps()) == 1
    store = AuthStore()
    if change == "set":
        store.set_password_hash(hash_password("new password here"))
    elif change == "replace":
        store.replace_password(hash_password("new password here"))
    else:
        store.remove_password()
    assert grants.list_connected_apps() == []
    assert rpc(client, token, "ping").status_code == 401


@pytest.mark.flags(mcp_oauth=True)
def test_changing_the_public_address_revokes_every_connected_app(monkeypatch):
    from backend.services.mcp_oauth import grants

    client = build_client()
    connect(client)
    reset_env(monkeypatch)  # the address now comes from Settings, not the env
    from backend.db.app_config_store import AppConfigStore

    AppConfigStore().set_mcp_public_url(PUBLIC)
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    res = client.put(
        "/api/mcp/oauth/public-url", json={"public_url": "https://other.example"}, headers=headers
    )
    assert res.status_code == 200 and res.json()["public_url"] == "https://other.example"
    assert grants.list_connected_apps() == []


@pytest.mark.flags(mcp_oauth=True)
def test_settings_refuses_to_change_an_address_the_env_sets():
    client = build_client()
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    res = client.put(
        "/api/mcp/oauth/public-url", json={"public_url": "https://other.example"}, headers=headers
    )
    assert res.status_code == 409


@pytest.mark.flags(mcp_oauth=True)
def test_the_owner_can_revoke_one_app_from_settings():
    client = build_client()
    token = connect(client)["access_token"]
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    status = client.get("/api/mcp/oauth", headers=headers).json()
    assert status["available"] is True and status["mcp_url"] == PUBLIC + "/mcp"
    [app] = status["apps"]
    assert app["name"] == "Claude" and app["scopes"] == ["read"] and app["last_used_at"] is None
    rpc(client, token, "ping")
    grant_id = client.get("/api/mcp/oauth", headers=headers).json()["apps"][0]["grant_id"]
    assert client.delete(f"/api/mcp/oauth/apps/{grant_id}", headers=headers).status_code == 200
    assert rpc(client, token, "ping").status_code == 401


# ── where the data lives ─────────────────────────────────────────────────────────

_TABLES = ("mcp_oauth_clients", "mcp_oauth_pending", "mcp_oauth_grants", "mcp_oauth_tokens")


def test_every_oauth_table_is_secret_non_restorable_and_never_checkpointed():
    from backend.ai.checkpoint._policy import _EXCLUDED_TABLES
    from backend.core.sensitive import NON_RESTORABLE_TABLES, SECRET_TABLES

    for table in _TABLES:
        assert (
            table in SECRET_TABLES and table in NON_RESTORABLE_TABLES and table in _EXCLUDED_TABLES
        )


@pytest.mark.flags(mcp_oauth=True)
def test_tokens_are_stored_only_as_hashes():
    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import mcp_oauth_tokens

    body = connect(build_client())
    with get_engine().connect() as conn:
        stored = str([dict(r) for r in conn.execute(select(mcp_oauth_tokens)).mappings()])
    assert body["access_token"] not in stored and body["refresh_token"] not in stored


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/.well-known/oauth-authorization-server"),
        ("GET", "/.well-known/oauth-protected-resource/mcp"),
        ("GET", "/oauth/authorize"),
        ("POST", "/oauth/token"),
        ("POST", "/oauth/revoke"),
        ("GET", "/api/oauth/consent"),
        ("POST", "/api/oauth/consent"),
        ("GET", "/api/mcp/oauth"),
        ("POST", "/api/mcp/oauth/clients"),
    ],
)
def test_the_public_demo_locks_every_oauth_path(method, path):
    from backend.api.demo_lockdown import is_demo_locked

    assert is_demo_locked(method, path)
