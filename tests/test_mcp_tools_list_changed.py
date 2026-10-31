"""A connected server's tool list is re-read when it changes, not only at boot or on Test.

Stdio: a ``notifications/tools/list_changed`` read off the pooled child triggers a re-list on
that same child after the call that carried it. Every server: a daily scheduler job re-runs
``connect_enabled``. Hermetic — a real Python subprocess stands in for the server.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest

from backend.db.mcp_client_store import McpClientStore
from backend.db.pending_plan_store import PendingPlanStore
from backend.mcp.client_manager import McpClientManager
from backend.models.mcp_client import McpServerConfig, mcp_connector_id
from backend.services import tool_permissions

# `grow` adds a tool and, when the stub was started with `notify`, announces it before replying.
_GROWING_STUB = textwrap.dedent(
    """
    import json, sys

    notify = "notify" in sys.argv[1:]
    tools = ["ping", "grow"]

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        m, rid = req.get("method"), req.get("id")
        if m == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05",
                  "capabilities": {"tools": {"listChanged": True}}}})
        elif m == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": n, "description": n, "inputSchema": {"type": "object"}} for n in tools]}})
        elif m == "tools/call":
            if req["params"]["name"] == "grow" and "fresh_tool" not in tools:
                tools.append("fresh_tool")
                if notify:
                    send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            send({"jsonrpc": "2.0", "id": rid, "result": {
                  "content": [{"type": "text", "text": "ok"}], "isError": False}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
    """
)


@pytest.fixture
def stub_path(tmp_path):
    p = tmp_path / "growing_stub.py"
    p.write_text(_GROWING_STUB, encoding="utf-8")
    return p


@pytest.fixture
def manager():
    store = McpClientStore()
    mgr = McpClientManager(store=store, pending=PendingPlanStore())
    yield mgr, store
    mgr.close_all_pooled_connections()


def _setup(store, stub_path, *extra_args):
    store.create(
        McpServerConfig(
            id="local",
            name="Local",
            transport="stdio",
            command=sys.executable,
            args=[str(stub_path), *extra_args],
            enabled=True,
        )
    )
    tool_permissions.set_tool_permission(mcp_connector_id("local"), "grow", "always_allow")


def _tool_names(store) -> list[str]:
    return [t["name"] for t in store.find("local").discovered_tools]


def test_a_list_changed_notice_re_reads_the_tools(manager, stub_path):
    mgr, store = manager
    _setup(store, stub_path, "notify")
    mgr.connect_enabled()
    assert _tool_names(store) == ["ping", "grow"]

    asyncio.run(mgr.call_external_tool("mcp__local__grow", {}))

    assert _tool_names(store) == ["ping", "grow", "fresh_tool"]


def test_no_notice_leaves_the_cached_list_until_the_next_refresh(manager, stub_path):
    mgr, store = manager
    _setup(store, stub_path)
    mgr.connect_enabled()

    asyncio.run(mgr.call_external_tool("mcp__local__grow", {}))
    assert _tool_names(store) == ["ping", "grow"]

    mgr.connect_enabled()  # what the daily mcp_tools_refresh job runs
    assert _tool_names(store) == ["ping", "grow", "fresh_tool"]


def test_the_daily_refresh_job_re_runs_connect_enabled(monkeypatch):
    from backend import scheduler
    from backend.mcp import client_manager

    calls = []

    class _Fake:
        def connect_enabled(self):
            calls.append(1)

    monkeypatch.setattr(client_manager, "get_manager", lambda: _Fake())
    asyncio.run(scheduler._run_mcp_tools_refresh())
    assert calls == [1]
