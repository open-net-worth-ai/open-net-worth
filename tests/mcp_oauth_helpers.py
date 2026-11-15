"""Shared setup for the inbound MCP OAuth server tests (flag ``mcp_oauth``)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import backend.core.config as config

PUBLIC = "https://wealth.example.com"
MCP_URL = PUBLIC + "/mcp"
PASSWORD = "correct horse battery staple"
API_TOKEN = "tok-static-api-123"
CLAUDE_CLIENT = "https://claude.ai/oauth/mcp-client-metadata"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def seed_auth(*, password: str = PASSWORD, trust_loopback: bool = False) -> None:
    from backend.core.security import hash_password
    from backend.db.auth_store import AuthStore

    AuthStore().save(
        {
            "password_hash": hash_password(password) if password else "",
            "session_signing_key": "key-xyz",
            "api_token": API_TOKEN,
            "trust_loopback": trust_loopback,
            "auth_disabled": False,
        }
    )


def reset_env(monkeypatch, **env) -> None:
    for key in (
        "AUTH_DISABLED",
        "AUTH_TRUST_LOOPBACK",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
        "ONW_PUBLIC_URL",
        "DEMO_MODE",
        "ONW_GATEWAY_TOKEN",
        "ONW_DESKTOP_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config._settings = None


def claude_doc(**overrides) -> dict:
    doc = {
        "client_id": CLAUDE_CLIENT,
        "client_name": "Totally Claude",
        "redirect_uris": [CLAUDE_CALLBACK],
        "token_endpoint_auth_method": "none",
    }
    doc.update(overrides)
    return doc


def build_client() -> TestClient:
    """A small app with the real gate: ``require_auth`` as the app-wide dependency, the /mcp
    router, both OAuth routers, and one ordinary /api route."""
    from backend.api._deps import require_auth
    from backend.api.mcp_oauth_server import router as oauth_router
    from backend.api.mcp_oauth_settings import router as settings_router
    from backend.mcp.server import router as mcp_router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(mcp_router)
    app.include_router(oauth_router)
    app.include_router(settings_router)

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    return TestClient(app, follow_redirects=False)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_params(challenge: str, **overrides) -> dict:
    params = {
        "response_type": "code",
        "client_id": CLAUDE_CLIENT,
        "redirect_uri": CLAUDE_CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "read write",
        "state": "st-123",
        "resource": MCP_URL,
    }
    params.update(overrides)
    return {k: v for k, v in params.items() if v is not None}


def query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def start(client: TestClient, challenge: str, **overrides) -> str:
    res = client.get("/oauth/authorize", params=authorize_params(challenge, **overrides))
    assert res.status_code == 302, res.text
    location = res.headers["location"]
    assert location.startswith(PUBLIC + "/oauth/consent?")
    return query(location)["request"]


def approve(client: TestClient, request_id: str, *, write: bool = False, password: str = PASSWORD):
    return client.post(
        "/api/oauth/consent",
        json={
            "request": request_id,
            "decision": "allow",
            "password": password,
            "allow_write": write,
        },
    )


def connect(client: TestClient, *, write: bool = False) -> dict:
    """The whole browser + token flow → the token response body."""
    verifier, challenge = pkce()
    request_id = start(client, challenge)
    res = approve(client, request_id, write=write)
    assert res.status_code == 200, res.text
    code = query(res.json()["redirect"])["code"]
    tok = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": CLAUDE_CLIENT,
            "redirect_uri": CLAUDE_CALLBACK,
            "resource": MCP_URL,
        },
    )
    assert tok.status_code == 200, tok.text
    return tok.json()


def refresh(client: TestClient, token: str):
    return client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": token, "client_id": CLAUDE_CLIENT},
    )


def rpc(client: TestClient, token: str, method: str, params: dict | None = None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"})


def shift_clock(monkeypatch, delta: timedelta) -> None:
    """Move "now" for every module of the OAuth service."""
    from backend.services.mcp_oauth import authorize, clients, common, grants, tokens

    real = common.now

    def later():
        return real() + delta

    for module in (authorize, clients, grants, tokens):
        monkeypatch.setattr(module, "now", later)
