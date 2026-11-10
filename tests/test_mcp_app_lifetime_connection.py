"""Regression tests for the app-lifetime external-MCP stdio connection pool (ADR
``2026-08-22-external-mcp-stdio-connections-persist-for-the-app-lifetime``).

**The history**: per-call spawn (broke Playwright MCP, whose open browser page lives on the
client session) -> per-turn pooling (fixed Playwright, but a real trading server,
interactive-brokers-mcp, needs its authenticated gateway session to survive ACROSS chat
messages, not just within one) -> per-app-process pooling (this file): one live child PER
SERVER, established eagerly at boot or lazily on first use, shared by every turn and every
conversation, closed only on death/config-change/process-shutdown.

Hermetic — no real network, no real ``npx``: a trivial inline Python stub stands in for the
external server (mirrors ``tests/test_mcp_stdio_client.py``'s own stub), spawned as a REAL
subprocess so a PID comparison genuinely proves process identity, not merely Python object
identity.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
import textwrap
import time

import pytest

from backend.ai.tools import _confirm_gate as gate
from backend.core.tool_use_context import set_tool_use_id
from backend.db.mcp_client_store import McpClientStore
from backend.db.pending_plan_store import PendingPlanStore
from backend.mcp.client_manager import McpClientManager
from backend.models.mcp_client import McpServerConfig, mcp_connector_id
from backend.services import tool_permissions


def _redeem_and_apply(mgr, tool_use_id, name):
    """Mirrors ``ai.assistant.resolve_pending_tool_use``'s real confirm->apply path: pop the
    staged entry by tool_use_id (never re-derive args from a caller), then fire it."""
    entry = gate.redeem(tool_use_id, name)
    assert entry is not None
    return mgr.apply_confirmed(name, gate.staged_args(entry))


# A stub MCP server that answers `get_pid` with its OWN process id (proves whether two calls
# talked to the same child or two different ones) and `get_pid_slow`, the identical thing but
# after an artificial delay — the fixture the concurrency test uses to prove calls to one
# shared connection are actually serialized, not corrupted by two overlapping RPCs.
_PID_STUB_SERVER = textwrap.dedent(
    """
    import json, os, sys, time

    def reply(rid, result):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        m, rid = req.get("method"), req.get("id")
        if m == "initialize":
            reply(rid, {"protocolVersion": "2024-11-05", "capabilities": {}})
        elif m == "tools/list":
            reply(rid, {"tools": [
                {"name": "get_pid", "description": "pid", "inputSchema": {"type": "object"}},
                {"name": "get_pid_slow", "description": "pid, slowly", "inputSchema": {"type": "object"}},
            ]})
        elif m == "tools/call":
            tool = req["params"]["name"]
            if tool == "get_pid":
                payload = json.dumps({"pid": os.getpid()})
                reply(rid, {"content": [{"type": "text", "text": payload}], "isError": False})
            elif tool == "get_pid_slow":
                time.sleep(0.15)
                payload = json.dumps({"pid": os.getpid()})
                reply(rid, {"content": [{"type": "text", "text": payload}], "isError": False})
            else:
                reply(rid, {"content": [{"type": "text", "text": "unknown tool"}], "isError": True})
        else:
            reply(rid, {})
    """
)


@pytest.fixture
def stub_path(tmp_path):
    p = tmp_path / "pid_stub_server.py"
    p.write_text(_PID_STUB_SERVER, encoding="utf-8")
    return p


# A stub whose `initialize` is deliberately slow (simulates a cold npx/uvx package install) —
# used to prove the pool's first spawn+initialize gets the LONG connect budget
# (STDIO_CONNECT_TIMEOUT_SECONDS) and not the short steady-state one (security review H2a).
_SLOW_INIT_STUB_SERVER = textwrap.dedent(
    """
    import json, sys, time

    def reply(rid, result):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        m, rid = req.get("method"), req.get("id")
        if m == "initialize":
            time.sleep(0.3)
            reply(rid, {"protocolVersion": "2024-11-05", "capabilities": {}})
        elif m == "tools/list":
            reply(rid, {"tools": [
                {"name": "ping", "description": "ping", "inputSchema": {"type": "object"}},
            ]})
        elif m == "tools/call":
            reply(rid, {"content": [{"type": "text", "text": "pong"}], "isError": False})
        else:
            reply(rid, {})
    """
)

# A stub that answers `echo_env` with the ACTUAL value of `API_KEY` it sees in its own
# environment — used to prove a rotated stdio credential (`env`) evicts the pooled child on
# its very next call, even with no explicit reconcile (security review H3).
_ENV_AWARE_STUB_SERVER = textwrap.dedent(
    """
    import json, os, sys

    def reply(rid, result):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        m, rid = req.get("method"), req.get("id")
        if m == "initialize":
            reply(rid, {"protocolVersion": "2024-11-05", "capabilities": {}})
        elif m == "tools/list":
            reply(rid, {"tools": [
                {"name": "echo_env", "description": "echo", "inputSchema": {"type": "object"}},
            ]})
        elif m == "tools/call":
            payload = json.dumps({"api_key": os.environ.get("API_KEY", "<unset>")})
            reply(rid, {"content": [{"type": "text", "text": payload}], "isError": False})
        else:
            reply(rid, {})
    """
)


@pytest.fixture
def store():
    # mcp_clients is DB-backed (Wave 5) — conftest's autouse Tier-2 table clear isolates
    # it per test with no path monkeypatch needed (mirrors test_mcp_stdio_client.py).
    return McpClientStore()


@pytest.fixture
def manager(store):
    pending = PendingPlanStore()
    return McpClientManager(store=store, pending=pending), store


def _cfg(stub_path, **over) -> McpServerConfig:
    base = dict(
        id="local",
        name="Local",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_path)],
        enabled=True,
    )
    base.update(over)
    return McpServerConfig(**base)


def _allow(server_id: str, tool: str) -> None:
    """Grant ``always_allow`` — the ONLY way an external call fires with no confirm card."""
    tool_permissions.set_tool_permission(mcp_connector_id(server_id), tool, "always_allow")


def _pid_of(fenced_text: str) -> int:
    match = re.search(r'\{\s*"pid"\s*:\s*\d+\s*\}', fenced_text)
    assert match, f"no pid payload found in: {fenced_text!r}"
    return json.loads(match.group(0))["pid"]


def _assert_dead(pid: int) -> None:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)
    pytest.fail(f"child pid {pid} is still alive")


# ── (a) every caller shares ONE connection per server — no turn/session scoping ─────────


def test_two_calls_reuse_the_same_child_process(manager, stub_path):
    """The core new behavior: nothing about a second call — from what would have been a
    SECOND turn, or a SECOND conversation, under the old per-turn design — spawns a new
    child. There is no turn/session concept in the manager any more; the pool key is
    server_id alone."""
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid"}], "ok", None)
    _allow("local", "get_pid")

    async def _run():
        text1, _, _images = await mgr.call_external_tool("mcp__local__get_pid", {})
        text2, _, _images = await mgr.call_external_tool("mcp__local__get_pid", {})
        return text1, text2

    text1, text2 = asyncio.run(_run())
    assert _pid_of(text1) == _pid_of(text2)


def test_two_different_servers_get_independent_connections(manager, stub_path):
    mgr, s = manager
    s.create(_cfg(stub_path, id="a"))
    s.create(_cfg(stub_path, id="b"))
    s.record_discovery("a", [{"name": "get_pid"}], "ok", None)
    s.record_discovery("b", [{"name": "get_pid"}], "ok", None)
    _allow("a", "get_pid")
    _allow("b", "get_pid")

    async def _run():
        text_a, _, _images = await mgr.call_external_tool("mcp__a__get_pid", {})
        text_b, _, _images = await mgr.call_external_tool("mcp__b__get_pid", {})
        return text_a, text_b

    text_a, text_b = asyncio.run(_run())
    assert _pid_of(text_a) != _pid_of(text_b)


# ── (b) boot eager-connect ───────────────────────────────────────────────────────────────


def test_connect_enabled_establishes_a_pooled_connection_reused_by_a_later_call(manager, stub_path):
    mgr, s = manager
    s.create(_cfg(stub_path))
    _allow("local", "get_pid")

    mgr.connect_enabled()  # boot-time eager connect — no chat turn has run yet

    assert "local" in mgr._server_conns
    boot_pid = mgr._server_conns["local"]._session._proc.pid
    # discovery landed on the SAME connection, not a redundant second spawn
    row = s.find("local")
    assert row.last_status == "ok"
    assert {t["name"] for t in row.discovered_tools} == {"get_pid", "get_pid_slow"}

    text, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
    assert _pid_of(text) == boot_pid  # the REAL chat call adopted the pre-warmed connection


def test_connect_enabled_never_raises_and_does_not_block_on_one_bad_server(manager, stub_path):
    """A server that can't even spawn (bad command) must not stop a good server's connect,
    and must not raise out of connect_enabled at all — the boot-time contract."""
    mgr, s = manager
    s.create(_cfg(stub_path, id="good"))
    s.create(
        _cfg(
            stub_path,
            id="bad",
            command="/no/such/executable-xyz",
            args=[],
        )
    )

    mgr.connect_enabled()  # must not raise

    assert "good" in mgr._server_conns
    good_row = s.find("good")
    assert good_row.last_status == "ok"
    bad_row = s.find("bad")
    assert bad_row.last_status in ("connect_error", "error")
    assert "bad" not in mgr._server_conns


# ── (c) concurrent callers against ONE shared connection are serialized, not corrupted ──


def test_concurrent_calls_to_the_same_server_are_serialized(manager, stub_path):
    """Two different sessions' turns run genuinely concurrently in this app (no
    cross-session lock in ai/turns.py) and would now hit the SAME shared connection.
    StdioMcpClientConnection's transport cannot safely handle two overlapping RPCs, so the
    manager's per-server lock must serialize them: total wall time for N concurrent slow
    calls should be roughly N times one call's delay (serialized), not one call's delay
    (parallel/corrupted), and every reply must carry the correct payload for the SAME
    process the whole time."""
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid_slow"}], "ok", None)
    _allow("local", "get_pid_slow")

    async def _run():
        start = time.monotonic()
        results = await asyncio.gather(
            *[mgr.call_external_tool("mcp__local__get_pid_slow", {}) for _ in range(3)]
        )
        elapsed = time.monotonic() - start
        return results, elapsed

    results, elapsed = asyncio.run(_run())
    pids = {_pid_of(text) for text, _, _images in results}
    assert len(pids) == 1  # one shared connection, never a race spawning a second one
    # 3 calls * 0.15s each, serialized, is ~0.45s; genuinely parallel would be ~0.15s.
    assert elapsed >= 0.4, f"calls did not appear to be serialized (elapsed={elapsed:.3f}s)"


# ── (d) a dead connection is detected and cleanly reconnected on the next call ──────────


def test_dead_connection_is_detected_and_reconnected_on_next_call(manager, stub_path):
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid"}], "ok", None)
    _allow("local", "get_pid")

    text1, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
    pid1 = _pid_of(text1)

    os.kill(pid1, signal.SIGKILL)
    conn = mgr._server_conns["local"]
    for _ in range(50):
        if not conn.is_alive():
            break
        time.sleep(0.05)
    assert not conn.is_alive(), "child did not die within the poll window"

    # The pool is NOT silently broken forever — the very next call succeeds on a fresh child.
    text2, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
    pid2 = _pid_of(text2)
    assert pid2 != pid1


# ── (e) config change: evict, and reconnect only if still enabled+stdio ─────────────────


def test_reconcile_pooled_connection_evicts_and_reconnects_on_config_change(manager, stub_path):
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid"}], "ok", None)
    _allow("local", "get_pid")

    text1, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
    pid1 = _pid_of(text1)

    mgr.reconcile_pooled_connection("local")

    assert "local" in mgr._server_conns  # still enabled+stdio -> reconnected immediately
    new_pid = mgr._server_conns["local"]._session._proc.pid
    assert new_pid != pid1
    _assert_dead(pid1)  # the OLD child is actually gone, not merely forgotten


def test_reconcile_pooled_connection_on_disabled_server_only_evicts(manager, stub_path):
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid"}], "ok", None)
    _allow("local", "get_pid")
    asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
    assert "local" in mgr._server_conns

    s.update("local", {"enabled": False})
    mgr.reconcile_pooled_connection("local")

    assert "local" not in mgr._server_conns


def test_reconcile_pooled_connection_on_deleted_server_only_evicts(manager, stub_path):
    """Delete: the row is already gone by the time reconcile runs — mirrors
    services.connector_catalog.remove_mcp_server's own call ordering."""
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid"}], "ok", None)
    _allow("local", "get_pid")
    asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
    assert "local" in mgr._server_conns

    s.delete("local")
    mgr.reconcile_pooled_connection("local")  # must not raise on an unknown server

    assert "local" not in mgr._server_conns


# ── (f) process shutdown closes everything ───────────────────────────────────────────────


def test_close_all_pooled_connections_closes_every_server(manager, stub_path):
    mgr, s = manager
    s.create(_cfg(stub_path, id="a"))
    s.create(_cfg(stub_path, id="b"))
    s.record_discovery("a", [{"name": "get_pid"}], "ok", None)
    s.record_discovery("b", [{"name": "get_pid"}], "ok", None)
    _allow("a", "get_pid")
    _allow("b", "get_pid")

    text_a, _, _images = asyncio.run(mgr.call_external_tool("mcp__a__get_pid", {}))
    text_b, _, _images = asyncio.run(mgr.call_external_tool("mcp__b__get_pid", {}))
    pid_a, pid_b = _pid_of(text_a), _pid_of(text_b)

    mgr.close_all_pooled_connections()

    assert mgr._server_conns == {}
    _assert_dead(pid_a)
    _assert_dead(pid_b)


# ── (g) the confirm-gate/permission check still fires independently per call ────────────


def test_permission_gate_still_runs_independently_per_call_on_a_shared_connection(
    manager, stub_path
):
    """``needs_approval`` (the default) means EVERY call proposes and writes nothing until
    confirmed — sharing the underlying transport connection across every caller must NOT let
    a second call to an already-approved tool skip that check. The permission decision
    happens in ``call_external_tool`` BEFORE ``_fire`` (where connection sharing lives) is
    ever reached, so this is really asserting dispatch order, made concrete across what would
    have been TWO SEPARATE TURNS under the old design: two proposals, two distinct staged
    tool_use_ids, two independent ``apply_confirmed`` calls, both landing on the SAME
    underlying shared child (proving sharing happened) without either bypassing its own
    gate."""
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid"}], "ok", None)
    # deliberately no _allow() — stays at the needs_approval default
    name = "mcp__local__get_pid"

    async def _run():
        set_tool_use_id("tu_gate_1")
        text1, proposed1, _images = await mgr.call_external_tool(name, {})
        assert proposed1 is not None  # parked — NOTHING ran yet
        assert text1 is None  # paused: no result until the card is answered
        applied1, after1, _images = await _redeem_and_apply(mgr, "tu_gate_1", name)
        assert after1 is None

        set_tool_use_id("tu_gate_2")
        text2, proposed2, _images = await mgr.call_external_tool(name, {})
        # the SECOND call is gated exactly as independently as the first — a fresh
        # tool_use_id, staged and redeemed on its own
        assert proposed2 is not None
        applied2, _, _images = await _redeem_and_apply(mgr, "tu_gate_2", name)
        set_tool_use_id(None)
        return applied1, applied2

    applied1, applied2 = asyncio.run(_run())
    # both calls actually ran (via apply_confirmed) and shared one child process
    assert _pid_of(applied1) == _pid_of(applied2)


# ── (h) the REST/service wiring, not just the manager method in isolation ───────────────


def test_remove_mcp_server_evicts_the_process_wide_pooled_connection(store, stub_path, monkeypatch):
    """``services.connector_catalog.remove_mcp_server`` — the real ``DELETE /api/mcp-clients/{id}``
    path — reaches the process-wide manager singleton and evicts its pooled connection, not
    just ``McpClientManager.reconcile_pooled_connection`` in isolation (covered above)."""
    import backend.mcp.client_manager as client_manager_module
    from backend.services import connector_catalog

    monkeypatch.setattr(client_manager_module, "_manager", None)
    mgr = client_manager_module.get_manager()
    try:
        store.create(_cfg(stub_path))
        store.record_discovery("local", [{"name": "get_pid"}], "ok", None)
        _allow("local", "get_pid")

        text, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
        pid = _pid_of(text)
        assert "local" in mgr._server_conns

        connector_catalog.remove_mcp_server("local")

        assert "local" not in mgr._server_conns
        _assert_dead(pid)
    finally:
        mgr.close_all_pooled_connections()
        monkeypatch.setattr(client_manager_module, "_manager", None)


# ── (i) H2a: the connection's first spawn gets the long budget, calls after get the short one ──


def test_the_first_spawn_gets_the_long_connect_budget_not_the_short_steady_state_one(
    manager, tmp_path, monkeypatch
):
    """Security review H2a: STDIO_CALL_TIMEOUT_SECONDS was reverted from a blanket 3600s back
    to a short steady-state bound, but the connection's very first spawn+initialize (where a
    cold npx/uvx install genuinely takes tens of seconds) must NOT share that short bound —
    it gets its own, separate STDIO_CONNECT_TIMEOUT_SECONDS instead. Proven here by shrinking
    the steady-state budget to something a 0.3s-slow `initialize` would blow through if the
    two budgets were ever accidentally merged back into one, while the connect budget stays
    generous."""
    import backend.mcp.client_manager as cm
    import backend.mcp.stdio_client as sc

    monkeypatch.setattr(sc, "STDIO_CALL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cm, "STDIO_CONNECT_TIMEOUT_SECONDS", 2.0)
    script = tmp_path / "slow_init.py"
    script.write_text(_SLOW_INIT_STUB_SERVER, encoding="utf-8")
    mgr, s = manager
    s.create(_cfg(script))
    s.record_discovery("local", [{"name": "ping"}], "ok", None)
    _allow("local", "ping")

    text, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__ping", {}))
    assert "pong" in text


# ── (j) H2b: a lock a caller can't acquire promptly fails fast, never queues unboundedly ──


def test_a_second_caller_gets_a_busy_error_instead_of_queuing_unboundedly(
    manager, stub_path, monkeypatch
):
    """Security review H2b: lock acquisition for an ordinary call is now BOUNDED
    (`lock.acquire(timeout=...)`) instead of an unbounded `with lock:`. A caller that can't
    get the per-server lock within that short window gets a clean, retryable tool-error
    instead of queuing behind whatever is currently using the connection for as long as THAT
    call takes (up to STDIO_CALL_TIMEOUT_SECONDS on its own)."""
    import backend.mcp.client_manager as cm

    monkeypatch.setattr(cm, "_CALL_LOCK_ACQUIRE_TIMEOUT_SECONDS", 0.05)
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid_slow"}], "ok", None)
    _allow("local", "get_pid_slow")

    async def _run():
        return await asyncio.gather(
            *[mgr.call_external_tool("mcp__local__get_pid_slow", {}) for _ in range(2)]
        )

    results = asyncio.run(_run())
    texts = [text for text, _, _images in results]
    busy = [t for t in texts if "busy" in t.lower()]
    ok = [t for t in texts if "busy" not in t.lower()]
    assert len(busy) == 1, f"expected exactly one caller to be turned away busy: {texts}"
    assert len(ok) == 1
    assert '"pid"' in ok[0]


# ── (k) H2c: an admin action (disable/delete/shutdown) always wins over a wedged call ──


def test_reconcile_force_evicts_a_wedged_lock_and_kills_the_child(manager, stub_path, monkeypatch):
    """Security review H2c: `reconcile_pooled_connection` must never itself hang behind a call
    that is genuinely stuck holding the per-server lock — the operator's only remedy (disable/
    delete) has to win. Simulates "stuck" by holding the lock directly from this thread (the
    call itself is irrelevant here — the point is the ADMIN path's own bound), then proves
    reconcile both returns promptly AND actually kills the pooled child despite never getting
    the lock the polite way."""
    import backend.mcp.client_manager as cm

    monkeypatch.setattr(cm, "_ADMIN_LOCK_ACQUIRE_TIMEOUT_SECONDS", 0.2)
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid"}], "ok", None)
    _allow("local", "get_pid")

    text, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
    pid = _pid_of(text)
    assert "local" in mgr._server_conns

    # Disabled so reconcile's post-evict reconnect attempt is skipped — isolates the eviction
    # bound this test is about (mirrors test_reconcile_pooled_connection_on_disabled_server_
    # only_evicts's own shape) rather than also exercising the separate reconnect-after-evict
    # wait, which is intentionally best-effort/unbounded (see client_manager's docstring).
    s.update("local", {"enabled": False})

    lock = mgr._rpc_lock_for("local")
    lock.acquire()  # simulate a call that is genuinely stuck and still holding the lock
    try:
        start = time.monotonic()
        mgr.reconcile_pooled_connection("local")
        elapsed = time.monotonic() - start
    finally:
        lock.release()

    assert elapsed < 1.0, f"reconcile waited too long for a held lock ({elapsed:.2f}s)"
    assert "local" not in mgr._server_conns
    _assert_dead(pid)  # force-killed even though the lock was never released to us


def test_close_all_force_evicts_when_a_lock_is_held(manager, stub_path, monkeypatch):
    """Security review H2c, the shutdown path: `close_all_pooled_connections` must not hang
    the whole backend teardown behind one wedged server's lock either."""
    import backend.mcp.client_manager as cm

    monkeypatch.setattr(cm, "_ADMIN_LOCK_ACQUIRE_TIMEOUT_SECONDS", 0.2)
    mgr, s = manager
    s.create(_cfg(stub_path))
    s.record_discovery("local", [{"name": "get_pid"}], "ok", None)
    _allow("local", "get_pid")
    text, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__get_pid", {}))
    pid = _pid_of(text)

    lock = mgr._rpc_lock_for("local")
    lock.acquire()
    try:
        start = time.monotonic()
        mgr.close_all_pooled_connections()
        elapsed = time.monotonic() - start
    finally:
        lock.release()

    assert elapsed < 1.0, f"shutdown waited too long for a held lock ({elapsed:.2f}s)"
    assert mgr._server_conns == {}
    _assert_dead(pid)


# ── (l) H3: a rotated stdio credential (env) can never keep serving calls after a config save ──


def test_env_rotation_is_picked_up_by_the_next_call_without_an_explicit_reconcile(
    manager, tmp_path
):
    """Security review H3: `env` is exactly where a stdio server's own credentials live. The
    pool's connect-or-reuse path (`_get_or_spawn_connection_locked`) now compares the CURRENT
    config's spawn signature — (command, args, env) — against the pooled child's own, and
    evicts+respawns on a mismatch. Proven here with NO call to `reconcile_pooled_connection`
    at all, to isolate the structural backstop from the API-layer proactive trigger (covered
    separately in tests/test_mcp_client_api.py)."""
    script = tmp_path / "env_aware.py"
    script.write_text(_ENV_AWARE_STUB_SERVER, encoding="utf-8")
    mgr, s = manager
    s.create(_cfg(script, env={"API_KEY": "old-key"}))
    s.record_discovery("local", [{"name": "echo_env"}], "ok", None)
    _allow("local", "echo_env")

    text1, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__echo_env", {}))
    assert "old-key" in text1
    old_pid = mgr._server_conns["local"]._session._proc.pid

    s.update("local", {"env": {"API_KEY": "new-key"}})  # rotated — nothing reconciled it

    text2, _, _images = asyncio.run(mgr.call_external_tool("mcp__local__echo_env", {}))
    assert "new-key" in text2
    assert "old-key" not in text2
    new_pid = mgr._server_conns["local"]._session._proc.pid
    assert new_pid != old_pid
    _assert_dead(old_pid)
