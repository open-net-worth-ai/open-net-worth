"""A connected server's tool list is re-read when it changes, not only at boot or on Test.

Stdio: a ``notifications/tools/list_changed`` read off the pooled child triggers a re-list on
that same child after the call that carried it. Every server: a daily scheduler job re-reads
it, and an hourly one retries those whose last read failed — keeping the last good list until
``REFRESH_FAILURES_BEFORE_ERROR`` failures in a row. Hermetic — a real Python subprocess stands in for the server.
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


def test_the_refresh_jobs_call_the_manager(monkeypatch):
    from backend import scheduler
    from backend.mcp import client_manager

    calls = []

    class _Fake:
        def refresh_tools(self, *, only_unhealthy=False):
            calls.append(only_unhealthy)

    monkeypatch.setattr(client_manager, "get_manager", lambda: _Fake())
    asyncio.run(scheduler._run_mcp_tools_refresh())
    asyncio.run(scheduler._run_mcp_tools_refresh(only_unhealthy=True))
    assert calls == [False, True]


# ── a failed scheduled re-read keeps the last good list until it keeps failing ──────────

_GOOD = [{"name": "ping", "description": "", "inputSchema": {"type": "object"}}]


def _unreachable(store, tmp_path, server_id="down"):
    store.create(
        McpServerConfig(
            id=server_id,
            name=server_id,
            transport="stdio",
            command=sys.executable,
            args=[str(tmp_path / "missing_server.py")],
            enabled=True,
        )
    )
    store.record_discovery(server_id, _GOOD, "ok", None)


def test_one_failed_scheduled_read_keeps_the_tools(manager, tmp_path):
    mgr, store = manager
    _unreachable(store, tmp_path)

    mgr.refresh_tools()

    server = store.find("down")
    assert server.last_status == "ok"
    assert [t["name"] for t in server.discovered_tools] == ["ping"]
    assert server.refresh_failures == 1


def test_the_error_shows_after_three_failures_in_a_row(manager, tmp_path):
    from backend.mcp.client_manager import REFRESH_FAILURES_BEFORE_ERROR

    mgr, store = manager
    _unreachable(store, tmp_path)

    mgr.refresh_tools()
    for _ in range(REFRESH_FAILURES_BEFORE_ERROR - 1):
        mgr.refresh_tools(only_unhealthy=True)

    server = store.find("down")
    assert server.last_status != "ok"
    assert server.discovered_tools == []


def test_boot_still_shows_a_failure_at_once(manager, tmp_path):
    mgr, store = manager
    _unreachable(store, tmp_path)

    mgr.connect_enabled()

    server = store.find("down")
    assert server.last_status != "ok"
    assert server.discovered_tools == []


def test_the_hourly_retry_skips_healthy_servers_and_a_success_resets_the_count(
    manager, stub_path, tmp_path
):
    mgr, store = manager
    _setup(store, stub_path)
    _unreachable(store, tmp_path)
    mgr.refresh_tools()
    assert store.find("down").refresh_failures == 1
    store.record_discovery("local", _GOOD, "ok", None)  # healthy: hourly must not touch it

    mgr.refresh_tools(only_unhealthy=True)
    assert _tool_names(store) == ["ping"]  # untouched — a re-read would list ping + grow

    store.update("down", {"args": [str(stub_path)]})  # the server is back
    mgr.refresh_tools(only_unhealthy=True)
    server = store.find("down")
    assert server.refresh_failures == 0
    assert [t["name"] for t in server.discovered_tools] == ["ping", "grow"]
