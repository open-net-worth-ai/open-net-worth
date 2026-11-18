"""Inbound MCP OAuth server (flag ``mcp_oauth``) — the token endpoint: code exchange, rotating
refresh, reuse detection, revocation and owner-created client credentials."""

from __future__ import annotations

import base64
from datetime import timedelta

import pytest

import backend.core.config as config
from tests.mcp_oauth_helpers import (
    CLAUDE_CALLBACK,
    CLAUDE_CLIENT,
    PUBLIC,
    approve,
    build_client,
    claude_doc,
    connect,
    pkce,
    query,
    refresh,
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


def _code(client, challenge, **overrides) -> str:
    redirect = approve(client, start(client, challenge, **overrides)).json()["redirect"]
    return query(redirect)["code"]


def _exchange(client, code, verifier, **overrides):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": CLAUDE_CLIENT,
        "redirect_uri": CLAUDE_CALLBACK,
    }
    data.update(overrides)
    return client.post("/oauth/token", data={k: v for k, v in data.items() if v is not None})


# ── code exchange ────────────────────────────────────────────────────────────────


def test_token_responses_are_never_cached(client):
    verifier, challenge = pkce()
    res = _exchange(client, _code(client, challenge), verifier)
    assert res.headers["cache-control"] == "no-store"
    body = res.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600
    assert body["access_token"].startswith("onw_at_") and body["refresh_token"].startswith(
        "onw_rt_"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_verifier": "x" * 50},
        {"code_verifier": None},
        {"redirect_uri": "https://claude.ai/api/mcp/other"},
        {"redirect_uri": None},
        {"resource": "https://other.example/mcp"},
    ],
)
def test_a_code_needs_the_matching_verifier_redirect_and_resource(client, overrides):
    verifier, challenge = pkce()
    res = _exchange(client, _code(client, challenge), verifier, **overrides)
    assert res.status_code == 400
    assert res.json()["error"] in ("invalid_grant", "invalid_target")


def test_a_code_belongs_to_its_client(client):
    verifier, challenge = pkce()
    res = _exchange(
        client,
        _code(client, challenge),
        verifier,
        client_id="https://chatgpt.com/oauth/client.json",
    )
    assert res.status_code == 400 and res.json()["error"] == "invalid_grant"


def test_a_public_client_sending_a_secret_is_refused(client):
    verifier, challenge = pkce()
    res = _exchange(client, _code(client, challenge), verifier, client_secret="guess")
    assert res.status_code == 401 and res.json()["error"] == "invalid_client"


def test_a_code_expires_after_a_minute(client, monkeypatch):
    verifier, challenge = pkce()
    code = _code(client, challenge)
    shift_clock(monkeypatch, timedelta(seconds=61))
    assert _exchange(client, code, verifier).json()["error"] == "invalid_grant"


def test_a_reused_code_revokes_what_it_minted(client):
    verifier, challenge = pkce()
    code = _code(client, challenge)
    first = _exchange(client, code, verifier).json()
    assert rpc(client, first["access_token"], "ping").status_code == 200
    again = _exchange(client, code, verifier)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert rpc(client, first["access_token"], "ping").status_code == 401


def test_a_request_id_is_not_a_code(client):
    verifier, challenge = pkce()
    request_id = start(client, challenge)
    assert _exchange(client, request_id, verifier).json()["error"] == "invalid_grant"


def test_only_form_encoded_token_requests_are_accepted(client):
    res = client.post("/oauth/token", json={"grant_type": "refresh_token"})
    assert res.status_code == 400 and res.json()["error"] == "invalid_request"


def test_an_unknown_grant_type_is_refused(client):
    res = client.post(
        "/oauth/token", data={"grant_type": "client_credentials", "client_id": CLAUDE_CLIENT}
    )
    assert res.json()["error"] == "unsupported_grant_type"


# ── refresh ──────────────────────────────────────────────────────────────────────


def test_refresh_rotates_and_the_new_pair_works(client):
    first = connect(client)
    second = refresh(client, first["refresh_token"])
    assert second.status_code == 200
    body = second.json()
    assert body["refresh_token"] != first["refresh_token"]
    assert rpc(client, body["access_token"], "ping").status_code == 200


def test_a_lost_refresh_response_can_be_retried_once_within_a_minute(client):
    first = connect(client)
    lost = refresh(client, first["refresh_token"]).json()
    retry = refresh(client, first["refresh_token"])
    assert retry.status_code == 200
    assert refresh(client, lost["refresh_token"]).status_code == 400  # the lost one was retired
    assert refresh(client, retry.json()["refresh_token"]).status_code == 200


def test_reusing_a_rotated_refresh_token_after_its_successor_was_used_revokes_the_grant(
    client, monkeypatch
):
    import backend.api.mcp_oauth_server as routes

    notices = []

    async def fake_notify(message):
        notices.append(message)

    monkeypatch.setattr(routes, "_notify", fake_notify)
    first = connect(client)
    second = refresh(client, first["refresh_token"]).json()
    third = refresh(client, second["refresh_token"]).json()
    stolen = refresh(client, first["refresh_token"])
    assert stolen.status_code == 400 and stolen.json()["error"] == "invalid_grant"
    assert rpc(client, third["access_token"], "ping").status_code == 401
    assert refresh(client, third["refresh_token"]).status_code == 400
    assert notices and "used twice" in notices[0]


def test_reuse_after_the_grace_window_revokes_the_grant(client, monkeypatch):
    first = connect(client)
    refresh(client, first["refresh_token"])
    shift_clock(monkeypatch, timedelta(seconds=61))
    assert refresh(client, first["refresh_token"]).status_code == 400
    from backend.services.mcp_oauth import grants

    assert grants.list_connected_apps() == []


def test_a_refresh_cannot_widen_the_scope(client):
    first = connect(client)
    res = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": CLAUDE_CLIENT,
            "scope": "read write",
        },
    )
    assert res.json()["error"] == "invalid_scope"


def test_refresh_stops_at_idle_expiry_and_the_grant_lifetime(client, monkeypatch):
    first = connect(client)
    shift_clock(monkeypatch, timedelta(days=31))
    assert refresh(client, first["refresh_token"]).status_code == 400


def test_a_refresh_token_belongs_to_its_client(client):
    first = connect(client)
    res = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": "https://chatgpt.com/oauth/client.json",
        },
    )
    assert res.status_code == 400


# ── revoke ───────────────────────────────────────────────────────────────────────


def test_revoking_either_token_ends_the_connection(client):
    body = connect(client)
    res = client.post(
        "/oauth/revoke", data={"token": body["refresh_token"], "client_id": CLAUDE_CLIENT}
    )
    assert res.status_code == 200
    assert rpc(client, body["access_token"], "ping").status_code == 401


def test_revoking_an_unknown_token_is_not_an_error(client):
    res = client.post("/oauth/revoke", data={"token": "onw_rt_nope", "client_id": CLAUDE_CLIENT})
    assert res.status_code == 200


def test_failed_token_calls_are_throttled(client):
    for _ in range(30):
        client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": "x", "client_id": CLAUDE_CLIENT},
        )
    res = client.post(
        "/oauth/token", data={"grant_type": "refresh_token", "client_id": CLAUDE_CLIENT}
    )
    assert res.status_code == 429


# ── owner-created credentials ────────────────────────────────────────────────────


def _manual(client, uris=("https://app.example/cb",)):
    res = client.post(
        "/api/mcp/oauth/clients",
        json={"name": "My app", "redirect_uris": list(uris)},
        headers={"Authorization": "Bearer tok-static-api-123"},
    )
    assert res.status_code == 200, res.text
    return res.json()["created"]


def _manual_flow(client, created, redirect, *, auth="post", secret=None):
    verifier, challenge = pkce()
    request_id = start(client, challenge, client_id=created["client_id"], redirect_uri=redirect)
    code = query(approve(client, request_id).json()["redirect"])["code"]
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect,
    }
    headers = {}
    secret = created["client_secret"] if secret is None else secret
    if auth == "basic":
        raw = f"{created['client_id']}:{secret}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    else:
        data.update(client_id=created["client_id"], client_secret=secret)
    return client.post("/oauth/token", data=data, headers=headers)


@pytest.mark.parametrize("auth", ["post", "basic"])
def test_manual_credentials_work_with_either_secret_method(client, auth):
    created = _manual(client)
    res = _manual_flow(client, created, "https://app.example/cb", auth=auth)
    assert res.status_code == 200, res.text


def test_a_wrong_manual_secret_is_refused(client):
    created = _manual(client)
    res = _manual_flow(client, created, "https://app.example/cb", secret="onw_cs_wrong")
    assert res.status_code == 401 and res.json()["error"] == "invalid_client"


def test_a_manual_loopback_redirect_matches_any_port(client):
    created = _manual(client, uris=("http://127.0.0.1/callback",))
    assert _manual_flow(client, created, "http://127.0.0.1:53111/callback").status_code == 200


def test_the_manual_secret_is_shown_once_and_stored_hashed(client):
    from backend.db.mcp_oauth_store import McpOAuthStore

    created = _manual(client)
    row = McpOAuthStore().get_client(created["client_id"])
    assert created["client_secret"] not in str(row)
    listed = client.get(
        "/api/mcp/oauth", headers={"Authorization": "Bearer tok-static-api-123"}
    ).json()
    assert created["client_secret"] not in str(listed)


def test_deleting_credentials_revokes_their_connections(client):
    created = _manual(client)
    token = _manual_flow(client, created, "https://app.example/cb").json()["access_token"]
    res = client.delete(
        f"/api/mcp/oauth/clients/{created['client_id']}",
        headers={"Authorization": "Bearer tok-static-api-123"},
    )
    assert res.status_code == 200
    assert rpc(client, token, "ping").status_code == 401


@pytest.mark.parametrize(
    "uri", ["http://app.example/cb", "ftp://x/y", "https://a.example/cb#frag", "not a url"]
)
def test_manual_redirects_must_be_https_or_loopback(client, uri):
    res = client.post(
        "/api/mcp/oauth/clients",
        json={"name": "x", "redirect_uris": [uri]},
        headers={"Authorization": "Bearer tok-static-api-123"},
    )
    assert res.status_code == 422
