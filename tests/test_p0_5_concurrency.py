"""
P0-5 concurrency hardening tests (CONC-H5 + CONC-H3).

CONC-H5 — a nested set-then-clear on a shared ContextVar (the audit scope; the
skill/headless/responder/bundle/depth "skill scope") must RESTORE the OUTER value on
exit, not null it via ``var.set(None)``. Nesting is real: an interactive turn's tool
call can dispatch to ``services.agents.run_agent``, which awaits a FRESH, NESTED
``assistant.run`` turn in the SAME async context (a direct ``await`` chain, no forked
``asyncio.Task`` / fresh ``contextvars.Context``), so the inner turn's own
scope-teardown is visible to the still-running OUTER turn. A blanket ``.set(None)``
wipes the OUTER turn's narrowed scope out from under it — for an ``@agent`` responder
turn this is a scope-WIDENING escalation (the rest of the turn falls through to the
full interactive tool offer), and for the audit scope it silently loses
transaction-provenance attribution.

CONC-H3 — ``SESSIONS[session_id]`` is one shared, mutable history list; two concurrent
turns on the SAME session interleave their mutations (a web double-send, two devices,
dock+panel, or a connector spawning one task per delivery). One turn's exception-path
rollback (``del history[history_snapshot_len:]``) can truncate the OTHER, still
in-flight turn's messages. ``assistant.run`` now serializes same-session turns behind
a per-session ``asyncio.Lock``.

Review round (code + security, on this lane's own P0-5 change — see the bottom of this
file): a verified BLOCKER + a fix-is-inert-in-production finding + two should-fixes.

Finding 1 (BLOCKER) — closing ``assistant.run``'s generator early (a client disconnect
mid-turn) used to leave the inner ``_run_turn`` generator merely unreferenced rather than
explicitly closed; Python's GC + asyncio's abandoned-async-generator finalizer force-close
it LATER, in a freshly forked ``contextvars.Context``, where the audit-scope
``Token.reset()`` raises ``ValueError`` — silently skipping ``_finalise_checkpoint()``.
Fixed both ways: ``run()`` now explicitly ``await``s ``gen.aclose()`` in its own
``finally`` (delivers the close synchronously, in the original Context), and
``clear_audit_scope`` falls back to ``set(None)`` if a reset ever does fire from a
foreign Context.

Finding 2 (CORE, fix inert in production) — ``main._scheduled_agent_runner``'s ``finally``
unconditionally called ``_skill_scope.clear_scope()``, which is correct when the
SCHEDULER fires it top-level but wrong when reached via dispatch (nested inside an outer,
still-running turn) — exactly the CONC-H5 shape this lane exists to fix, just missed in
one more call site. Now snapshots on entry and ``restore_scope``s in the ``finally``.

Finding 3 (should-fix, FIXED) — ``clear_session()`` never popped ``_SESSION_LOCKS``; a
scheduled/dispatched run mints a fresh ``scheduled-<uuid>`` session id per invocation and
always calls ``clear_session()`` when done, leaking one ``asyncio.Lock`` per run forever.
The first fix (unconditional pop) was itself a NEW race: ``clear_session`` is also called
by ``DELETE /api/chat/{id}`` and ``connectors.session_reset.reset_session`` with no
happens-before vs. an in-flight ``run()`` on that same session — an unconditional pop could
yank the lock out from under a still-running turn, letting a concurrent retry/duplicate
submit mint a fresh, UNLOCKED lock for the same id (the exact CONC-H3 race). Narrowed to
pop only when the lock is NOT currently held (atomic on the single-threaded event loop — no
``await`` between the ``.locked()`` check and the pop).

Finding 4 (should-fix, WALKED BACK — not fixed for real) — ``restore_scope``'s
capture/restore of the auto-apply grant (a prior attempt at fixing "an outer agent's grant
gets wiped by a nested dispatch") was proven INERT in production by both the code and
security re-reviews: ``services.agents.run_agent``'s own ``finally`` unconditionally clears
the grant AFTER the runner (and the nested ``assistant.run`` turn ``restore_scope`` guards)
returns, so whatever ``restore_scope`` restored was immediately overwritten. The mechanism
+ its test asserted a guarantee that wasn't true. Reverted rather than fixed for real: the
nesting shape it would matter for is structurally UNREACHABLE today (no dispatch tool —
``dispatch_to_agent`` / ``run_agent_now`` / ``run_scheduled_task_now`` — is on the headless
ceiling or grantable T1, so an agent's own headless turn can never reach one; see
``tests/test_headless_scope.py``'s paired disjointness tests + the module-load-time
assertion in ``core.headless_scope``), so building a real fix for it now would be
unverifiable, unreachable machinery. ``ScopeSnapshot``/``snapshot_scope``/``restore_scope``
are back to the original 5 vars (auto-apply removed); ``run_agent``'s unconditional clear is
now explicitly documented as an accepted imprecision, not silently wrong.

Offline + isolated: no LLM, no network. Providers/session-meta/disk-persistence are
stubbed the same way as ``tests/test_dispatch_gate.py``.

Run:
    .venv/bin/python3 -m pytest tests/test_p0_5_concurrency.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from backend.ai import assistant
from backend.ai.tools import _skill_scope
from backend.core import audit_context


# ── CONC-H5: ContextVar token-based restore ────────────────────────────────────
@pytest.fixture(autouse=True)
def _reset_scopes():
    audit_context.clear_audit_scope()
    _skill_scope.clear_scope()
    yield
    audit_context.clear_audit_scope()
    _skill_scope.clear_scope()


def test_audit_scope_nested_set_clear_restores_outer_via_token():
    """A nested set+clear using the real contextvars Token/reset mechanism restores
    the OUTER scope; the old ``clear_audit_scope()`` (no token -> ``_scope.set(None)``)
    would null it instead. This is the exact shape of run_agent's dispatch nesting:
    the OUTER scope is set once by an interactive turn/outer agent run, then a NESTED
    turn sets + clears its OWN overlay in the same async context."""
    outer_token = audit_context.set_audit_scope("user", "ui", session_id="outer")
    try:
        inner_token = audit_context.set_audit_scope("agent", "agent:bot", session_id="inner")
        # ... the nested turn runs here ...
        audit_context.clear_audit_scope(inner_token)  # the FIX: reset(token), not set(None)

        restored = audit_context.get_audit_scope()
        assert restored is not None, "nested clear nulled the OUTER scope"
        assert restored.actor == "user"
        assert restored.session_id == "outer"
    finally:
        audit_context.clear_audit_scope(outer_token)
    assert audit_context.get_audit_scope() is None


def test_audit_scope_clear_without_token_still_nulls_top_level():
    """Back-compat: omitting the token (a top-level entrypoint that never nests, or
    ``set_audit_scope_if_absent``'s caller) still resets to None."""
    audit_context.set_audit_scope("user", "ui", session_id="s1")
    audit_context.clear_audit_scope()
    assert audit_context.get_audit_scope() is None


def test_skill_scope_nested_snapshot_restore_preserves_outer_responder_scope():
    """The ``_skill_scope`` analogue of the token test above: an outer ``@agent``
    responder's hard allowlist must survive a NESTED turn's own scope narrowing +
    teardown. ``assistant.run``'s finally now calls ``restore_scope(snapshot)``
    instead of the old blanket ``clear_scope()`` for exactly this reason (see the
    next test, which documents what the OLD call would have done).

    Models the real shape: ``api/chat.py`` sets the responder scope BEFORE calling
    ``assistant.run`` (the OUTER turn), whose own entry snapshot therefore already
    reflects it — restoring that snapshot at the outer turn's own exit puts back
    the responder scope, not None (the actual top-level wipe to None is chat.py's
    own separate, subsequent ``clear_scope()`` call, asserted at the end here)."""
    _skill_scope.set_offer_scope(["get_net_worth", "get_portfolio"])  # api/chat.py
    outer_run_snapshot = _skill_scope.snapshot_scope()  # the OUTER assistant.run() entry
    try:
        # A tool call in the outer turn dispatches to an agent -> a NESTED
        # assistant.run() call, which snapshots on ITS OWN entry (inheriting the
        # still-active outer responder scope) and narrows further for its own turn.
        nested_run_snapshot = _skill_scope.snapshot_scope()
        _skill_scope.set_offer_scope({"get_net_worth", "telegram_send_message"})
        # ... the nested turn's tool loop runs here ...
        _skill_scope.restore_scope(nested_run_snapshot)  # the nested run()'s own exit — the FIX

        # Back in the outer turn: its responder scope must be untouched by the
        # nested dispatch that just ran and exited.
        assert _skill_scope.offer_tool_names() == frozenset(["get_net_worth", "get_portfolio"]), (
            "nested restore nulled the OUTER responder scope"
        )
    finally:
        _skill_scope.restore_scope(outer_run_snapshot)  # the outer run()'s own exit
        _skill_scope.clear_scope()  # chat.py's own belt-and-braces top-level wipe
    assert _skill_scope.offer_tool_names() is None


def test_clear_scope_would_null_outer_scope_if_used_for_nested_exit():
    """Documents the exact bug ``restore_scope`` fixes: the pre-existing blanket
    ``clear_scope()`` (still used by TOP-LEVEL owners only — api/chat.py's
    belt-and-braces cleanup, the scheduled-agent-runner's own turn, mcp/adapter.py)
    unconditionally wipes every skill-scope ContextVar to None/0. If
    ``assistant.run``'s finally still called this for a NESTED turn (instead of
    ``restore_scope``), the outer responder scope would vanish exactly like this."""
    _skill_scope.set_offer_scope(["get_net_worth"])
    _skill_scope.clear_scope()
    assert _skill_scope.offer_tool_names() is None


# ── CONC-H3: per-session turn lock ──────────────────────────────────────────────
def test_concurrent_same_session_turns_are_serialized_not_corrupted(monkeypatch):
    """Two turns racing on the SAME session_id must not corrupt each other's history.

    Scripted so the ONLY thing that determines correctness is whether same-session
    turns are serialized: turn "b" starts FIRST, snapshots an EMPTY history
    (history_snapshot_len=0), extends it, then PARKS mid-flight (before its own
    provider raises). Turn "a" is started next — if NOT serialized behind "b", it
    runs to completion immediately (it has no internal suspension point of its own),
    appending its message + response, BEFORE "b" resumes; "b" then raises and its
    rollback (``del history[0:]``) wipes EVERYTHING, including "a"'s already-completed,
    unrelated turn. If serialized (the fix), "a" cannot even begin until "b" (and its
    own rollback) is fully done, so the final history contains ONLY "a"'s turn."""
    sid = "test-conc-h3-session"
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)

    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "_save_session", lambda *a, **k: None)

    async def _consume(gen):
        async for _ in gen:
            pass

    async def _go():
        # Constructed here (not in the outer sync test body) so ``asyncio.Lock``/
        # ``asyncio.Event`` bind to the loop that's actually running (Python 3.9
        # requires a running loop at construction time; a prior test's now-closed
        # ``asyncio.run`` leaves no "current" loop outside one).
        pause_before_raise = asyncio.Event()
        b_reached_pause = asyncio.Event()
        call_n = [0]

        class _RaisingProviderB:
            async def stream_chat(self, *a, **kw):
                b_reached_pause.set()
                await pause_before_raise.wait()
                raise RuntimeError("boom-b")
                yield  # pragma: no cover — unreachable; keeps this an async generator

        class _ImmediateProviderA:
            async def stream_chat(self, *a, **kw):
                yield {"type": "text_delta", "text": "a-ok"}
                yield {"type": "done", "stop_reason": "end_turn"}

        def _get_provider(*a, **kw):
            call_n[0] += 1
            return _RaisingProviderB() if call_n[0] == 1 else _ImmediateProviderA()

        monkeypatch.setattr(assistant, "get_provider", _get_provider)

        t_fail = asyncio.create_task(
            _consume(
                assistant.run(
                    session_id=sid,
                    new_messages=[{"role": "user", "content": "b", "_test_marker": "b"}],
                    system="s",
                )
            )
        )
        await b_reached_pause.wait()  # "b" has snapshotted + extended and is now parked
        t_succ = asyncio.create_task(
            _consume(
                assistant.run(
                    session_id=sid,
                    new_messages=[{"role": "user", "content": "a", "_test_marker": "a"}],
                    system="s",
                )
            )
        )
        # Give "a" every opportunity to run if it is NOT serialized behind "b".
        for _ in range(5):
            await asyncio.sleep(0)
        pause_before_raise.set()  # let "b" raise + roll back
        # "b"'s turn is EXPECTED to raise (run()/-run_turn's except path re-raises
        # after its rollback) — capture it instead of letting gather propagate it.
        return await asyncio.gather(t_fail, t_succ, return_exceptions=True)

    try:
        results = asyncio.run(_go())
        assert isinstance(results[0], RuntimeError), "b's turn should have raised boom-b"
        history = assistant.SESSIONS.get(sid, [])
        markers = [m.get("_test_marker") for m in history if isinstance(m, dict)]
        # CONC-H3 itself — "a" survived "b"'s failure. This is the safety property; it is now
        # satisfied by construction rather than by ordering, since nothing deletes history.
        assert "a" in markers, "a's turn was wiped by b's concurrent failure (CONC-H3)"
        # "b"'s own messages are KEPT, where this used to assert they were rolled away
        # (2026-08-16). The rollback was deleting the whole transcript of any crashed turn:
        # measured on run_2c606ddf3dc7, a run whose row recorded 24 tool calls and 5 of 6
        # tasks completed kept exactly ONE message, so nothing it did — ledger writes
        # included — was visible to a reader afterwards. The rollback was never what kept the
        # session valid for the next message either; `_fix_dangling_tool_uses` is, and it
        # still runs. What made the ORIGINAL bug dangerous was the rollback reaching across
        # into a CONCURRENT turn's messages, and the session lock is what prevents that.
        assert "b" in markers, "a crashed turn must leave its own record behind"
        assert markers.index("b") < markers.index("a"), "serialized: b's turn opened first"
    finally:
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)


def test_get_session_lock_returns_same_lock_for_same_session():
    # asyncio.Lock() must be constructed while a loop is running (Python 3.9 binds
    # eagerly at construction) — matches how _get_session_lock is actually invoked
    # in production (always from inside run(), an async generator).
    assistant._SESSION_LOCKS.pop("same-sid", None)

    async def _go():
        a = assistant._get_session_lock("same-sid")
        b = assistant._get_session_lock("same-sid")
        assert a is b

    try:
        asyncio.run(_go())
    finally:
        assistant._SESSION_LOCKS.pop("same-sid", None)


def test_get_session_lock_returns_different_locks_for_different_sessions():
    assistant._SESSION_LOCKS.pop("sid-one", None)
    assistant._SESSION_LOCKS.pop("sid-two", None)

    async def _go():
        a = assistant._get_session_lock("sid-one")
        b = assistant._get_session_lock("sid-two")
        assert a is not b

    try:
        asyncio.run(_go())
    finally:
        assistant._SESSION_LOCKS.pop("sid-one", None)
        assistant._SESSION_LOCKS.pop("sid-two", None)


# ── Review round: finding 1 (BLOCKER) ───────────────────────────────────────────
def test_abandoned_generator_close_clears_scope_and_finalizes_checkpoint_no_crash(
    monkeypatch,
):
    """Finding 1 (BLOCKER): closing the OUTER ``assistant.run()`` generator early (a
    client disconnect mid-turn, routine for chat) after only ONE event has been pulled
    used to leave the inner ``_run_turn`` generator merely unreferenced. CPython's
    refcounting GC collects it almost immediately, and asyncio's abandoned-async-
    generator finalizer force-closes it via a FRESH ``asyncio.Task`` — a freshly forked
    ``contextvars.Context``. ``clear_audit_scope``'s bare ``_scope.reset(token)`` can
    only run in the Context the token was created in, so that forked-context call raised
    ``ValueError`` instead of the intended ``GeneratorExit`` — asyncio logs "Task
    exception was never retrieved" and ``_finalise_checkpoint()`` is skipped entirely:
    the documented "persist the checkpoint exactly once even on early close" guarantee
    silently broke.

    Fail-first on ``c56e0b5a``: the loop's exception handler catches the "Task exception
    was never retrieved" context (``caught_exceptions`` non-empty) AND
    ``checkpoint_calls`` stays empty (``_finalise_checkpoint()`` never ran) — both
    assertions below fail. Fixed (``run()`` explicitly ``await``s ``gen.aclose()`` in its
    own ``finally`` + ``clear_audit_scope``'s foreign-Context fallback), both pass: no
    exception is ever raised, and the checkpoint finalizes exactly once.
    """
    import backend.ai.checkpoint as ckpt_mod

    checkpoint_calls: list[tuple] = []

    def fake_begin_turn(session_id, anchor_history_len, message_preview, kind="turn"):
        return object(), object()

    def fake_end_turn(ctx, token):
        checkpoint_calls.append((ctx, token))
        return {"id": "cp-abandon-test"}

    monkeypatch.setattr(ckpt_mod, "begin_turn", fake_begin_turn)
    monkeypatch.setattr(ckpt_mod, "end_turn", fake_end_turn)

    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "_save_session", lambda *a, **k: None)

    class _ParkingProvider:
        """Yields exactly one event, then hangs — so the turn is genuinely mid-flight
        (suspended inside ``provider.stream_chat``'s own body) when the test aborts it,
        matching a real client-disconnect-mid-stream shape."""

        async def stream_chat(self, *a, **kw):
            # A whole line: the live stream holds a short unfinished one back until it ends.
            yield {"type": "text_delta", "text": "hi\n"}
            await asyncio.Event().wait()  # never resolves within this test
            yield {"type": "done", "stop_reason": "end_turn"}  # pragma: no cover

    monkeypatch.setattr(assistant, "get_provider", lambda *a, **kw: _ParkingProvider())

    sid = "test-conc-abandon-session"
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)

    caught_exceptions: list[dict] = []

    def _exc_handler(loop, context):
        caught_exceptions.append(context)

    # Captured INSIDE _go()'s own (never-forked) Context, before any outside cleanup —
    # checking audit_context.get_audit_scope() from OUTSIDE asyncio.run(_go()) would be
    # meaningless: this test's own belt-and-braces cleanup below also calls
    # clear_audit_scope(), which would trivially make the assertion pass regardless of
    # whether the abandoned generator's OWN cleanup ever ran.
    observed: dict = {}

    async def _go():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_exc_handler)
        gen = assistant.run(
            session_id=sid,
            new_messages=[{"role": "user", "content": "hi"}],
            system="s",
        )
        first = await gen.__anext__()
        assert first["type"] == "text_delta"
        await gen.aclose()  # simulates the client-disconnect / early-close path
        # Pump the loop so asyncio's abandoned-async-generator finalizer (scheduled via
        # call_soon at GC time, which fires essentially immediately under CPython's
        # refcounting) actually runs before we assert.
        for _ in range(10):
            await asyncio.sleep(0)
        observed["scope_after"] = audit_context.get_audit_scope()

    try:
        asyncio.run(_go())
    finally:
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)
        audit_context.clear_audit_scope()
        _skill_scope.clear_scope()

    assert not caught_exceptions, (
        f"abandoning the generator raised/logged an unhandled exception: {caught_exceptions}"
    )
    assert observed.get("scope_after") is None, "audit scope was not cleared on abandonment"
    assert checkpoint_calls, "checkpoint finalize (end_turn) never ran on abandonment"


# ── Review round: finding 2 (CORE — fix is inert in production) ────────────────
# ── Review round: finding 3 (should-fix — session-lock leak) ───────────────────
def test_clear_session_removes_an_unheld_session_lock():
    """Finding 3: a scheduled/dispatched run mints ``session_id =
    f"scheduled-{uuid4()[:8]}"`` and ALWAYS calls ``clear_session()`` when done, but
    ``clear_session()`` never popped ``_SESSION_LOCKS`` — leaking one ``asyncio.Lock`` per
    run, forever, for the life of the process. An UNHELD lock (the scheduled-runner case:
    its own turn's ``run()`` has already released it by the time ``clear_session`` runs)
    must still be popped — this is the leak-fix half of the narrowed condition."""
    sid = "test-clear-session-lock-leak"
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)

    async def _go():
        assistant._get_session_lock(sid)

    try:
        asyncio.run(_go())
        assert sid in assistant._SESSION_LOCKS

        assistant.clear_session(sid)

        assert sid not in assistant._SESSION_LOCKS, "clear_session() leaked the session lock"
    finally:
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)


def test_clear_session_does_not_pop_a_held_session_lock():
    """Finding 3 (should-fix): the naive fix — an unconditional pop — reopens CONC-H3.
    ``clear_session`` is reachable with no happens-before against an in-flight ``run()``
    on the SAME session (``DELETE /api/chat/{id}``, a connector's ``reset_session``): if a
    turn is still mid-flight and holding the lock when a concurrent caller clears the
    session, popping it would let a concurrent retry/duplicate-submit ``run(session_id)``
    mint a fresh, UNLOCKED lock for the same id — two turns mutating the shared history
    concurrently. A HELD lock must therefore be left in place."""
    sid = "test-clear-session-held-lock"
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)

    async def _go():
        lock = assistant._get_session_lock(sid)
        async with lock:
            assert lock.locked()
            assistant.clear_session(sid)
            assert sid in assistant._SESSION_LOCKS, (
                "clear_session() popped a lock a concurrent turn is still holding"
            )
            assert assistant._SESSION_LOCKS[sid] is lock, (
                "clear_session() left a different lock behind (or replaced it)"
            )

    try:
        asyncio.run(_go())
    finally:
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)


# ── Review round: finding 4 (should-fix — auto-apply grant nesting, WALKED BACK) ──
