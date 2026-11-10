"""RFC 2026-07-22 (approval-as-pending-tool_use) — the end-to-end turn-loop behavior.

Drives the REAL turn loop (``assistant.run``/``assistant.resolve_pending_tool_use``) and the
REAL confirm gate (``registry.dispatch`` → ``ai.tools._confirm_gate``) — only the LLM provider
is stubbed (a scripted/validating fake, no network) so these stay fast and offline while
proving the actual production code paths, not a mock of them.

Covers the stages' own stated verification bar:
  1. Resume/400 safety — a pending call survives a simulated server restart and the next
     model call is never sent a transcript shape a real Anthropic API would 400 on.
  2. One call, not two — approve appends the result to the SAME tool_use_id.
  3. N calls, N cards — two gated calls in one model response each get their own pending
     state and their own proposed_change event.
  4. Abort-on-new-message — a new user message aborts a still-pending call before it is
     processed.
  5. Live gate intact — real writes still gate, read-shaped tools don't, email_send_message gates.

The genuine-crash-orphan-still-stubbed case is already pinned by
``tests/test_dangling_tool_use_approval.py`` and is not duplicated here.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant
from backend.ai.tools import _confirm_gate as gate
from backend.core.tool_use_context import set_tool_use_id
from backend.db import pending_plan_store as plan_mod
from backend.services import payee_registry


def _assert_valid_transcript(messages: list[dict]) -> None:
    """Fails the way a real Anthropic call would 400: an assistant tool_use with no matching
    tool_result in the VERY NEXT message (when there is one) is an invalid transcript."""
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
        tu_ids = {b["id"] for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"}
        if not tu_ids or i + 1 >= len(messages):
            continue  # a trailing tool_use with nothing after it yet is fine
        nxt = messages[i + 1]
        assert nxt.get("role") == "user", (
            f"assistant tool_use {tu_ids} not followed by a user tool_result message"
        )
        nxt_blocks = nxt.get("content") if isinstance(nxt.get("content"), list) else []
        tr_ids = {
            b.get("tool_use_id")
            for b in nxt_blocks
            if isinstance(b, dict) and b.get("type") == "tool_result"
        }
        missing = tu_ids - tr_ids
        assert not missing, (
            f"tool_use ids {missing} have no matching tool_result in the next message"
        )


class _ScriptedProvider:
    """Emits ``first_calls`` as tool_use blocks on the FIRST model call, then plain text on
    every subsequent call (so a resumed/continued turn terminates cleanly). Validates every
    transcript it is handed — the thing that would actually 400 against a real API."""

    def __init__(self, first_calls):
        self._first_calls = first_calls
        self.calls = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.calls += 1
        _assert_valid_transcript(messages)
        if self.calls == 1:
            for i, (name, inp) in enumerate(self._first_calls):
                yield {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolated stores + the gate armed on a card-capable surface, no standing permissions —
    the same posture a real interactive web turn has. PayeeStore and ToolPermissionStore
    (the absorbed ToolGrantStore's successor, revision 0040) are DB-backed — db_isolated
    redirects both (and every other unpinned store) to a fresh, empty database, so every
    ``ToolPermissionStore()``/``payee_registry`` call anywhere in the turn loop naturally
    shares it; no more per-store _path/constructor-arg to repoint or class-level monkeypatch
    to force a single shared instance."""
    # PendingPlanStore is DB-backed now (WAVE 5D2b, JSON->DB) — db_isolated already covers
    # its isolation, no class-level __init__ override needed.
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    monkeypatch.setattr(gate, "GATE_ALL_WRITES", True)
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    # Never let a real memory/fold background task survive past the test.
    yield tmp_path
    set_tool_use_id(None)


def _install(monkeypatch, provider) -> None:
    monkeypatch.setattr(assistant, "get_provider", lambda: provider)


async def _drain(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


# ── 1. Resume/400 safety (the hardest bar) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_after_simulated_server_restart_does_not_400(env, monkeypatch):
    """Stage a pending call, simulate a process restart (drop the in-memory session cache —
    the on-disk session file + PendingPlanStore's own file are what actually survives a real
    restart), then send a NEW message with NOBODY having approved the card. The healer must
    NOT mis-stub the still-awaiting tool_use as a crash-orphan, and the abort-on-new-message
    trigger must resolve it before the model is called again — proven by the scripted
    provider's own transcript validation raising if either failed."""
    provider = _ScriptedProvider([("create_payees", {"name": "Restart Co"})])
    _install(monkeypatch, provider)
    session_id = "test-resume-restart"

    tool_use_id = None
    for ev in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "add a payee"}], "s")
    ):
        if ev.get("type") == "proposed_change":
            tool_use_id = ev["tool_use_id"]
    assert tool_use_id == "t0"

    from backend.db.pending_plan_store import PendingPlanStore

    store = PendingPlanStore()
    assert store.get_pending(tool_use_id) is not None, "the pending record must survive"

    # Simulate the restart: the in-memory SESSIONS cache is gone; the NEXT get_or_create_session
    # for this id re-loads from disk (where _fix_dangling_tool_uses runs, per get_or_create_session).
    assistant.SESSIONS.pop(session_id, None)

    # The next model call — a brand new user message, no approval — must not 400.
    await _drain(assistant.run(session_id, [{"role": "user", "content": "never mind"}], "s"))

    assert provider.calls == 2, "the second run() call never reached the (stubbed) model"
    assert store.get_pending(tool_use_id) is None, "abort must consume the pending record"

    _, history = assistant.get_or_create_session(session_id)
    resolved = [
        b
        for m in history
        if m.get("role") == "user"
        for b in (m.get("content") or [])
        if isinstance(b, dict)
        and b.get("type") == "tool_result"
        and b.get("tool_use_id") == tool_use_id
    ]
    assert len(resolved) == 1, "the paused tool_use must end up with EXACTLY one result"
    assert "did not approve" in resolved[0]["content"]


# ── 2. One call, not two ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_appends_the_result_to_the_same_tool_use_id(env, monkeypatch):
    provider = _ScriptedProvider([("create_payees", {"name": "OneCall Co"})])
    _install(monkeypatch, provider)
    session_id = "test-one-call"

    tool_use_id = None
    for ev in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "add OneCall Co"}], "s")
    ):
        if ev.get("type") == "proposed_change":
            tool_use_id = ev["tool_use_id"]
    assert tool_use_id == "t0"

    await _drain(assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s"))

    _, history = assistant.get_or_create_session(session_id)
    tool_use_ids = [
        b["id"]
        for m in history
        if m.get("role") == "assistant"
        for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    assert tool_use_ids == ["t0"], "a SECOND tool_use block appeared — the re-call bug regressed"

    results_for_t0 = [
        b
        for m in history
        if m.get("role") == "user"
        for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == "t0"
    ]
    assert len(results_for_t0) == 1, "exactly one result must land on the original id"
    assert "OneCall Co" in results_for_t0[0]["content"]
    # And the write actually happened.
    assert any(p.get("name") == "OneCall Co" for p in payee_registry.list_registry())


@pytest.mark.asyncio
async def test_approving_a_card_left_open_for_hours_still_executes(env, monkeypatch):
    """REGRESSION (2026-07-30, ADR ``approvals-never-expire``). A ``delete_transactions`` card
    sat on screen for 22 minutes; the 15-minute TTL plus the 5-minutely scheduler sweep deleted
    the pending record underneath it, so the ✓ came back "This approval is no longer pending"
    while the UI had already painted "✓ Approved". An approval is a question put to a human —
    it waits until it is answered or superseded, never on a clock.
    """
    provider = _ScriptedProvider([("create_payees", {"name": "Patient Co"})])
    _install(monkeypatch, provider)
    session_id = "test-no-ttl"

    tool_use_id = None
    for ev in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "add Patient Co"}], "s")
    ):
        if ev.get("type") == "proposed_change":
            tool_use_id = ev["tool_use_id"]
    assert tool_use_id

    # Age the staged row well past the legacy 15-minute TTL, exactly as wall-clock would.
    store = plan_mod.PendingPlanStore()
    raw = store._read_raw()
    raw[tool_use_id]["created_at"] = "2020-01-01T00:00:00+00:00"
    store._write_raw(raw)
    assert store.get_pending(tool_use_id) is not None, "an aged card must still read as pending"

    events = await _drain(
        assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s")
    )

    assert not [e for e in events if e.get("type") == "error"], (
        "an aged approval was refused — the TTL regressed"
    )
    assert any(p.get("name") == "Patient Co" for p in payee_registry.list_registry()), (
        "the approved write did not happen"
    )


@pytest.mark.asyncio
async def test_a_refused_approval_names_its_tool_use_id_so_the_card_can_be_un_painted(
    env, monkeypatch
):
    """The client paints ✓ optimistically, so a refusal MUST identify which card it refused —
    otherwise the UI shows a green tick above a red error (the 2026-07-30 screenshot)."""
    provider = _ScriptedProvider([("create_payees", {"name": "Ghost Co"})])
    _install(monkeypatch, provider)
    session_id = "test-refusal-shape"
    await _drain(assistant.run(session_id, [{"role": "user", "content": "add Ghost Co"}], "s"))

    events = await _drain(
        assistant.resolve_pending_tool_use(session_id, "never-staged", "approve", "s")
    )
    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["tool_use_id"] == "never-staged"


# ── 3. N calls, N cards ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_gated_calls_in_one_response_each_get_their_own_pending_state_and_card(
    env, monkeypatch
):
    provider = _ScriptedProvider(
        [
            ("create_payees", {"name": "Alpha Co"}),
            ("create_payees", {"name": "Beta Co"}),
        ]
    )
    _install(monkeypatch, provider)
    session_id = "test-n-cards"

    proposed = [
        ev
        for ev in await _drain(
            assistant.run(session_id, [{"role": "user", "content": "add both"}], "s")
        )
        if ev.get("type") == "proposed_change"
    ]
    assert len(proposed) == 2, "N gated calls in one response must produce N cards, not 1"
    ids = {ev["tool_use_id"] for ev in proposed}
    assert ids == {"t0", "t1"}

    from backend.db.pending_plan_store import PendingPlanStore

    store = PendingPlanStore()
    for tid in ids:
        assert store.get_pending(tid) is not None, f"{tid} must have its own pending record"

    # Neither tool_use has a result yet — the turn ended with BOTH result-less.
    _, history = assistant.get_or_create_session(session_id)
    last = history[-1]
    assert last.get("role") == "assistant"
    assert not any(m.get("role") == "user" and m is not history[0] for m in history[1:])


@pytest.mark.asyncio
async def test_sibling_approvals_buffer_results_until_the_group_is_complete(env, monkeypatch):
    provider = _ScriptedProvider(
        [
            ("create_payees", {"name": "Alpha Buffered"}),
            ("create_payees", {"name": "Beta Buffered"}),
        ]
    )
    _install(monkeypatch, provider)
    session_id = "test-buffer-sibling-results"

    await _drain(assistant.run(session_id, [{"role": "user", "content": "add both"}], "s"))
    assert provider.calls == 1

    first_events = await _drain(
        assistant.resolve_pending_tool_use(session_id, "t0", "approve", "s")
    )

    assert any(p.get("name") == "Alpha Buffered" for p in payee_registry.list_registry())
    assert not any(p.get("name") == "Beta Buffered" for p in payee_registry.list_registry())
    assert provider.calls == 1, "the model must not resume while a sibling is unresolved"
    assert not any(ev.get("type") == "text_delta" for ev in first_events)

    from backend.db.pending_plan_store import PendingPlanStore

    assert PendingPlanStore().get_pending("t1") is not None

    # Prove the partial result and remaining pending call survive a process restart.
    assistant.SESSIONS.pop(session_id, None)

    await _drain(assistant.resolve_pending_tool_use(session_id, "t1", "approve", "s"))

    assert any(p.get("name") == "Beta Buffered" for p in payee_registry.list_registry())
    assert provider.calls == 2, "the completed group resumes the provider exactly once"
    _, history = assistant.get_or_create_session(session_id)
    result_messages = [
        m
        for m in history
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
    ]
    assert len(result_messages) == 1
    assert [b["tool_use_id"] for b in result_messages[0]["content"]] == ["t0", "t1"]


# ── 4. Abort-on-new-message ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_message_aborts_the_still_pending_call_before_processing(env, monkeypatch):
    provider = _ScriptedProvider([("create_payees", {"name": "Abort Co"})])
    _install(monkeypatch, provider)
    session_id = "test-abort-new-message"

    tool_use_id = None
    for ev in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "add Abort Co"}], "s")
    ):
        if ev.get("type") == "proposed_change":
            tool_use_id = ev["tool_use_id"]
    assert tool_use_id == "t0"

    from backend.db.pending_plan_store import PendingPlanStore

    store = PendingPlanStore()
    assert store.get_pending(tool_use_id) is not None

    await _drain(assistant.run(session_id, [{"role": "user", "content": "actually skip it"}], "s"))

    assert store.get_pending(tool_use_id) is None, "abort must consume the pending record"
    assert not any(p.get("name") == "Abort Co" for p in payee_registry.list_registry()), (
        "an aborted call must never have written anything"
    )

    _, history = assistant.get_or_create_session(session_id)
    aborted = [
        b
        for m in history
        if m.get("role") == "user"
        for b in (m.get("content") or [])
        if isinstance(b, dict)
        and b.get("type") == "tool_result"
        and b.get("tool_use_id") == tool_use_id
    ]
    assert len(aborted) == 1
    assert "did not approve" in aborted[0]["content"]


# ── 5. Deny (the resolve endpoint's OTHER outcome) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_deny_stubs_the_tool_use_and_writes_nothing(env, monkeypatch):
    provider = _ScriptedProvider([("create_payees", {"name": "Denied Co"})])
    _install(monkeypatch, provider)
    session_id = "test-deny"

    tool_use_id = None
    for ev in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "add Denied Co"}], "s")
    ):
        if ev.get("type") == "proposed_change":
            tool_use_id = ev["tool_use_id"]

    await _drain(assistant.resolve_pending_tool_use(session_id, tool_use_id, "deny", "s"))

    assert not any(p.get("name") == "Denied Co" for p in payee_registry.list_registry())
    _, history = assistant.get_or_create_session(session_id)
    results = [
        b
        for m in history
        if m.get("role") == "user"
        for b in (m.get("content") or [])
        if isinstance(b, dict)
        and b.get("type") == "tool_result"
        and b.get("tool_use_id") == tool_use_id
    ]
    assert len(results) == 1
    assert "DECLINED" in results[0]["content"]


# ── 6. Live gate intact ──────────────────────────────────────────────────────────────────


def test_real_writes_still_gate_read_shaped_tools_dont_email_send_message_gates(env):
    set_tool_use_id("toolu_live_gate_check")
    assert gate.should_gate("update_transactions") is True
    assert gate.should_gate("create_account") is True
    assert gate.should_gate("email_send_message") is True
    assert gate.should_gate("email_search_messages") is False, (
        "read-shaped exemption must still hold"
    )
    assert gate.should_gate("email_get_messages") is False, "read-shaped exemption must still hold"
    assert gate.should_gate("get_live_prices") is False
    assert gate.should_gate("get_transactions") is False
    assert gate.should_gate("list_accounts") is False
