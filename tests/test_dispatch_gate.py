"""Interactive tool-discovery gate (Lane E — tool-discovery RFC).

The CORE-based offer (~20 tools) means most tools are NOT in the ``tools=`` array sent
to the model — the full surface lives in the prompt catalog instead. When the model
calls a catalog tool it has not loaded yet, the dispatch loop must NOT run it blind on
guessed arguments. Instead the gate:

  * loads the tool's real schema (``add_extra_names``) and then VALIDATES the arguments the
    model already emitted against it:
      - a READ whose arguments validate is EXECUTED right there — one round trip, not two
        (a bounce re-sends the whole context to learn nothing the model does not know);
      - anything else BOUNCES — returns an ERROR tool_result ("NOT EXECUTED", carrying the
        pointer-precise validation error, when there is one) so the RETRY sees the true
        parameters. WRITES bounce unconditionally, valid arguments or not; OR
  * refuses a hallucinated name with an "unknown tool" error.

The verdict logic and the read/write asymmetry live in ``assistant._discovery_gate_verdict``
and are unit-tested in ``tests/test_discovery_gate.py``; this file pins the LOOP wiring.

Security invariant (the load-bearing one): the gate is INTERACTIVE-only. On a
headless/skill turn the pre-existing default-deny block refuses any off-scope name first,
so the gate never auto-loads a tool an unattended turn could not otherwise reach — the
T2-never-headless boundary is untouched.

Offline + isolated: the provider, ``execute_tool`` and session-meta persistence are all
stubbed (no LLM, no network, no disk). ContextVars are reset between tests.

Run:
    .venv/bin/python3 -m pytest tests/test_dispatch_gate.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.ai.assistant as assistant
from backend.ai.assistant import _is_known_tool
from backend.ai.tools import _skill_scope
from backend.ai.tools._extra_groups_scope import set_extra_groups, set_extra_names
from backend.core import mcp_external

# A small explicit tool offer, standing in for what a headless caller now passes.
_SAMPLE_OFFER = {"get_net_worth", "get_transactions", "get_portfolio", "telegram_send_message"}

_FAKE_MCP_SCHEMA = {
    "name": "mcp__fake__tool",
    "description": "fake external mcp tool used in tests",
    "input_schema": {"type": "object", "properties": {}},
}


_SID_COUNTER = [0]  # unique session id per _drive call → no cross-test history reload


class _FakeDispatcher:
    def schemas(self):
        return [_FAKE_MCP_SCHEMA]


class _ScriptedProvider:
    """Emits a scripted list of tool_use calls per model round; plain text once the
    script is exhausted (so the assistant tool loop terminates)."""

    def __init__(self, turns):
        # turns: list of rounds; each round is a list of (name, input) tool calls.
        self._turns = turns
        self._i = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium", **kwargs):
        idx = self._i
        self._i += 1
        if idx < len(self._turns):
            for j, (name, inp) in enumerate(self._turns[idx]):
                yield {"type": "tool_use", "id": f"r{idx}_{j}", "name": name, "input": inp}
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture(autouse=True)
def _reset_scopes():
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())
    yield
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())


def _drive(monkeypatch, turns, *, headless=False, skill=None, offer=None):
    """Run one assistant turn with a scripted provider + stubbed execute_tool/persistence.

    ``offer`` overrides ``_SAMPLE_OFFER`` when ``headless=True`` — lets a test drive a
    LARGE explicit offer (an oversized agent) through the same harness.

    Returns (executed_calls, tool_result_contents, persisted_meta_kwargs)."""
    executed: list[tuple] = []

    async def fake_execute_tool(name, inp, tool_use_id=None):
        executed.append((name, inp))
        return json.dumps({"ok": True}), None

    captured_meta: list[dict] = []

    def spy_upsert(session_id, **kw):  # avoid real disk writes
        captured_meta.append(kw)

    monkeypatch.setattr(assistant, "get_provider", lambda: _ScriptedProvider(turns))
    monkeypatch.setattr(assistant, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(assistant, "upsert_session_meta", spy_upsert)
    monkeypatch.setattr(assistant, "_save_session", lambda *a, **k: None)  # no disk, no reload

    if headless:
        _skill_scope.set_offer_scope(offer if offer is not None else _SAMPLE_OFFER)
    if skill is not None:
        _skill_scope.set_offer_scope(skill)

    _SID_COUNTER[0] += 1
    sid = f"test-gate-{_SID_COUNTER[0]}"
    results: list[str] = []

    async def _go():
        try:
            async for _ev in assistant.run(
                session_id=sid,
                new_messages=[{"role": "user", "text": "go"}],
                system="s",
            ):
                pass
            for msg in assistant.SESSIONS.get(sid, []):
                if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    for part in msg["content"]:
                        if isinstance(part, dict) and part.get("type") == "tool_result":
                            results.append(part.get("content", ""))
        finally:
            assistant.SESSIONS.pop(sid, None)

    asyncio.run(_go())
    return executed, results, captured_meta


def _loaded_names(meta_kwargs: list[dict]) -> set[str]:
    names: set[str] = set()
    for kw in meta_kwargs:
        for n in kw.get("extra_tool_names") or []:
            names.add(n)
    return names


# ---------------------------------------------------------------------------
# _is_known_tool unit coverage
# ---------------------------------------------------------------------------


def test_is_known_tool_internal_true():
    assert _is_known_tool("get_net_worth") is True
    assert _is_known_tool("update_automations") is True


def test_is_known_tool_unknown_false():
    assert _is_known_tool("totally_fake_tool") is False
    # mcp__ name with no dispatcher configured is NOT known.
    assert _is_known_tool("mcp__fake__tool") is False


def test_is_known_tool_external_true_with_dispatcher(monkeypatch):
    monkeypatch.setattr(mcp_external, "get_external_dispatcher", lambda: _FakeDispatcher())
    assert _is_known_tool("mcp__fake__tool") is True
    assert _is_known_tool("mcp__fake__missing") is False


# ---------------------------------------------------------------------------
# Interactive gate behaviour
# ---------------------------------------------------------------------------


def test_unloaded_read_with_valid_args_executes_in_one_pass(monkeypatch):
    """The round-trip saving. An unloaded READ whose guessed arguments validate against the
    freshly loaded schema runs immediately — no bounce, no retry."""
    turns = [[("get_uncategorized_summary", {})]]
    executed, results, meta = _drive(monkeypatch, turns)

    assert executed == [("get_uncategorized_summary", {})]
    assert not any("NOT EXECUTED" in r for r in results)
    # Still loaded + persisted, so the rest of the session sees the real schema.
    assert "get_uncategorized_summary" in _loaded_names(meta)


def test_unloaded_read_with_invalid_args_bounces_with_the_validation_error(monkeypatch):
    """A bad guess still costs a round trip — but the retry is informed, not blind."""
    turns = [[("trace_transactions", {"transaction_ids": {"nested": "obj"}})]]
    executed, results, _meta = _drive(monkeypatch, turns)

    assert executed == []
    bounce = next(r for r in results if "NOT EXECUTED" in r)
    assert "invalid_arguments" in bounce
    assert "transaction_ids" in bounce


def test_unloaded_write_bounces_even_when_args_are_valid(monkeypatch):
    """The deliberate asymmetry, at the loop level: a write is never auto-executed on
    arguments the model guessed without seeing the schema."""
    turns = [[("delete_transactions", {"transaction_ids": ["txn_1"]})]]
    executed, results, meta = _drive(monkeypatch, turns)

    assert executed == []
    assert any("NOT EXECUTED" in r for r in results)
    assert "delete_transactions" in _loaded_names(meta)


def test_unloaded_known_tool_bounces_then_executes(monkeypatch):
    """A blind call to a known non-core WRITE BOUNCES (not executed), then the retry
    runs once the schema is loaded."""
    turns = [
        [("update_automations", {"id": "x"})],  # round 0: blind → bounce
        [("update_automations", {"automation_id": "x"})],  # round 1: now loaded → execute
    ]
    executed, results, meta = _drive(monkeypatch, turns)

    # Executed exactly once — only the post-load retry, never the blind call.
    assert executed == [("update_automations", {"automation_id": "x"})]
    assert any("NOT EXECUTED" in r for r in results)
    assert any('"ok": true' in r for r in results)
    # The tool was persisted into the session's loaded-name scope.
    assert "update_automations" in _loaded_names(meta)


def test_unknown_tool_is_refused_not_executed(monkeypatch):
    turns = [[("totally_fake_tool", {"foo": 1})]]
    executed, results, meta = _drive(monkeypatch, turns)
    assert executed == []
    assert any("unknown tool" in r for r in results)
    assert "totally_fake_tool" not in _loaded_names(meta)


def test_core_tool_executes_without_gate(monkeypatch):
    """A CORE tool is already in the offer → the gate never fires."""
    turns = [[("get_net_worth", {})]]
    executed, results, _meta = _drive(monkeypatch, turns)
    assert executed == [("get_net_worth", {})]
    assert not any("NOT EXECUTED" in r for r in results)


def test_sibling_calls_same_unloaded_tool_both_bounce(monkeypatch):
    """Two tool_use blocks for the SAME unloaded tool in ONE round: the offer is
    snapshotted once before the loop, so BOTH bounce — a tool loaded by the first
    sibling must not let the second slip through and execute on guessed args."""
    turns = [[("update_automations", {"a": 1}), ("update_automations", {"b": 2})]]
    executed, results, meta = _drive(monkeypatch, turns)
    assert executed == []  # neither sibling executed
    assert len([r for r in results if "NOT EXECUTED" in r]) == 2
    assert "update_automations" in _loaded_names(meta)


def test_full_tool_offer_env_bypasses_gate(monkeypatch):
    """ONW_FULL_TOOL_OFFER=1 offers every tool, so the gate is a no-op (no bounce)."""
    monkeypatch.setenv("ONW_FULL_TOOL_OFFER", "1")
    turns = [[("update_automations", {"automation_id": "x"})]]
    executed, results, _meta = _drive(monkeypatch, turns)
    assert executed == [("update_automations", {"automation_id": "x"})]
    assert not any("NOT EXECUTED" in r for r in results)


def test_external_tool_bounces_when_unloaded(monkeypatch):
    """An unloaded external-MCP tool flows through the SAME gate: bounce + load, no
    blind execution — the universal path that needs no pre-grouping."""
    monkeypatch.setattr(mcp_external, "get_external_dispatcher", lambda: _FakeDispatcher())
    turns = [[("mcp__fake__tool", {"q": "hi"})]]
    executed, results, meta = _drive(monkeypatch, turns)
    assert executed == []  # internal execute_tool never called
    assert any("NOT EXECUTED" in r for r in results)
    assert "mcp__fake__tool" in _loaded_names(meta)


# ---------------------------------------------------------------------------
# The discovery gate never auto-loads a tool OUTSIDE the caller's explicit offer
# ---------------------------------------------------------------------------


def test_explicit_offer_turn_does_not_auto_load_an_unoffered_tool(monkeypatch):
    """When the CALLER supplied the turn's tool offer (an agent run), the discovery gate
    must NOT auto-load a tool OUTSIDE that offer — a name ``update_automations`` never
    declared is left untouched (falls straight through to normal dispatch), never bounced,
    never loaded into the turn's extra-names scope.

    Note what this test no longer asserts: the tool is not REFUSED either. The dispatch-time
    default-deny block was removed with the rest of the permission system, so an explicit
    offer is now advisory — it shapes what the model is shown, not what it may execute."""
    turns = [[("update_automations", {"id": "x"})]]
    executed, results, meta = _drive(monkeypatch, turns, headless=True)
    assert not any("NOT EXECUTED" in r for r in results)
    assert "update_automations" not in _loaded_names(meta)


# ---------------------------------------------------------------------------
# A LARGE explicit offer (an oversized agent, ai/tools/_agent_offer) DOES get the
# lazy auto-load-then-validate path — for tools INSIDE its own offer only
# ---------------------------------------------------------------------------


def _large_real_offer():
    """Every registered tool except find_tools — stands in for the live 135-tool
    Transaction & Trade Logger agent (2026-07-19 measurement)."""
    from backend.ai.tools.registry import registered_tool_names

    return frozenset(registered_tool_names()) - {"find_tools"}


def test_agent_offer_unloaded_read_with_valid_args_executes_in_one_pass(monkeypatch):
    """The interactive round-trip-saving optimization applies identically to the
    agent-offer branch: a tool inside the offer but not yet inlined, called with
    arguments that already validate, executes right here — no bounce."""
    target = "get_uncategorized_summary"  # real, registered, not in CORE_TOOL_NAMES
    turns = [[(target, {})]]
    executed, results, meta = _drive(monkeypatch, turns, headless=True, offer=_large_real_offer())
    assert executed == [(target, {})]
    assert not any("NOT EXECUTED" in r for r in results)
    assert target in _loaded_names(meta)


def test_agent_offer_unloaded_read_with_invalid_args_bounces_then_retries(monkeypatch):
    """A bad guess still costs a round trip — but the retry is informed, not blind —
    identical to the interactive gate's behavior (``test_discovery_gate.py``)."""
    target = "trace_transactions"
    turns = [
        [(target, {"transaction_ids": {"nested": "obj"}})],  # round 0: invalid -> bounce
        [(target, {"transaction_ids": ["txn_1"]})],  # round 1: now loaded -> executes
    ]
    executed, results, meta = _drive(monkeypatch, turns, headless=True, offer=_large_real_offer())
    assert executed == [(target, {"transaction_ids": ["txn_1"]})]
    bounce = next(r for r in results if "NOT EXECUTED" in r)
    assert "invalid_arguments" in bounce
    assert target in _loaded_names(meta)


def test_agent_offer_write_tool_bounces_once_then_stays_loaded(monkeypatch):
    """The read/write asymmetry holds for the agent-offer branch too: a write never
    auto-executes on guessed arguments, even though it's inside the agent's own offer.
    Once loaded, it stays in the round's offer — the SECOND call executes directly
    (the discovery-gate branch is skipped entirely, same as the interactive path's
    ``update_automations`` case) rather than re-bouncing."""
    from backend.ai.tools.registry import mutated_domains

    target = "delete_transactions"
    assert mutated_domains(target)
    turns = [
        [(target, {"transaction_ids": ["txn_1"]})],  # round 0: write -> bounce (loads it)
        [(target, {"transaction_ids": ["txn_1"]})],  # round 1: now offered -> executes
    ]
    executed, results, meta = _drive(monkeypatch, turns, headless=True, offer=_large_real_offer())
    assert executed == [(target, {"transaction_ids": ["txn_1"]})]
    assert len([r for r in results if "NOT EXECUTED" in r]) == 1
    assert target in _loaded_names(meta)
