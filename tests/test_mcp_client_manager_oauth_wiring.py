"""client_manager.py calls ensure_fresh_token(server) BEFORE make_connection in both `_fire`
and `_discover` (RFC 2026-08-14-mcp-oauth §4.1/§4.7 "Proactive"). Proven by observing that the
REFRESHED config's access_token — not the stale one on the stored row — is what actually goes
out on the wire, mirroring tests/test_mcp_client.py's own fake-urlopen convention.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
from email.message import Message

import pytest

from backend.db.mcp_client_store import McpClientStore
from backend.mcp import client_manager as client_manager_mod
from backend.models.mcp_client import McpOAuth, McpServerConfig, mcp_connector_id
from backend.services import tool_permissions


class _Headers(dict):
    def __init__(self, mapping=None):
        super().__init__()
        for k, v in (mapping or {}).items():
            self[k.lower()] = v

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
        self._pos = 0
        self.headers = _Headers()

    def read(self, size: int = -1):
        chunk = (
            self._body[self._pos :]
            if size is None or size < 0
            else self._body[self._pos : self._pos + size]
        )
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(captured):
    def fake(request, timeout=None):
        captured.setdefault("auth_headers", []).append(request.headers.get("Authorization"))
        body = json.loads(request.data)
        if "id" not in body:  # notifications/initialized
            return _FakeResp(b"")
        method = body["method"]
        result = (
            {"tools": []}
            if method == "tools/list"
            else (
                {}
                if method == "initialize"
                else {"content": [{"type": "text", "text": "ok"}], "isError": False}
            )
        )
        return _FakeResp(
            json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        )

    return fake


def _oauth_server(access_token: str) -> McpServerConfig:
    return McpServerConfig(
        id="mercury",
        name="Mercury",
        transport="http",
        url="https://mcp.mercury.com/mcp",
        auth_type="oauth",
        enabled=True,
        oauth=McpOAuth(
            issuer="https://mcp.mercury.com/",
            resource="https://mcp.mercury.com/mcp",
            authorization_endpoint="https://mcp.mercury.com/authorize",
            token_endpoint="https://mcp.mercury.com/token",
            client_id="c1",
            access_token=access_token,
            refresh_token="rt-1",
        ),
    )


@pytest.fixture
def manager(db_isolated):
    from backend.db.pending_plan_store import PendingPlanStore
    from backend.mcp.client_manager import McpClientManager

    store = McpClientStore()
    store.create(_oauth_server(access_token="stale-access"))
    tool_permissions.set_tool_permission(mcp_connector_id("mercury"), "list_files", "always_allow")
    return McpClientManager(store=store, pending=PendingPlanStore()), store


def test_fire_uses_the_refreshed_token_not_the_stored_stale_one(manager, monkeypatch):
    mgr, store = manager
    captured = {}
    monkeypatch.setattr("backend.mcp.client._urlopen", _fake_urlopen(captured))

    refresh_calls = []

    def fake_ensure(server, **kw):
        refresh_calls.append(server.id)
        return _oauth_server(access_token="freshly-refreshed")

    monkeypatch.setattr(client_manager_mod.oauth_flow, "ensure_fresh_token", fake_ensure)

    # 3-tuple since the MCP-image lane: (fenced_text, proposed_change, image_attachment_ids).
    text, proposed, _images = asyncio.run(mgr.call_external_tool("mcp__mercury__list_files", {}))
    assert proposed is None
    assert refresh_calls == ["mercury"]
    # initialize + notifications/initialized + tools/call — every one carries the fresh token.
    assert captured["auth_headers"] == ["Bearer freshly-refreshed"] * 3


def test_a_401_on_a_read_asks_for_sign_in_and_keeps_the_tools(manager, monkeypatch):
    mgr, store = manager
    store.record_discovery("mercury", [{"name": "list_files"}], "ok", None)

    def challenge(request, *a, **kw):
        headers = Message()
        headers["WWW-Authenticate"] = (
            'Bearer resource_metadata="https://mcp.mercury.com/.well-known"'
        )
        raise urllib.error.HTTPError(request.full_url, 401, "no", headers, None)

    monkeypatch.setattr("backend.mcp.client._urlopen", challenge)
    monkeypatch.setattr(
        client_manager_mod.oauth_flow, "ensure_fresh_token", lambda server, **kw: server
    )

    mgr.test_connect("mercury")

    server = store.find("mercury")
    assert server.last_status == "auth_error"
    assert [t["name"] for t in server.discovered_tools] == ["list_files"]
    assert store.public_one("mercury")["oauth"]["needs_reauth"] is True
    assert server.last_error.startswith("Connection has expired")


def test_a_401_keeps_the_reason_the_refresh_recorded(manager, monkeypatch):
    mgr, store = manager
    store.record_oauth_status("mercury", "auth_error", "Mercury's app registration was revoked")

    def challenge(request, *a, **kw):
        raise urllib.error.HTTPError(request.full_url, 401, "no", Message(), None)

    monkeypatch.setattr("backend.mcp.client._urlopen", challenge)
    monkeypatch.setattr(
        client_manager_mod.oauth_flow, "ensure_fresh_token", lambda server, **kw: server
    )

    mgr.test_connect("mercury")

    assert store.find("mercury").last_error == "Mercury's app registration was revoked"


def test_discover_uses_the_refreshed_token(manager, monkeypatch):
    mgr, store = manager
    captured = {}
    monkeypatch.setattr("backend.mcp.client._urlopen", _fake_urlopen(captured))

    monkeypatch.setattr(
        client_manager_mod.oauth_flow,
        "ensure_fresh_token",
        lambda server, **kw: _oauth_server(access_token="fresh-for-discovery"),
    )

    mgr.connect_enabled()
    # initialize + notifications/initialized + tools/list.
    assert captured["auth_headers"] == ["Bearer fresh-for-discovery"] * 3
    assert store.find("mercury").last_status == "ok"
