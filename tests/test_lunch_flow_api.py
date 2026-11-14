"""REST smoke for the Lunch Flow endpoints (status / config / oauth begin+poll+callback /
disconnect). Fetching + booking transactions is no longer a REST action (RFC
connectors-as-data-sources) — see tests/test_connector_fetch_tools.py for
lunch_flow_fetch_transactions.

Mirrors ``tests/test_mcp_oauth_api.py``'s own pattern for proving the callback route is
auth-exempt: a minimal app gated EXACTLY like main.py (the real ``require_auth`` dependency +
the real exempt set), with a REMOTE client IP so neither the "unconfigured+loopback" nor the
"loopback trust" allow-rules mask the gate.
"""

from __future__ import annotations

import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.services.aggregation import lunch_flow_pending as pending


@pytest.fixture(autouse=True)
def _reset_pending():
    pending._reset_for_tests()
    yield
    pending._reset_for_tests()


def _app() -> FastAPI:
    from backend.api._deps import require_auth
    from backend.api.lunch_flow_api import router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(router)
    return app


def _client(remote: bool = False) -> TestClient:
    app = _app()
    if remote:
        return TestClient(app, client=("203.0.113.7", 9999))
    return TestClient(app)


# `detect_base_url` reads the client's own host by default ("testclient", neither https nor
# loopback) — every `begin()` call below needs a base URL that clears the "reach this instance
# over HTTPS, or from the machine it runs on" gate, same as a real tunnelled request would.
_TUNNEL_HEADERS = {"x-forwarded-proto": "https", "x-forwarded-host": "onw.example.test"}


def _owner_bearer() -> dict:
    """A request that came through a tunnel is not trusted as local, so it signs in, as the
    owner's browser or phone does through one."""
    from backend.db.auth_store import AuthStore

    store = AuthStore()
    return {"authorization": f"Bearer {store.api_token() or store.rotate_api_token()}"}


def _tunnel_headers() -> dict:
    return {**_TUNNEL_HEADERS, **_owner_bearer()}


@pytest.fixture()
def client(db_isolated):
    return _client()


# ── status / config ──────────────────────────────────────────────────────────────────


def test_status_default_not_configured(client):
    r = client.get("/api/lunch-flow/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["has_credentials"] is False
    assert body["has_link"] is False
    assert body["connections"] == []
    assert "client_secret" not in body
    assert "access_token" not in body
    assert "refresh_token" not in body


def test_put_sets_creds_and_hides_secret(client):
    r = client.put(
        "/api/lunch-flow/config",
        json={
            "client_id": "cid",
            "client_secret": "super-secret",
            "email": "me@example.com",
            "enabled": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_credentials"] is True
    assert body["client_id"] == "cid"
    assert body["email"] == "me@example.com"
    assert body["enabled"] is True
    assert "client_secret" not in body
    assert "super-secret" not in json.dumps(body)


def test_put_blank_secret_keeps_saved(client):
    client.put("/api/lunch-flow/config", json={"client_id": "cid", "client_secret": "keep"})
    r = client.put("/api/lunch-flow/config", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["has_credentials"] is True
    assert body["enabled"] is False


def test_put_cannot_smuggle_an_access_token(client):
    """LunchFlowConfigUpdate has no access_token/refresh_token field at all — only the OAuth
    exchange may ever set them."""
    r = client.put("/api/lunch-flow/config", json={"client_id": "cid", "access_token": "sneaky"})
    assert r.status_code == 200
    assert r.json()["has_link"] is False


# ── oauth begin / poll ──────────────────────────────────────────────────────────────────


def test_begin_requires_credentials(client):
    r = client.post("/api/lunch-flow/oauth/begin", headers=_tunnel_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False


def test_begin_returns_authorize_url(client):
    client.put("/api/lunch-flow/config", json={"client_id": "cid", "client_secret": "sec"})
    r = client.post("/api/lunch-flow/oauth/begin", headers=_tunnel_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["authorize_url"].startswith("https://lunchflow.app/api/platform/oauth/authorize")
    assert "onw.example.test" in body["authorize_url"]
    assert body["flow_id"]


def test_begin_is_auth_gated():
    r = _client(remote=True).post("/api/lunch-flow/oauth/begin", headers=_TUNNEL_HEADERS)
    assert r.status_code == 401


def test_poll_of_an_unknown_flow_is_a_generic_error_never_a_500(client):
    r = client.get("/api/lunch-flow/oauth/never-issued")
    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_poll_never_returns_a_token(client):
    state = pending.stage(redirect_uri="https://onw.example.test/api/lunch-flow/oauth/callback")
    body = client.get(f"/api/lunch-flow/oauth/{state}").json()
    assert body == {"status": "pending", "error": None}
    assert "access_token" not in body


def test_poll_is_auth_gated(db_isolated):
    r = _client(remote=True).get("/api/lunch-flow/oauth/anything")
    assert r.status_code == 401


# ── oauth callback: exempt, GET-only, no oracle ─────────────────────────────────────────


def test_callback_is_reachable_without_a_session(db_isolated):
    r = _client(remote=True).get(
        "/api/lunch-flow/oauth/callback", params={"state": "bogus", "code": "c"}
    )
    assert r.status_code == 200  # exempt — a remote, credential-less caller still gets an answer
    assert "text/html" in r.headers["content-type"]


def test_callback_with_a_bogus_state_is_a_generic_page_no_stack_no_oracle(client):
    r = client.get(
        "/api/lunch-flow/oauth/callback", params={"state": "totally-made-up", "code": "c"}
    )
    assert r.status_code == 200
    assert "Traceback" not in r.text
    assert "totally-made-up" not in r.text  # echoes no caller-supplied text


def test_callback_page_is_static_regardless_of_query_params(client):
    a = client.get(
        "/api/lunch-flow/oauth/callback",
        params={"state": "aaa", "error": "<script>evil</script>"},
    ).text
    b = client.get(
        "/api/lunch-flow/oauth/callback", params={"state": "bbb", "code": "whatever"}
    ).text
    assert a == b


def test_callback_only_accepts_get(client):
    r = client.post("/api/lunch-flow/oauth/callback", params={"state": "x"})
    assert r.status_code == 405


def test_callback_completes_a_real_staged_flow_and_never_returns_a_token(client, monkeypatch):
    client.put("/api/lunch-flow/config", json={"client_id": "cid", "client_secret": "sec"})
    begin = client.post("/api/lunch-flow/oauth/begin", headers=_tunnel_headers()).json()

    from backend.services.aggregation import lunch_flow_oauth as oauth_svc

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "live-access",
                "refresh_token": "live-refresh",
                "expires_in": 3600,
                "user_id": "u1",
            }

    monkeypatch.setattr(oauth_svc.httpx, "post", lambda *a, **k: _Resp())

    r = client.get(
        "/api/lunch-flow/oauth/callback", params={"state": begin["flow_id"], "code": "auth-code-1"}
    )
    assert r.status_code == 200
    assert "live-access" not in r.text

    status = client.get(f"/api/lunch-flow/oauth/{begin['flow_id']}").json()
    assert status == {"status": "ok", "error": None}

    body = client.get("/api/lunch-flow/status").json()
    assert body["has_link"] is True
    assert "live-access" not in json.dumps(body)


# ── disconnect ────────────────────────────────────────────────────────────────────────


def test_disconnect_returns_result(client, monkeypatch):
    from backend.services.aggregation import lunch_flow as sync_svc

    monkeypatch.setattr(
        sync_svc,
        "disconnect_connection",
        lambda connection_id: {"ok": True, "connection_id": connection_id, "accounts_unlinked": 2},
    )
    r = client.delete("/api/lunch-flow/connections/100")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["connection_id"] == 100


def test_disconnect_bad_request_returns_422(client, monkeypatch):
    from backend.core.errors import ValidationError
    from backend.services.aggregation import lunch_flow as sync_svc

    def _raise(connection_id):
        raise ValidationError("Lunch Flow is not linked — connect a bank first.")

    monkeypatch.setattr(sync_svc, "disconnect_connection", _raise)
    r = client.delete("/api/lunch-flow/connections/100")
    assert r.status_code == 422
