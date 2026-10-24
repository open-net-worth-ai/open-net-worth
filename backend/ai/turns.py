"""Server-owned turn lifetime — concurrent-conversations RFC Phase 3.

``POST /api/chat`` and ``POST /api/chat/{id}/resolve`` stop driving turns inside the
request. Each validates, then calls ``spawn()`` — synchronous, no ``await`` anywhere in
it — which opens the ``live_sessions`` episode, registers a supervised background task,
and returns a handle immediately, so the route can answer ``202`` before the turn has done
any work. The client attaches to the SAME ``GET /api/sessions/{id}/live`` endpoint
Automation runs already use. Interactive, resolve, and Automation-followup turns become one
lane with different initiators.

Layering: this is ``ai`` — one layer below ``api`` (which calls it) and one above
``services``/``db``/``core`` (contract 1: ``api -> ai -> services -> db -> core``). The
caller is ABOVE this module, so a direct import is legal in both directions and no
core-seam inversion (like ``core.automation_runner``, which exists for the OPPOSITE
direction: a lower layer reaching up into ``ai``) is needed here.

``THE BLOCKING DEFECT THIS MODULE EXISTS TO CLOSE`` (security review finding 1): the old
``event_stream()`` generator in ``api/chat.py`` called ``set_cost_surface("web")`` INSIDE
itself. ``contextvars`` are copied at ``asyncio.create_task`` time, so a task spawned from
the route handler's context would start with ``cost_surface`` still ``None`` — and
``ai.tools._confirm_gate.should_gate`` fails OPEN on an unrecognised surface by design (a
gate that cannot determine its context must never silently stop a working write path). With
the gate always armed that would mean every mutating tool, ``send_email``, and
``restore_checkpoint`` executing with no approval card. ``_drive`` below calls
``set_cost_surface`` as its FIRST statement — before the first ``await`` — precisely so
this can never happen. See ``tests/test_turn_supervisor.py``'s gate regression test, which
asserts on the gate (a ``proposed_change`` event + a ``PendingPlanStore`` entry), never on
the cost log — a cost row falls back to a legacy attribution and looks fine even when the
gate is wide open, which is exactly how this bug hides.

No new surface-fail-closed policy was added to ``can_render_card`` itself (a broader,
cross-cutting change affecting every headless caller — self_harness, capture, inbound MCP —
that legitimately runs with no surface at all; the unattended hardline path that once
covered them is gone). Closing this bug does not need that: every path that reaches
``_drive`` goes through ``spawn()`` or ``run_inline()``, whose ``cost_surface`` parameter is
a plain ``str`` (defaulting to ``"web"`` for ``spawn``, required for ``run_inline``) — there
is no way to reach a driven turn with an unset surface short of a future caller explicitly
passing ``cost_surface=None`` past the type checker.

``ONE LIFECYCLE, TWO ENTRY POINTS`` (ADR ``2026-08-06-one-turn-lifecycle-for-chat-and-
automations``): ``spawn`` runs a turn in a NEW task (``POST /api/chat``); ``run_inline``
runs the same ``_drive`` body in the CALLER's task, for an Automation run whose task
``services.automations.runs`` already owns. Both register here, so ``cancel`` and
``executing_turn_id`` see every turn in the process — which is what gives a run the Stop
button and mid-turn injection it could not have while it ran outside this registry. A new
turn initiator MUST use one of the two, never a bare ``asyncio.create_task`` around
``assistant.run``: that is what put the fail-open gate fix above in two places once already.
"""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterable, Callable, Optional

from backend.ai import assistant, live_sessions
from backend.core.automation_runner import stopped_summary_note
from backend.core.config import get_settings
from backend.core.conversation_approval import (
    ConversationApprovalContext,
    bind_conversation_approval,
    reset_conversation_approval,
)
from backend.core.cost_context import clear_cost_surface, set_cost_surface
from backend.core.turn_cancel import record_cancel_reason
from backend.db import app_config_store
from backend.services import conversation_approval

logger = logging.getLogger(__name__)

MakeEvents = Callable[[str], AsyncIterable[dict]]
ErrorEvent = Callable[[BaseException], dict]


@dataclass
class TurnHandle:
    turn_id: str
    session_id: str
    kind: str  # "chat" | "resolve" | "automation_followup"
    started_at: str  # ISO-8601 UTC, for the wire / logs
    started_monotonic: float  # loop.time() unit, for duration logging
    deadline_at: float  # loop.time() unit — the time limit out from spawn(); inf = no limit
    limit_s: float  # the time limit this turn started under, in seconds; 0 = no limit
    # Set immediately after construction, still inside spawn()'s synchronous body — see
    # spawn()'s comment. Never None once spawn() has returned.
    task: "Optional[asyncio.Task[None]]" = None
    cancel_reason: Optional[str] = None  # "user_stop" | "timeout" | "shutdown"


# turn_id -> handle (authority). session_id -> turn_ids in spawn order (a lookup, not the
# key: POST /{id}/stop names a session while every event names a turn, and two resolve taps
# on sibling approval cards legitimately produce two turns on one session).
_turns: dict[str, TurnHandle] = {}
_by_session: dict[str, list[str]] = {}

_inline = False


def set_inline_for_tests(value: bool) -> None:
    """Make ``spawn`` drive its turn to completion before returning (test-only seam).

    The 22-odd existing tests that POST to ``/api/chat`` and assert on ``assistant.run``'s
    captured arguments or the resulting session state are about request -> argument
    shaping, not about the concurrent shape Phase 3 adds. Under this flag the caller (the
    route) awaits the spawned task before writing its response, so those tests stay
    hermetic and deterministic. OFF by default, and never used by this module's own tests
    of the concurrent shape itself (attach-mid-turn, cancel-mid-tool-call, two concurrent
    approves), which need the real interleaving.
    """
    global _inline
    _inline = value


def inline_for_tests() -> bool:
    return _inline


def _turn_id() -> str:
    return f"turn_{secrets.token_hex(6)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open(session_id: str, kind: str) -> TurnHandle:
    """Mint the turn id, open the live episode, build the handle. Synchronous throughout —
    that is what makes ``spawn``'s race-freedom argument hold: a client calling ``GET
    /live`` the instant it reads the 202 can never find the session not-live.
    """
    turn_id = _turn_id()
    started_at = _now_iso()
    live_sessions.begin(session_id, turn_id)
    live_sessions.publish(
        session_id,
        {
            "type": "turn_started",
            "session_id": session_id,
            "turn_id": turn_id,
            "kind": kind,
            "started_at": started_at,
        },
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        live_sessions.end(session_id, turn_id)
        raise
    limit_s = _turn_limit_s()
    return TurnHandle(
        turn_id=turn_id,
        session_id=session_id,
        kind=kind,
        started_at=started_at,
        started_monotonic=loop.time(),
        deadline_at=loop.time() + limit_s if limit_s > 0 else math.inf,
        limit_s=limit_s,
    )


def _register(handle: TurnHandle) -> None:
    """Publish the handle to the registry ``cancel``/``executing_turn_id`` read. Called only
    once ``handle.task`` is set, so nothing can observe a turn it cannot cancel. The
    ``running`` stamp is what makes boot recovery meaningful: a process that dies before
    ``_drive``'s terminal stamp leaves it behind for ``recover_interrupted_turns``.
    """
    _turns[handle.turn_id] = handle
    _by_session.setdefault(handle.session_id, []).append(handle.turn_id)
    assistant.upsert_session_meta(handle.session_id, last_turn_status="running")


def spawn(
    session_id: str,
    kind: str,
    make_events: MakeEvents,
    *,
    error_event: ErrorEvent,
    cost_surface: str = "web",
) -> TurnHandle:
    """Register and launch one turn in a NEW task. Synchronous — no ``await`` anywhere in
    this function body — so the live episode is open and the task is registered BEFORE the
    caller can write its 202 response.

    ``make_events`` is called with the turn_id THIS call mints, not a fresh one of its own
    — ``assistant.run`` / ``resolve_pending_tool_use`` already mint and stamp their own
    ``turn_id`` on every event (Phase 2), so the caller's closure must thread this id
    through (``turn_id=turn_id``) rather than let a second, disagreeing id get minted
    deeper. Two id namespaces for one turn would be a genuine bug — a watcher matching
    events against the id in this handle / the 202 body would never see them.

    The sibling entry point is ``run_inline``, for a caller that already owns a task.
    """
    handle = _open(session_id, kind)
    # `_drive(handle, ...)` only CONSTRUCTS the coroutine here — its body does not start
    # running until the loop schedules it, which is after `handle.task` is set below. That
    # is what lets `_drive` safely read `handle.task`/`handle.cancel_reason` even though the
    # handle did not have a task yet at the moment this coroutine object was created.
    coro = _drive(handle, make_events, error_event, cost_surface)
    try:
        task = asyncio.create_task(coro, name=f"lucius-turn:{session_id[:8]}:{handle.turn_id}")
    except RuntimeError:
        coro.close()
        live_sessions.end(session_id, handle.turn_id)
        raise
    handle.task = task
    _register(handle)
    task.add_done_callback(lambda _t, h=handle: _unregister(h))
    logger.info("turn_spawned turn=%s session=%s kind=%s", handle.turn_id, session_id[:8], kind)
    return handle


async def run_inline(
    session_id: str,
    kind: str,
    make_events: MakeEvents,
    *,
    error_event: ErrorEvent,
    cost_surface: str,
) -> str:
    """Drive one turn in the CALLER's task — the same ``_drive`` body ``spawn`` runs.

    An Automation run's task is created and tracked by ``services.automations.runs``, which
    also owns the durable run row and the runtime claim and must keep owning them. So it
    cannot be handed to ``spawn``; this lets that existing task BE the turn instead of
    running a second, unregistered lifecycle beside it.

    Returns the terminal status ``_drive`` journaled, so the caller can settle its own
    durable record from it. ``CancelledError`` propagates instead — after ``_drive`` has
    journaled the turn — so the task's owner still observes a cancelled task.
    """
    handle = _open(session_id, kind)
    handle.task = asyncio.current_task()
    _register(handle)
    logger.info("turn_inline turn=%s session=%s kind=%s", handle.turn_id, session_id[:8], kind)
    return await _drive(handle, make_events, error_event, cost_surface)


def executing_turn_id(session_id: str) -> Optional[str]:
    """The id of the turn CURRENTLY registered as running on this session, or ``None``.

    The routing authority for mid-turn injection (security review finding 7a): a caller
    asks THIS — never ``live_sessions.is_live()`` — to decide "splice into the running
    turn" vs "spawn a fresh one". ``live_sessions`` also reports live-ish state around a
    turn that just PARKED for the user's own approval decision (see
    ``assistant.has_pending_approval``'s docstring); routing on that would inject into a
    turn with nothing left to drain it, which is exactly the black hole finding 7a warns
    about. This dict is popped by ``_unregister`` (a ``task.add_done_callback``) the
    instant the turn's task finishes, so presence here is a live, synchronous fact — safe
    to check with no ``await`` between the check and whatever the caller does next (see
    ``pending_injection``'s module docstring for why that matters for race-freedom).

    A session can have more than one turn id queued (a resolve-continuation behind an
    executing chat turn) — this names the one at the FRONT, i.e. the one actually
    running right now, matching ``cancel()``'s own "no explicit turn_id" default.
    """
    ids = _by_session.get(session_id)
    return ids[0] if ids else None


def _unregister(handle: TurnHandle) -> None:
    _turns.pop(handle.turn_id, None)
    ids = _by_session.get(handle.session_id)
    if ids is None:
        return
    if handle.turn_id in ids:
        ids.remove(handle.turn_id)
    if not ids:
        _by_session.pop(handle.session_id, None)


def _log_long_running(handle: TurnHandle) -> None:
    logger.warning(
        "turn_long_running turn=%s session=%s kind=%s",
        handle.turn_id,
        handle.session_id[:8],
        handle.kind,
    )


def _turn_limit_s() -> float:
    return app_config_store.turn_time_limit_s()


def _limit_phrase(limit_s: float) -> str:
    minutes = round(limit_s / 60)
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}-hour"
    return f"{max(minutes, 1)}-minute"


def _cancelled_event(handle: TurnHandle) -> dict:
    return {
        "type": "error",
        "session_id": handle.session_id,
        "turn_id": handle.turn_id,
        "code": "turn_time_limit",
        "message": (
            f"This turn hit its {_limit_phrase(handle.limit_s)} time limit and was stopped. To let it "
            "finish, pick a faster model (for an Automation, set its own Model) or raise the "
            "Turn time limit in Settings → AI Models. Nothing was left half-applied — check "
            "the Undo chip on the turn."
        ),
    }


async def _drive(
    handle: TurnHandle,
    make_events: MakeEvents,
    error_event: ErrorEvent,
    cost_surface: str,
) -> str:
    """The task body. Returns the terminal status it journaled; raises on cancellation.

    ``set_cost_surface`` is the FIRST statement — see the module docstring. Every gated
    write this turn makes depends on that line running before the first tool call, which is
    why it is not merely early, it is first: there is no ``await`` before it, so nothing
    this task does can observe a ``None`` surface.

    The returned status is for ``run_inline``'s caller, which has its own durable record to
    settle (an Automation run row) and must not have to re-derive the outcome — notably
    ``"timeout"``, which is reported without raising and would otherwise read as success.
    ``spawn`` discards it: a detached turn's only record IS the meta stamp made below.
    """
    set_cost_surface(cost_surface)
    # Bound HERE, beside the cost surface, and for the same structural reason: this is the
    # task body, so the value is set inside the task before anything can observe it. Binding
    # it in `spawn` would work by contextvars inheritance but would also leave it set on the
    # REQUEST's context, where a later turn on another session could read a stale mode.
    #
    # Bound for EVERY turn, including an Automation run: `should_gate` consults the
    # Automation's verdict first and only falls through to this one when there is none, so a
    # run's own authority still speaks alone. Failing to read it must never fail the turn —
    # `get_mode` swallows and returns `manual`, the mode that asks.
    approval_token = bind_conversation_approval(
        ConversationApprovalContext(
            session_id=handle.session_id,
            mode=conversation_approval.get_mode(handle.session_id),
        )
    )
    loop = asyncio.get_running_loop()
    # With no limit, "long" still means past the default limit's halfway point.
    warn_after = handle.limit_s or get_settings().turn_timeout_s
    warn_delay = max(0.0, handle.started_monotonic + warn_after / 2 - loop.time())
    warn_timer = loop.call_later(warn_delay, _log_long_running, handle)
    status = "ok"
    # A turn can fail WITHOUT raising: assistant.run maps a provider/tool failure into an
    # `error` event and then completes normally.
    saw_error_event = False
    # The error TEXT, not just the fact of one. The live stream carries it to whoever is
    # watching, and the client attaches it to the open assistant bubble — but that is
    # in-memory only, so a reader who was not watching sees nothing. That is every
    # Automation conversation, which is only ever opened after the fact: the run card said
    # "API Error: 400" while the conversation beside it rendered empty (owner, 2026-08-16).
    # Stamped onto the session meta below so `rehydrate_messages` can rebuild it.
    error_message: Optional[str] = None
    try:
        events = make_events(handle.turn_id)

        async def _pump() -> None:
            nonlocal saw_error_event, error_message
            try:
                async for event in events:
                    if event.get("type") == "error":
                        saw_error_event = True
                        error_message = str(event.get("message") or "") or error_message
                    live_sessions.publish(handle.session_id, event)
            finally:
                aclose = getattr(events, "aclose", None)
                if aclose is not None:
                    await aclose()

        remaining = None if handle.limit_s <= 0 else max(0.0, handle.deadline_at - loop.time())
        try:
            await asyncio.wait_for(_pump(), timeout=remaining)
        except asyncio.TimeoutError:
            # wait_for already cancelled `_pump` (and everything under it, including the
            # in-flight assistant.run()/_run_turn() generator) internally — the dangling-
            # tool-use heal already ran on THAT path. This is the outer, turn-level report.
            status = "timeout"
            handle.cancel_reason = "timeout"
            _timeout_event = _cancelled_event(handle)
            error_message = str(_timeout_event.get("message") or "") or error_message
            live_sessions.publish(handle.session_id, _timeout_event)
        else:
            if assistant.has_pending_approval(handle.session_id):
                status = "awaiting_approval"
            elif saw_error_event:
                status = "error"
            else:
                status = "ok"
    except asyncio.CancelledError:
        # turns.cancel() (a user Stop, or shutdown()) cancelled this task directly — the
        # timeout path above never reaches here (asyncio.wait_for converts ITS OWN timeout
        # into TimeoutError to this coroutine; only an externally-cancelled task sees a
        # CancelledError at the `await wait_for(...)` itself). No synthetic error event —
        # a deliberate Stop is not a failure, and today's pre-Phase-3 Stop (dropping the
        # request) never showed one either; `live_sessions.end()`'s `stream_end` alone
        # tells watchers nothing more is coming, exactly as it always has.
        status = "cancelled"
        # No synthetic error EVENT (see above) — but the conversation still has to be able
        # to say what happened when it is opened later. Without this the transcript ends on
        # whatever tool chips the turn got through and nothing else, so opening an
        # Automation's conversation from the sidebar showed a wall of tool pills and no
        # explanation at all (owner, 2026-08-22). `rehydrate_messages` renders this as a
        # NOTICE, not an error: being cancelled is not a failure.
        error_message = stopped_summary_note(handle.cancel_reason, subject="turn")
        logger.info(
            "turn_cancel_requested turn=%s session=%s reason=%s",
            handle.turn_id,
            handle.session_id[:8],
            handle.cancel_reason or "unknown",
        )
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to a friendly event, never re-raised
        status = "error"
        logger.warning(
            "turn_failed turn=%s session=%s %s: %.200s",
            handle.turn_id,
            handle.session_id[:8],
            type(exc).__name__,
            str(exc),
        )
        _raised_event = error_event(exc)
        error_message = str(_raised_event.get("message") or "") or error_message
        live_sessions.publish(
            handle.session_id,
            {**_raised_event, "session_id": handle.session_id, "turn_id": handle.turn_id},
        )
    finally:
        warn_timer.cancel()
        clear_cost_surface()
        reset_conversation_approval(approval_token)
        live_sessions.end(handle.session_id, handle.turn_id)
        _unregister(handle)
        # Always passed alongside the status, never only on failure: a later successful
        # turn has to CLEAR a stale message, or the conversation keeps rendering an error
        # it already recovered from.
        assistant.upsert_session_meta(
            handle.session_id,
            last_turn_status=status,
            last_turn_error=error_message or "",
        )
        logger.info(
            "turn_%s turn=%s session=%s kind=%s dur=%.1fs",
            status,
            handle.turn_id,
            handle.session_id[:8],
            handle.kind,
            loop.time() - handle.started_monotonic,
        )
    return status


def _arm_cancel(handle: TurnHandle, reason: str) -> None:
    """Set the reason on the handle AND publish it to the ``core`` seam, in that order.

    Both, always — the handle serves this process's own logging, the seam serves the
    ``services`` code that settles the durable row and cannot import this module. Recorded
    BEFORE ``task.cancel()`` fires so the reason is already readable by the time any
    ``CancelledError`` handler downstream looks for it.

    The timeout path deliberately does not come through here: ``wait_for`` converts its own
    timeout in-band, so the task is never cancelled and the run settles as ``timeout``
    through the normal return path, with nothing for the seam to explain.
    """
    handle.cancel_reason = reason
    record_cancel_reason(handle.session_id, reason)


def cancel(session_id: str, *, turn_id: Optional[str] = None, reason: str = "user_stop") -> dict:
    """Cancel one turn. Never a 404/409 — a stale or absent ``turn_id`` is a truthful no-op.

    ``turn_id`` given: cancel exactly that turn (used by a resolve-continuation Stop, or a
    client that knows which of several queued turns on this session it means). No
    ``turn_id``: cancel only the EXECUTING turn (``_by_session[sid][0]``) — a queued sibling
    approval-resolution is not collateral damage.
    """
    if turn_id is not None:
        handle = _turns.get(turn_id)
        if (
            handle is None
            or handle.session_id != session_id
            or handle.task is None
            or handle.task.done()
        ):
            return {"stopped": False, "reason": "not_running"}
        _arm_cancel(handle, reason)
        handle.task.cancel()
        return {"stopped": True, "turn_id": turn_id}

    turn_ids = _by_session.get(session_id) or []
    if not turn_ids:
        return {"stopped": False, "reason": "not_running"}
    executing_id = turn_ids[0]
    handle = _turns.get(executing_id)
    if handle is None or handle.task is None or handle.task.done():
        return {"stopped": False, "reason": "not_running"}
    _arm_cancel(handle, reason)
    handle.task.cancel()
    return {"stopped": True, "turn_id": executing_id}


def cancel_session(session_id: str, *, reason: str = "conversation_deleted") -> dict:
    """Cancel EVERY turn for this session — executing and any queued sibling.

    Distinct from ``cancel()``, which deliberately spares a queued sibling because a Stop
    tap means "stop what I can see". Deleting the conversation is the opposite: the thing
    those turns are writing into is going away, so none of them may survive it. A turn that
    outlives its conversation still holds the same ``history`` list, and its next
    ``_save_session`` RESURRECTS the transcript file the delete just removed (B6).
    """
    turn_ids = list(_by_session.get(session_id) or [])
    stopped = []
    for turn_id in turn_ids:
        handle = _turns.get(turn_id)
        if handle is None or handle.task is None or handle.task.done():
            continue
        _arm_cancel(handle, reason)
        handle.task.cancel()
        stopped.append(turn_id)
    if stopped:
        logger.info("turns: cancelled %d turn(s) for deleted session %s", len(stopped), session_id)
    return {"stopped": bool(stopped), "turn_ids": stopped}


def recover_interrupted_turns() -> dict:
    """Boot recovery, modelled on ``services.automations.runs.recover_automation_runs``.

    A ``last_turn_status == "running"`` row in a FRESH process is definitionally stale — the
    registry above is empty at this point in boot, so no live task can own it; only a crash
    (SIGKILL, an OOM kill) leaves that status behind, since a clean ``shutdown()`` rewrites
    every in-flight turn to ``"cancelled"`` first. Transcripts are untouched here — the
    dangling tool_use a crash-orphaned turn left heals lazily on the next
    ``get_or_create_session`` disk load, the same existing, tested path a plain crash
    already exercises.
    """
    recovered = 0
    for row in assistant.list_sessions():
        if row.get("last_turn_status") != "running":
            continue
        assistant.upsert_session_meta(row["id"], last_turn_status="interrupted")
        recovered += 1
    if recovered:
        logger.info("turn_recovered_at_boot count=%d", recovered)
    return {"recovered": recovered}


async def shutdown() -> None:
    """Cancel every registered turn and wait for its journaling to land.

    Run from the lifespan's post-``yield`` block so a graceful restart records
    ``"cancelled"`` for every turn that was in flight — never leaving ``"running"`` behind
    for ``recover_interrupted_turns`` to correct next boot as ``"interrupted"``. A clean
    shutdown and a real crash must read differently to the user, and this is the line that
    keeps them apart. A hard SIGKILL never reaches this at all, which is exactly what
    ``recover_interrupted_turns`` exists for.
    """
    handles = list(_turns.values())
    for handle in handles:
        _arm_cancel(handle, "shutdown")
        if handle.task is not None and not handle.task.done():
            handle.task.cancel()
    tasks = [h.task for h in handles if h.task is not None]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def drain_for_tests(session_id: str, *, timeout: float = 2.0) -> bool:
    """Block (via a plain sleep loop) until ``session_id`` has no registered turn left.

    For the handful of tests that need the REAL concurrent shape from OUTSIDE the app's
    event loop — ``TestClient`` runs it in a worker thread, so a blocking poll from the
    test thread works (see ``spawn``'s module docstring) — rather than
    ``set_inline_for_tests``. Returns whether it drained before the timeout, so a test can
    fail with a clear message instead of a downstream assertion on incomplete state.

    NOT for a test that itself ``await``s the app's coroutines in-process (same event
    loop): a blocking ``time.sleep`` there starves the very task it is waiting on. Use
    ``adrain_for_tests`` instead in that shape of test.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _by_session.get(session_id):
            return True
        time.sleep(0.01)
    return not _by_session.get(session_id)


async def adrain_for_tests(session_id: str, *, timeout: float = 2.0) -> bool:
    """``drain_for_tests``, but ``await asyncio.sleep`` — for a test running IN-PROCESS on
    the same event loop as the turn it is waiting on (a plain ``time.sleep`` there would
    block that loop and the turn could never finish)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _by_session.get(session_id):
            return True
        await asyncio.sleep(0.01)
    return not _by_session.get(session_id)


__all__ = [
    "TurnHandle",
    "adrain_for_tests",
    "cancel",
    "drain_for_tests",
    "executing_turn_id",
    "inline_for_tests",
    "recover_interrupted_turns",
    "run_inline",
    "set_inline_for_tests",
    "shutdown",
    "spawn",
]
