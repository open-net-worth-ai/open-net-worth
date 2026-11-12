# ruff: noqa: F811 — pytest fixtures imported from test_mcp_client, then requested by name
"""A remote (HTTP) MCP server keeps ONE session for the app's lifetime, like a stdio child and
like Codex / Claude Code: boot discovery opens it, every tool call and re-read reuses it.

Regression: a stateful server (a broker's) answered every call "missing Mcp-Session-Id header"
while its card read Connected, because discovery's session was thrown away and each tool call
sent none; the first fix then opened and closed a session PER CALL, losing server-side state.
"""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error

from tests.test_mcp_client import (  # noqa: F401 — fixtures
    _allow,
    _cfg,
    _FakeResp,
    _Headers,
    _rpc_envelope,
    manager,
    store,
)


class _StatefulServer:
    """Issues ``session-N`` on initialize; refuses any other request without a live, initialized
    session (400), and 404s a session it has forgotten — as the Streamable HTTP spec says."""

    def __init__(self, *, tool_error: bool = False):
        self.log: list[tuple] = []
        self.sessions: dict[str, bool] = {}
        self.issued = 0
        self.tool_error = tool_error

    def forget_all(self) -> None:
        self.sessions.clear()

    def __call__(self, request, timeout=None):
        sid = {k.lower(): v for k, v in dict(request.headers).items()}.get("mcp-session-id")
        if request.get_method() == "DELETE":
            self.log.append(("DELETE", sid))
            self.sessions.pop(sid, None)
            return _FakeResp(b"")
        body = json.loads(request.data)
        method = body["method"]
        self.log.append((method, sid))
        if method == "initialize":
            self.issued += 1
            new = f"session-{self.issued}"
            self.sessions[new] = False
            return _FakeResp(
                json.dumps(_rpc_envelope(method, body["id"])).encode(),
                headers=_Headers({"Mcp-Session-Id": new}),
            )
        if sid is None:
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "",
                {},
                io.BytesIO(b'{"error":"missing Mcp-Session-Id header"}'),
            )
        if sid not in self.sessions:
            raise urllib.error.HTTPError(request.full_url, 404, "", {}, io.BytesIO(b""))
        if method == "notifications/initialized":
            self.sessions[sid] = True
            return _FakeResp(b"")
        if method == "tools/call" and self.tool_error:
            result = {"content": [{"type": "text", "text": "order rejected"}], "isError": True}
            return _FakeResp(
                json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
            )
        envelope = _rpc_envelope(
            method, body["id"], tools=[{"name": "search_files"}], call_text="positions"
        )
        return _FakeResp(json.dumps(envelope).encode())


def _setup(manager, monkeypatch, server: _StatefulServer):
    mgr, s = manager
    s.create(_cfg())
    monkeypatch.setattr("backend.mcp.client._urlopen", server)
    _allow("drive", "search_files")
    return mgr, s


def _call(mgr):
    text, _proposed, _images = asyncio.run(mgr.call_external_tool("mcp__drive__search_files", {}))
    return text


def test_boot_opens_the_session_every_call_reuses(manager, monkeypatch):
    server = _StatefulServer()
    mgr, _ = _setup(manager, monkeypatch, server)

    mgr.connect_enabled()
    assert "positions" in _call(mgr)
    assert "positions" in _call(mgr)

    assert server.log == [
        ("initialize", None),
        ("notifications/initialized", "session-1"),
        ("tools/list", "session-1"),
        ("tools/call", "session-1"),
        ("tools/call", "session-1"),
    ]


def test_a_first_call_with_no_boot_opens_the_session_once(manager, monkeypatch):
    server = _StatefulServer()
    mgr, s = _setup(manager, monkeypatch, server)
    s.record_discovery("drive", [{"name": "search_files", "inputSchema": {}}], "ok", None)

    _call(mgr)
    _call(mgr)

    assert [m for m, _ in server.log].count("initialize") == 1
    assert ("tools/call", "session-1") in server.log
    assert ("DELETE", "session-1") not in server.log


def test_a_forgotten_session_is_reopened_and_the_call_retried_once(manager, monkeypatch):
    server = _StatefulServer()
    mgr, _ = _setup(manager, monkeypatch, server)
    mgr.connect_enabled()

    server.forget_all()  # the server restarted
    assert "positions" in _call(mgr)

    assert server.log[-4:] == [
        ("tools/call", "session-1"),  # 404: unknown session, not processed
        ("initialize", None),
        ("notifications/initialized", "session-2"),
        ("tools/call", "session-2"),
    ]


def test_a_tool_error_keeps_the_session(manager, monkeypatch):
    server = _StatefulServer(tool_error=True)
    mgr, _ = _setup(manager, monkeypatch, server)
    mgr.connect_enabled()

    assert "order rejected" in _call(mgr)
    _call(mgr)

    calls = [sid for m, sid in server.log if m == "tools/call"]
    assert calls == ["session-1", "session-1"]
    assert not any(m == "DELETE" for m, _ in server.log)


def test_a_network_failure_keeps_the_session(manager, monkeypatch):
    server = _StatefulServer()
    mgr, _ = _setup(manager, monkeypatch, server)
    mgr.connect_enabled()

    def blip(request, timeout=None):
        raise OSError("connection reset")

    monkeypatch.setattr("backend.mcp.client._urlopen", blip)
    assert "error" in _call(mgr)
    monkeypatch.setattr("backend.mcp.client._urlopen", server)
    _call(mgr)

    assert [sid for m, sid in server.log if m == "tools/call"] == ["session-1"]


def test_a_config_change_ends_the_session_and_the_next_call_opens_a_new_one(manager, monkeypatch):
    server = _StatefulServer()
    mgr, s = _setup(manager, monkeypatch, server)
    mgr.connect_enabled()

    s.update("drive", {"token": "rotated"})
    mgr.reconcile_pooled_connection("drive")
    _call(mgr)

    assert ("DELETE", "session-1") in server.log
    assert ("tools/call", "session-2") in server.log


def test_shutdown_ends_the_session(manager, monkeypatch):
    server = _StatefulServer()
    mgr, _ = _setup(manager, monkeypatch, server)
    mgr.connect_enabled()

    mgr.close_all_pooled_connections()

    assert server.log[-1] == ("DELETE", "session-1")


def test_an_oauth_token_refresh_keeps_the_session(manager, monkeypatch):
    """An access token rotates hourly; the session it authenticates must not end with it."""
    from backend.mcp import client_manager

    server = _StatefulServer()
    mgr, _ = _setup(manager, monkeypatch, server)
    mgr.connect_enabled()

    seen: list = []

    def refreshed(cfg, **_kw):
        fresh = cfg.model_copy(update={"token": cfg.token})
        seen.append(fresh)
        return fresh

    monkeypatch.setattr(client_manager.oauth_flow, "ensure_fresh_token", refreshed)
    _call(mgr)
    _call(mgr)

    assert seen  # the refreshed config reached the call
    assert [m for m, _ in server.log].count("initialize") == 1
