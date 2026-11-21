"""Per-tool-call telemetry recorder — the writer behind ``ToolCallLogStore``.

Hooked into ``registry.dispatch``, the ONE seam every internal tool call passes through
regardless of surface (interactive chat, agent runs, scheduled tasks, skills, inbound MCP,
connector confirms, REST apply paths). See ``record_call`` for the field contract and
``docs/adr/2026-07-19-per-tool-call-telemetry.md`` for why this seam and not the
assistant loop.

Three properties this module must never violate:

1. **It logs sizes and shapes, never content.** No argument values, no result bodies, no
   error messages. The app holds real financial records and this ring is long-lived and
   script-readable, so the safe default is that a leak is structurally impossible rather
   than merely redacted. ``backend/core/redact.py`` is available and is what we would route
   through if an argument value were ever logged; today nothing is, so it is unused here by
   design. The only free text stored is registry-derived (canonical tool name, alias) or a
   closed vocabulary (origin, error class).

2. **It never fails a tool call.** Every public entry point swallows everything, mirroring
   ``mcp/adapter._audit_write``. Losing a measurement is always better than breaking the
   thing being measured.

3. **It adds no latency to the hot path.** ``record_call`` appends to an in-memory deque
   and returns; a single daemon thread batches those into one store write every
   ``_FLUSH_INTERVAL_SEC``. A per-call read-modify-write of a 5,000-row JSON file would
   otherwise put ~10ms of fsync on every dispatch.
"""

from __future__ import annotations

import atexit
import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Flush cadence. The buffer drains when it reaches _FLUSH_THRESHOLD rows or when the
# flusher wakes, whichever comes first — so a burst of tool calls costs one file write and
# an idle process costs none.
_FLUSH_INTERVAL_SEC = 2.0
_FLUSH_THRESHOLD = 64

# Hard ceiling on the in-memory buffer, so a store that is failing to write (read-only
# disk, permissions) can never grow the process's memory without bound. The deque drops
# its OLDEST rows past this — telemetry is lossy under duress, by design.
_MAX_BUFFERED = 2000

_buffer: deque[dict] = deque(maxlen=_MAX_BUFFERED)
_buffer_lock = threading.Lock()
_wake = threading.Event()
_flusher: Optional[threading.Thread] = None
_flusher_lock = threading.Lock()

# ── Turn tracking ────────────────────────────────────────────────────────────────────
# A "turn" is one ``assistant.run`` invocation (one user message and the whole agentic
# loop it drives), which is the unit the questions are asked in: "median tool calls per
# turn", "which sequences are the long ones". ``assistant.run`` calls ``begin_turn`` once;
# everything else is derived here, so the assistant needs exactly one hook.
#
# Model ROUND-TRIPS per turn deliberately are NOT counted here: cost_log.json already
# instruments every API call, and joining the two by session id answers "4.1 API calls per
# turn" without a second assistant.py hook or a duplicated counter.
#
# A surface with no turn concept (inbound MCP, a connector confirm, a REST apply) never
# calls ``begin_turn`` and records turn_index=-1, call_index=0 — an honest "single call, no
# turn" rather than a fabricated one.
#
# KNOWN LIMITATION: a NESTED turn (a tool call that recurses into a fresh ``assistant.run``)
# opens a new turn ordinal, so calls the OUTER turn makes afterwards are attributed to the
# inner turn's index. In practice a nested run carries its own session id, so this only
# mis-groups if outer and inner share one. Grouping granularity is affected; totals and
# per-tool counts are not. Fixing it properly needs a snapshot/restore pair in
# ``assistant.run``'s ``finally`` (the ``_skill_scope.snapshot_scope`` pattern) — not worth
# a second hook into that file for the accuracy it buys.
_TURN_TRACKER_MAX = 256
_turn_counts: dict[str, int] = {}
_turn_state: dict[str, list[int]] = {}  # session_id -> [turn_index, next_call_index]
_turn_lock = threading.Lock()

_NO_TURN = ("", -1)


def begin_turn(session_id: Optional[str]) -> None:
    """Open a new turn for ``session_id`` (called once by ``assistant.run``). Never raises.

    Bumps that session's turn ordinal and resets its call counter. Sessions are tracked in
    a bounded dict (oldest evicted past ``_TURN_TRACKER_MAX``) so a long-lived process with
    many conversations cannot leak memory here.
    """
    try:
        if not session_id:
            return
        with _turn_lock:
            if len(_turn_counts) >= _TURN_TRACKER_MAX and session_id not in _turn_counts:
                oldest = next(iter(_turn_counts))
                _turn_counts.pop(oldest, None)
                _turn_state.pop(oldest, None)
            nxt = _turn_counts.get(session_id, -1) + 1
            _turn_counts[session_id] = nxt
            _turn_state[session_id] = [nxt, 0]
    except Exception:  # noqa: BLE001 — telemetry must never break a turn
        logger.debug("call_log: begin_turn failed", exc_info=True)


def _peek_position(session_id: Optional[str]) -> tuple[int, int]:
    """(turn_index, call_index) WITHOUT advancing the counter.

    Used by ``record_gate_event``: a discovery-gate row describes what happened *to* a tool
    call, not a call of its own. Advancing here would make the executed case consume two
    call slots and silently inflate "tool calls per turn" for every session that discovers
    a tool.
    """
    if not session_id:
        return _NO_TURN[1], 0
    with _turn_lock:
        state = _turn_state.get(session_id)
        if state is None:
            return _NO_TURN[1], 0
        return state[0], state[1]


def _next_position(session_id: Optional[str]) -> tuple[int, int]:
    """(turn_index, call_index) for the call about to be recorded, advancing the counter."""
    if not session_id:
        return _NO_TURN[1], 0
    with _turn_lock:
        state = _turn_state.get(session_id)
        if state is None:
            # A dispatch on a session whose turn was never opened (a direct in-process
            # caller). Treat it as a standalone call rather than inventing a turn.
            return _NO_TURN[1], 0
        turn_index, call_index = state[0], state[1]
        state[1] = call_index + 1
        return turn_index, call_index


# ── Origin resolution ────────────────────────────────────────────────────────────────
# Reuses the EXISTING provenance seam (``core.audit_context``) rather than adding a sixth
# trail: every entrypoint already sets an AuditScope surface before it writes, and those
# strings map cleanly onto the origin taxonomy. A path that sets no scope records
# "unknown" — surfaced as a real number in the report so the gap is measured, not hidden.


def _resolve_origin() -> str:
    """The calling surface, in the closed origin vocabulary. Never raises."""
    try:
        from backend.ai.tools import _skill_scope

        # Skill provenance is more specific than the surface hosting it, so it wins. Plain
        # inline invocations intentionally keep the established bare "skill" vocabulary; a
        # current bundled invocation can publish its id. Legacy callers that set only a
        # single bundle retain their existing skill:<id> attribution.
        current = _skill_scope.current_invocation_id()
        bundle_ids = _skill_scope.active_bundle_ids()
        if current:
            return f"skill:{current}" if current in bundle_ids else "skill"
        if len(bundle_ids) == 1:
            return f"skill:{bundle_ids[0]}"
    except Exception:  # noqa: BLE001
        pass

    try:
        from backend.core.audit_context import get_audit_scope

        scope = get_audit_scope()
        surface = (scope.surface if scope else "") or ""
    except Exception:  # noqa: BLE001
        surface = ""

    if not surface:
        return "unknown"
    if surface == "ui" or surface == "chat:web":
        return "web"
    if surface.startswith("chat:"):
        # Every non-web chat surface is a connector, derived from the surface string rather
        # than matched against a hardcoded channel list — so a newly added connector is
        # attributed correctly on day one instead of silently counting as "web".
        return f"connector:{surface.split(':', 1)[1]}"
    if surface.startswith("mcp"):
        return "mcp"
    if surface.startswith("agent:"):
        return surface  # already agent:<id>
    if surface.startswith("recurring:"):
        return "scheduled"
    if surface.startswith("import"):
        return "import"
    return "unknown"


def _resolve_session_id() -> Optional[str]:
    """The chat session / agent-run correlation id, or None. Never raises."""
    try:
        from backend.core.chat_session import get_session_id

        sid = get_session_id()
        if sid:
            return sid
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.core.audit_context import get_audit_scope

        scope = get_audit_scope()
        return scope.session_id if scope else None
    except Exception:  # noqa: BLE001
        return None


def _resolve_checkpoint_id(mutated: bool) -> Optional[str]:
    """The checkpoint this call's write is undoable from, or None. Never raises.

    Only meaningful for a mutating call: the id is allocated when the turn's
    CheckpointContext opens, and ``has_writes`` decides whether it will actually persist.
    """
    if not mutated:
        return None
    try:
        from backend.ai import checkpoint

        ctx = checkpoint.get_active()
        return ctx.id if ctx is not None else None
    except Exception:  # noqa: BLE001
        return None


def _input_chars(input_data) -> int:
    """Serialized SIZE of the arguments. The value itself is never read or stored."""
    try:
        return len(json.dumps(input_data, default=str))
    except Exception:  # noqa: BLE001
        try:
            return len(str(input_data))
        except Exception:  # noqa: BLE001
            return 0


def classify_error(text: str) -> Optional[str]:
    """The error class for a tool's text result, or None when it succeeded.

    Mirrors ``assistant._tool_error_message``'s detection (a structured
    ``{"error": …}`` body) but keeps only the CLASS — never the message, which can quote
    account names, balances, or payee strings. The message is still logged to ``app.log``
    by the assistant loop for live debugging; it just never enters this durable ring.
    """
    try:
        if not text or not text.lstrip().startswith("{"):
            return None
        parsed = json.loads(text)
        if not isinstance(parsed, dict) or parsed.get("error") in (None, "", False):
            return None
        message = str(parsed["error"])
        if message.startswith("unknown tool:"):
            return "unknown_tool"
        if message.startswith("invalid arguments for "):
            return "validation"
        return "tool_error"
    except Exception:  # noqa: BLE001
        return None


def record_call(
    *,
    tool: str,
    invoked: str,
    input_data,
    result_text: str,
    duration_ms: float,
    domains: tuple[str, ...],
    exception: Optional[BaseException] = None,
) -> None:
    """Buffer one telemetry row for a completed dispatch. Never raises, never blocks.

    ``tool`` is the CANONICAL name and ``invoked`` the name the caller actually used, so an
    alias is both resolved (for aggregation) and preserved (for "is this alias still load
    bearing?"). ``domains`` is the per-invocation ``mutates`` set the handler actually
    declared, not the static superset.
    """
    try:
        error_class = (
            f"exception:{type(exception).__name__}"
            if exception is not None
            else classify_error(result_text)
        )
        mutated = bool(domains)
        session_id = _resolve_session_id()
        turn_index, call_index = _next_position(session_id)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "alias_used": invoked if invoked != tool else None,
            "origin": _resolve_origin(),
            "session_id": session_id,
            "turn_index": turn_index,
            "call_index": call_index,
            "mutated": mutated,
            "domains": list(domains),
            "ok": error_class is None,
            "error_class": error_class,
            "duration_ms": duration_ms,
            "result_chars": len(result_text or ""),
            "input_chars": _input_chars(input_data),
            "checkpoint_id": _resolve_checkpoint_id(mutated),
        }
        with _buffer_lock:
            _buffer.append(row)
            pending = len(_buffer)
        _ensure_flusher()
        if pending >= _FLUSH_THRESHOLD:
            _wake.set()
    except Exception:  # noqa: BLE001 — telemetry must NEVER fail a tool call
        logger.debug("call_log: could not record call", exc_info=True)


def record_gate_event(*, tool: str, outcome: str, args_valid: Optional[bool]) -> None:
    """Buffer one INTERACTIVE-DISCOVERY-GATE row. Never raises, never blocks.

    Emitted by ``assistant``'s gate every time it auto-loads a tool the model called before
    loading — the event that was previously invisible. Each bounce is a wasted model round
    trip (the whole context re-sent to learn nothing), so this is the measurement that says
    whether hiding a tool from the default offer is paying for itself:

        rows where event="discovery_gate", grouped by tool and outcome

    Read it by DISTINCT SESSIONS, not by raw count — ``session_id`` is on every row for
    exactly this reason. A tool's place in the always-offered ``CORE_TOOL_NAMES`` is paid on
    every turn of every session, while a bounce is paid only when it happens: a tool that
    bounced 8 times in ONE session is a bursty re-call and belongs nowhere near CORE, while
    the same 8 bounces spread over 8 sessions is broad demand. Ranking by raw count conflates
    the two and argues for promotions that charge the 98% of sessions that never bounce
    (measured 2026-07-19, when ranking by count did exactly that). The cheap fix is almost
    always to make a bounce cost less, not to make it rarer.

    ``outcome`` is a closed vocabulary: ``executed`` (args validated against the freshly
    loaded schema and the call ran in ONE round trip), ``bounced_invalid_args``,
    ``bounced_write`` (a write or other side-effecting tool — never auto-executed on guessed
    args, whatever ``args_valid`` says) or ``bounced_external`` (external-MCP with no discovered
    ``inputSchema`` to check) or ``executed_external`` (an external-MCP read whose arguments
    validated against its discovered schema — it then goes through the normal permission-gated
    external dispatch, so "executed" means "allowed to proceed", not "fired"). ``args_valid`` is None when the arguments were never checked.

    The row rides the same ring and the same "sizes and shapes, never content" contract as a
    dispatch row: the tool NAME is registry-derived and the outcome is a closed vocabulary —
    no argument values, and deliberately not the validation MESSAGE (it quotes the model's
    own argument path). ``event`` distinguishes these from dispatch rows so no existing
    query double-counts: an ``executed`` gate event is followed by a real dispatch row for
    the same call.
    """
    try:
        session_id = _resolve_session_id()
        turn_index, call_index = _peek_position(session_id)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "discovery_gate",
            "tool": tool,
            "origin": _resolve_origin(),
            "session_id": session_id,
            "turn_index": turn_index,
            "call_index": call_index,
            "ok": outcome in ("executed", "executed_external"),
            "gate_outcome": outcome,
            "gate_args_valid": args_valid,
        }
        with _buffer_lock:
            _buffer.append(row)
            pending = len(_buffer)
        _ensure_flusher()
        if pending >= _FLUSH_THRESHOLD:
            _wake.set()
    except Exception:  # noqa: BLE001 — telemetry must NEVER fail a turn
        logger.debug("call_log: could not record gate event", exc_info=True)


# ── Flushing ─────────────────────────────────────────────────────────────────────────


def _drain() -> list[dict]:
    with _buffer_lock:
        rows = list(_buffer)
        _buffer.clear()
    return rows


def flush_now() -> int:
    """Write every buffered row to the store synchronously. Returns rows written.

    Public so tests and the report script get a deterministic view without racing the
    background thread. Never raises; rows are returned to the buffer on a failed write so
    a transient error does not silently discard measurements.
    """
    rows = _drain()
    if not rows:
        return 0
    try:
        from backend.db.tool_call_log_store import ToolCallLogStore

        return ToolCallLogStore().append_many(rows)
    except Exception:  # noqa: BLE001
        logger.debug("call_log: flush failed, re-buffering", exc_info=True)
        with _buffer_lock:
            _buffer.extendleft(reversed(rows))
        return 0


def _flush_loop() -> None:
    while True:
        _wake.wait(_FLUSH_INTERVAL_SEC)
        _wake.clear()
        try:
            flush_now()
        except Exception:  # noqa: BLE001 — the daemon must never die
            logger.debug("call_log: flush loop error", exc_info=True)


def _ensure_flusher() -> None:
    """Start the daemon flusher on first use (never at import — a module import must not
    spawn a thread in a script, a test collection, or a forked worker)."""
    global _flusher
    if _flusher is not None and _flusher.is_alive():
        return
    with _flusher_lock:
        if _flusher is not None and _flusher.is_alive():
            return
        thread = threading.Thread(target=_flush_loop, name="tool-call-telemetry", daemon=True)
        thread.start()
        _flusher = thread


atexit.register(flush_now)
