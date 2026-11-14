"""backend/api/mcp_oauth_api.py — REST smoke (RFC 2026-08-14-mcp-oauth §7 "API smoke").

Mirrors tests/test_config_api.py's own pattern for proving an auth-exempt route: a minimal
app gated EXACTLY like main.py (the real `require_auth` dependency + the real exempt set),
with a REMOTE client IP so neither the "unconfigured+loopback" nor the "loopback trust"
allow-rules mask the gate — a remote, unconfigured instance still refuses (require_auth rule
5's "remote -> 401, fail safe"), so a 200 from a REMOTE caller with no credentials is real
proof of exemption, not an artifact of the test harness being loopback.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.db.mcp_client_store import McpClientStore
from backend.mcp import oauth, oauth_pending
from backend.models.mcp_client import McpServerConfig


@pytest.fixture(autouse=True)
def _reset_pending():
    oauth_pending._reset_for_tests()
    yield
    oauth_pending._reset_for_tests()


def _app():
    from backend.api._deps import require_auth
    from backend.api.mcp_client_api import router as mcp_client_router
    from backend.api.mcp_oauth_api import router as mcp_oauth_router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(mcp_client_router)
    app.include_router(mcp_oauth_router)
    return app


def _client(remote: bool = False) -> TestClient:
    app = _app()
    if remote:
        return TestClient(app, client=("203.0.113.7", 9999))
    return TestClient(app)


# `detect_base_url` reads the client's own host by default ("testclient", neither https nor
# loopback) — every `begin()` call below needs a base URL that clears the "reach this
# instance over HTTPS, or from the machine it runs on" gate, same as a real tunnelled/browser
# request would supply via these same headers.
_TUNNEL_HEADERS = {"x-forwarded-proto": "https", "x-forwarded-host": "onw.example.test"}


def _owner_bearer() -> dict:
    """A request that came through a tunnel is not trusted as local, so it signs in, as the
    owner's browser or phone does through one."""
    from backend.db.auth_store import AuthStore

    store = AuthStore()
    return {"authorization": f"Bearer {store.api_token() or store.rotate_api_token()}"}


def _tunnel_headers() -> dict:
    return {**_TUNNEL_HEADERS, **_owner_bearer()}


# ── begin() never 5xx's, even for a hopeless request ────────────────────────────


def test_begin_on_an_unreachable_server_returns_200_ok_false(db_isolated, monkeypatch):
    def fail(mcp_url):
        raise oauth.NotOAuthProtectedError("no 401")

    monkeypatch.setattr(oauth, "discover_www_authenticate", fail)
    res = _client().post(
        "/api/mcp-clients/oauth/begin",
        json={"url": "https://mcp.example.test/mcp"},
        headers=_tunnel_headers(),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "not_oauth"


def test_begin_with_neither_url_nor_server_id_is_a_clean_400_shaped_failure(db_isolated):
    res = _client().post("/api/mcp-clients/oauth/begin", json={})
    assert res.status_code == 200
    assert res.json() == {
        "ok": False,
        "reason": "bad_request",
        "error": "a url or server_id is required",
    }


def test_begin_on_an_unknown_server_id_returns_200_ok_false(db_isolated):
    res = _client().post("/api/mcp-clients/oauth/begin", json={"server_id": "nope"})
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert res.json()["reason"] == "unknown_server"


def test_begin_is_auth_gated(db_isolated):
    res = _client(remote=True).post(
        "/api/mcp-clients/oauth/begin", json={"url": "https://mcp.example.test/mcp"}
    )
    assert res.status_code == 401


# ── poll: never a token, auth-gated ─────────────────────────────────────────────


def test_poll_of_an_unknown_flow_is_a_generic_error_never_a_500(db_isolated):
    res = _client().get("/api/mcp-clients/oauth/never-issued")
    assert res.status_code == 200
    assert res.json()["status"] == "error"


def test_poll_never_returns_a_token(db_isolated):
    state = oauth_pending.stage(
        server_id=None,
        url="https://mcp.example.test/mcp",
        code_verifier="v",
        redirect_uri="https://onw.example/api/mcp-oauth/callback",
        expected_issuer="https://as.example.test/",
        resource="https://mcp.example.test/mcp",
        scope="",
        client=oauth.ClientRegistration(client_id="c1"),
        as_meta=oauth.AuthServerMetadata(
            issuer="i", authorization_endpoint="a", token_endpoint="t"
        ),
    )
    body = _client().get(f"/api/mcp-clients/oauth/{state}").json()
    assert body == {"status": "pending", "error": None}
    assert "access_token" not in body
    assert "code_verifier" not in body


def test_poll_is_auth_gated(db_isolated):
    res = _client(remote=True).get("/api/mcp-clients/oauth/anything")
    assert res.status_code == 401


# ── callback: exempt, GET-only, no oracle, does nothing without a valid state ───


def test_callback_is_reachable_without_a_session(db_isolated):
    res = _client(remote=True).get(
        "/api/mcp-oauth/callback", params={"state": "bogus", "code": "c"}
    )
    assert res.status_code == 200  # exempt — a remote, credential-less caller still gets an answer
    assert "text/html" in res.headers["content-type"]


def test_callback_with_a_bogus_state_is_a_generic_page_no_stack_no_oracle(db_isolated):
    res = _client().get("/api/mcp-oauth/callback", params={"state": "totally-made-up", "code": "c"})
    assert res.status_code == 200
    assert "Traceback" not in res.text
    assert "bogus" not in res.text.lower()
    assert "totally-made-up" not in res.text  # echoes no caller-supplied text


def test_callback_page_is_static_regardless_of_query_params():
    """No caller-, server-, or vendor-supplied string is ever interpolated — the exact same
    bytes come back whichever bogus params are sent."""
    c = _client()
    a = c.get(
        "/api/mcp-oauth/callback", params={"state": "aaa", "error": "<script>evil</script>"}
    ).text
    b = c.get(
        "/api/mcp-oauth/callback", params={"state": "bbb", "iss": "https://attacker.example/"}
    ).text
    assert a == b


def test_callback_does_nothing_without_a_valid_state(db_isolated):
    """A bogus state must not create a server, and must not throw."""
    before = McpClientStore().all()
    _client().get("/api/mcp-oauth/callback", params={"state": "bogus", "code": "c"})
    after = McpClientStore().all()
    assert before == after


def test_callback_completes_a_real_staged_flow(db_isolated, monkeypatch):
    def fake_exchange(as_meta, client, mcp_url, *, code, code_verifier, redirect_uri, resource):
        return oauth.TokenResponse(
            access_token="live-access",
            refresh_token="live-refresh",
            expires_in=3600,
            scope="mcp:tools",
        )

    monkeypatch.setattr(oauth, "exchange_code_for_token", fake_exchange)
    state = oauth_pending.stage(
        server_id=None,
        url="https://mcp.example.test/mcp",
        code_verifier="v",
        redirect_uri="https://onw.example/api/mcp-oauth/callback",
        expected_issuer="https://as.example.test/",
        resource="https://mcp.example.test/mcp",
        scope="mcp:tools",
        client=oauth.ClientRegistration(client_id="c1"),
        as_meta=oauth.AuthServerMetadata(
            issuer="https://as.example.test/", authorization_endpoint="a", token_endpoint="t"
        ),
    )
    res = _client(remote=True).get(
        "/api/mcp-oauth/callback", params={"state": state, "code": "auth-code-1"}
    )
    assert res.status_code == 200

    status = _client().get(f"/api/mcp-clients/oauth/{state}").json()
    assert status == {"status": "ok", "error": None}

    servers = McpClientStore().all()
    assert len(servers) == 1
    assert servers[0].oauth.access_token == "live-access"


def test_callback_only_accepts_get():
    res = _client().post("/api/mcp-oauth/callback", params={"state": "x"})
    assert res.status_code == 405


# ── DELETE attempts revocation, still deletes when it fails ────────────────────


def test_delete_attempts_revocation_and_still_deletes_when_it_fails(db_isolated, monkeypatch):
    from backend.models.mcp_client import McpOAuth

    store = McpClientStore()
    store.create(
        McpServerConfig(
            id="mercury",
            name="Mercury",
            transport="http",
            url="https://mcp.mercury.com/mcp",
            auth_type="oauth",
        )
    )
    store.set_oauth(
        "mercury",
        McpOAuth(
            issuer="i",
            resource="r",
            authorization_endpoint="ae",
            token_endpoint="te",
            revocation_endpoint="https://mcp.mercury.com/revoke",
            client_id="c1",
            access_token="at",
            refresh_token="rt1",
        ),
    )
    revoke_calls = []

    def fail_revoke(*a, **kw):
        revoke_calls.append(1)
        raise oauth.TokenExchangeError("revoke endpoint is down")

    monkeypatch.setattr(oauth, "revoke_token", fail_revoke)
    res = _client().delete("/api/mcp-clients/mercury")
    assert res.status_code == 200
    assert revoke_calls == [1]
    assert store.find("mercury") is None


# ── PATCH that repoints the URL clears oauth ────────────────────────────────────


def test_patch_repointing_url_clears_oauth(db_isolated):
    from backend.models.mcp_client import McpOAuth

    store = McpClientStore()
    store.create(
        McpServerConfig(
            id="mercury",
            name="Mercury",
            transport="http",
            url="https://mcp.mercury.com/mcp",
            auth_type="oauth",
        )
    )
    store.set_oauth(
        "mercury",
        McpOAuth(
            issuer="i",
            resource="r",
            authorization_endpoint="ae",
            token_endpoint="te",
            client_id="c1",
            access_token="at",
            refresh_token="rt1",
        ),
    )
    res = _client().patch(
        "/api/mcp-clients/mercury", json={"url": "https://mcp.mercury.com/mcp-v2"}
    )
    assert res.status_code == 200
    updated = store.find("mercury")
    assert updated.oauth is None
    assert updated.last_status == "auth_error"


def test_patch_not_repointing_url_keeps_oauth(db_isolated):
    from backend.models.mcp_client import McpOAuth

    store = McpClientStore()
    store.create(
        McpServerConfig(
            id="mercury",
            name="Mercury",
            transport="http",
            url="https://mcp.mercury.com/mcp",
            auth_type="oauth",
        )
    )
    store.set_oauth(
        "mercury",
        McpOAuth(
            issuer="i",
            resource="r",
            authorization_endpoint="ae",
            token_endpoint="te",
            client_id="c1",
            access_token="at",
            refresh_token="rt1",
        ),
    )
    res = _client().patch("/api/mcp-clients/mercury", json={"name": "Mercury Bank"})
    assert res.status_code == 200
    updated = store.find("mercury")
    assert updated.oauth is not None
    assert updated.oauth.access_token == "at"
