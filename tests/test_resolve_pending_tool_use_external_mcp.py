"""External-MCP confirm→apply through the REAL generic resolve path (RFC 2026-07-22),
migrated 2026-08-22 off ``apply_external_mcp_call`` + a bespoke nonce store — see ADR
``2026-08-22-external-mcp-confirm-migrates-onto-generic-resolve``.

Drives the REAL turn loop (``assistant.run``/``assistant.resolve_pending_tool_use``), the REAL
confirm gate, and a REAL ``McpClientManager`` registered on the ``core.mcp_external`` seam
(only the LLM provider and the outbound HTTP transport are stubbed — no network) — mirroring
``tests/test_resolve_pending_tool_use.py``'s own philosophy of proving the actual production
code paths, not a mock of them.

Proves the three security properties the deleted nonce mechanism existed for, end-to-end:
  1. Args are authoritative-from-storage — the resolve REQUEST carries only
     ``tool_use_id``/``decision`` (``ResolvePendingRequest``'s real shape), so there is
     structurally no channel for a caller to substitute a different amount on approve.
  2. An unknown/already-used/stale pending entry is refused, not silently ignored.
  3. The target tool's CURRENT permission level is re-checked at resolution time — a tool
     moved to ``blocked`` while its card sat awaiting a click is refused rather than fired.
"""

from __future__ import annotations

import json

import pytest

from backend.ai import assistant
from backend.ai.tools import _confirm_gate as gate
from backend.core import mcp_external
from backend.core.tool_use_context import set_tool_use_id
from backend.db.mcp_client_store import McpClientStore
from backend.mcp.client_manager import McpClientManager
from backend.models.mcp_client import McpServerConfig, mcp_connector_id
from backend.services import tool_permissions


def _cfg(**over) -> McpServerConfig:
    base = dict(
        id="drive",
        name="Drive",
        transport="http",
        url="http://fake/mcp",
        auth_type="bearer",
        token="secret-token-xyz",
        enabled=True,
        read_only=True,
    )
    base.update(over)
    return McpServerConfig(**base)


def _make_fake_urlopen(captured: dict, *, call_text="external result"):
    """A minimal fake ``urlopen`` answering ``tools/call`` in-process — mirrors
    ``tests/test_mcp_client.py``'s own helper, trimmed to what this file needs (no
    initialize/tools/list handshake — the server row is seeded directly via ``record_discovery``,
    never discovered live)."""

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        rid = captured["body"]["id"]
        captured["call_args"] = captured["body"]["params"]
        result = {"content": [{"type": "text", "text": call_text}], "isError": False}
        envelope = {"jsonrpc": "2.0", "id": rid, "result": result}
        return _FakeResp(json.dumps(envelope).encode("utf-8"))

    return fake_urlopen


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
        self._pos = 0
        self.headers = {}

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


class _ScriptedProvider:
    """Mirrors ``tests/test_resolve_pending_tool_use.py``'s own fake: emits ``first_calls`` as
    tool_use blocks on the FIRST model call, plain text after — so a resumed turn terminates."""

    def __init__(self, first_calls):
        self._first_calls = first_calls
        self.calls = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.calls += 1
        if self.calls == 1:
            for i, (name, inp) in enumerate(self._first_calls):
                yield {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Same posture as ``tests/test_resolve_pending_tool_use.py``'s own ``env`` fixture, plus a
    REAL ``McpClientManager`` (fake HTTP transport, ``db_isolated``-backed store) registered on
    the external-MCP dispatcher seam so ``mcp__drive__*`` tool_use names route through it
    exactly as they do in production."""
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    monkeypatch.setattr(gate, "GATE_ALL_WRITES", True)

    store = McpClientStore()
    store.create(_cfg())
    store.record_discovery("drive", [{"name": "transfer"}], "ok", None)
    captured: dict = {}
    monkeypatch.setattr("backend.mcp.client._urlopen", _make_fake_urlopen(captured))
    manager = McpClientManager(store=store)
    mcp_external.register_external_dispatcher(manager)
    # Include the external schema in this turn's offer — the REAL `_active_tools()` only
    # appends an external tool once its exact name has already been loaded via find_tools
    # (§4's "loaded set"), which would otherwise route this first call through the discovery
    # gate's unconditional external-tool bounce (`_discovery_gate_verdict`) instead of
    # dispatching it. Mirrors `test_resolve_pending_tool_use.py`'s own `_active_tools`
    # override, extended with the external schemas this file's turns actually need offered.
    monkeypatch.setattr(
        assistant,
        "_active_tools",
        lambda: list(assistant.TOOL_SCHEMAS) + assistant._external_tool_schemas(),
    )

    yield tmp_path, captured
    mcp_external.register_external_dispatcher(None)
    set_tool_use_id(None)


def _install(monkeypatch, provider) -> None:
    monkeypatch.setattr(assistant, "get_provider", lambda: provider)


async def _drain(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


# ── 1. Args are authoritative-from-storage (H1) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_fires_the_stored_args_never_a_resend(env, monkeypatch):
    """A card showing ``{"to": "cash", "amount": 100}`` fires exactly that on approve.

    Structural proof of H1: ``resolve_pending_tool_use`` (what ``POST /api/chat/{id}/resolve``
    calls) takes only ``session_id``/``tool_use_id``/``decision`` — there is no args parameter
    anywhere on this call for a substituted amount to ride in on, unlike the old nonce path
    which at least LOOKED like ``apply_confirmed`` could grow a second argument."""
    _tmp, captured = env
    provider = _ScriptedProvider([("mcp__drive__transfer", {"to": "cash", "amount": 100})])
    _install(monkeypatch, provider)
    session_id = "test-ext-mcp-approve"

    tool_use_id = None
    for ev in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "transfer $100 to savings"}], "s")
    ):
        if ev.get("type") == "proposed_change":
            tool_use_id = ev["tool_use_id"]
            assert ev["change"]["type"] == "external_mcp_call"
            assert ev["change"]["args"] == {"to": "cash", "amount": 100}
            assert "nonce" not in ev["change"]
    assert tool_use_id == "t0"
    assert "call_args" not in captured, "nothing must fire before approval"

    await _drain(assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s"))

    assert captured["call_args"]["arguments"] == {"to": "cash", "amount": 100}, (
        "the STORED args must fire verbatim"
    )

    _, history = assistant.get_or_create_session(session_id)
    tool_use_ids = [
        b["id"]
        for m in history
        if m.get("role") == "assistant"
        for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    assert tool_use_ids == ["t0"], "approval must not add a second visible tool_use"


# ── 2. Unknown / already-used pending entry is refused (M2) ────────────────────────────────


@pytest.mark.asyncio
async def test_resolving_an_unknown_tool_use_id_is_refused(env):
    """No prior proposal ⇒ resolve refuses cleanly rather than raising or silently no-opping."""
    _tmp, captured = env
    events = await _drain(
        assistant.resolve_pending_tool_use(
            "test-ext-mcp-unknown", "never-staged-toolu", "approve", "s"
        )
    )
    assert any(e.get("type") == "error" for e in events)
    assert "call_args" not in captured


@pytest.mark.asyncio
async def test_a_second_approve_of_the_same_card_is_refused_not_replayed(env, monkeypatch):
    """Single-use: approving the SAME card twice fires the external call exactly once."""
    _tmp, captured = env
    provider = _ScriptedProvider([("mcp__drive__transfer", {"to": "cash", "amount": 5})])
    _install(monkeypatch, provider)
    session_id = "test-ext-mcp-replay"

    tool_use_id = None
    for ev in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "transfer $5 to checking"}], "s")
    ):
        if ev.get("type") == "proposed_change":
            tool_use_id = ev["tool_use_id"]

    await _drain(assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s"))
    assert captured["call_args"]["arguments"] == {"to": "cash", "amount": 5}
    captured.pop("call_args")

    # Replay the SAME tool_use_id — refused, and NOTHING fires a second time.
    events = await _drain(
        assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s")
    )
    assert any(e.get("type") == "error" for e in events)
    assert "call_args" not in captured


# ── 3. The current permission level is re-checked at resolution time ───────────────────────


@pytest.mark.asyncio
async def test_a_tool_blocked_while_the_card_waited_is_refused_not_fired(env, monkeypatch):
    """Set to ``blocked`` AFTER the card was raised but BEFORE the tap — the resolve path must
    refuse, not fire, proving the level is re-read at apply time rather than trusted from the
    moment the card was drawn."""
    _tmp, captured = env
    provider = _ScriptedProvider([("mcp__drive__transfer", {"to": "cash", "amount": 100})])
    _install(monkeypatch, provider)
    session_id = "test-ext-mcp-blocked-after-propose"

    tool_use_id = None
    for ev in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "transfer $100 to savings"}], "s")
    ):
        if ev.get("type") == "proposed_change":
            tool_use_id = ev["tool_use_id"]
    assert tool_use_id

    tool_permissions.set_tool_permission(mcp_connector_id("drive"), "transfer", "blocked")

    result_text = None
    async for ev in assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s"):
        if ev.get("type") == "text_delta":
            result_text = (result_text or "") + ev.get("text", "")

    assert "call_args" not in captured, "a blocked tool must never fire"

    _, history = assistant.get_or_create_session(session_id)
    result_blocks = [
        b
        for m in history
        if m.get("role") == "user"
        for b in (m.get("content") or [])
        if isinstance(b, dict)
        and b.get("type") == "tool_result"
        and b.get("tool_use_id") == tool_use_id
    ]
    assert len(result_blocks) == 1
    assert "blocked" in result_blocks[0]["content"].lower()
