"""Per-visitor AI quotas for a PUBLIC demo instance.

A demo whose provider actually works is an open door to somebody's account. The web chat
surface has no rate limit of its own — only the login route does — so without this, one
visitor with a loop, or one bored crowd, spends the owner's quota. Everything here is inert
unless ``settings.demo_mode`` is on, so a self-hoster is never throttled on their own laptop.

**Three limits, because each alone has an obvious hole.** A burst window stops one visitor
hammering Send but not a patient one; a per-IP daily token budget stops the patient one but
not fifty of them; the instance-wide daily ceiling is the only limit that actually bounds
what the demo can cost its owner, which is the number that matters.

**Tokens, not dollars.** A Claude Code CLI demo runs on a subscription, where the recorded
dollar figure is a computed LIST PRICE for usage already covered by the plan (see
``ai/cost_tracker.record``'s ``cost_basis="plan_included"``) — a spend cap would never bite.
Tokens are the real consumed quota on either billing model.

**How tokens are attributed.** They are only known after a turn runs, so this is charged
RETROACTIVELY: the chat route binds ``session_id → ip`` before spawning, and
``ai/cost_tracker.record`` reports each turn's usage back through ``note_usage``. That is an
``ai → services`` import, which the dependency rule allows. A turn already in flight is never
killed mid-way — the budget it overruns is enforced on the NEXT request, which is the honest
place to draw the line for a demo.

**Everything that spends is counted.** Tokens recorded by ANY model call land in the instance
ceiling — a call with no visitor behind it (a scheduled job, the app's own memory/title work)
goes to a shared unattributed bucket rather than nowhere — and ``assert_model_available`` is
checked where every provider is built (``ai.assistant``), so a route that was never listed
here still cannot spend past the budgets. The burst window is for deliberate actions only.

State is in-memory and resets on restart, matching ``core.ratelimit``'s own stated precedent.
A demo host restarts on the operator's schedule; persisting a visitor ledger would be a
database of IP addresses, which is a worse trade than a quota that occasionally resets.
"""

from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import dataclass
from typing import Callable

from backend.core.config import get_settings
from backend.core.errors import ConfigurationError
from backend.core.ratelimit import SlidingWindowLimiter

_DAY_SECONDS = 24 * 60 * 60

# session_id → the IP that started it. Bounded by _MAX_TRACKED so a long-lived demo cannot
# grow this without limit; the oldest bindings are dropped first (their turns are long done).
_MAX_TRACKED = 20_000

_lock = threading.Lock()
_session_ip: dict[str, str] = {}
_usage: dict[str, list[tuple[float, int]]] = {}  # ip → [(monotonic_ts, tokens)]
_clock: Callable[[], float] = time.monotonic
_burst: SlidingWindowLimiter | None = None

# Tokens spent with no visitor behind them. Counted toward the instance ceiling only; no visitor's
# per-IP check ever reads it ("(unattributed)" is not an address).
UNATTRIBUTED = "(unattributed)"
# The visitor whose request this task is serving, bound by the demo guard so a model call made
# on a visitor's behalf (a narration, a suggestion, a background task it spawned) is charged to them.
_current_visitor: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "demo_current_visitor", default=None
)


@dataclass(frozen=True)
class Verdict:
    """The answer to "may this visitor start a turn?"."""

    allowed: bool
    reason: str = ""
    retry_after: int = 0


ALLOWED = Verdict(allowed=True)


def enabled() -> bool:
    return bool(get_settings().demo_mode)


def _limiter() -> SlidingWindowLimiter:
    global _burst
    cfg = get_settings()
    if _burst is None:
        _burst = SlidingWindowLimiter(
            max_attempts=cfg.demo_turns_per_window,
            window_seconds=cfg.demo_turn_window_s,
            clock=_clock,
        )
    return _burst


def _prune(ip: str, now: float) -> list[tuple[float, int]]:
    cutoff = now - _DAY_SECONDS
    kept = [row for row in _usage.get(ip, ()) if row[0] > cutoff]
    if kept:
        _usage[ip] = kept
    else:
        _usage.pop(ip, None)
    return kept


def tokens_used(ip: str) -> int:
    """Tokens this IP has consumed in the last 24h."""
    with _lock:
        return sum(n for _, n in _prune(ip, _clock()))


def tokens_used_total() -> int:
    """Tokens consumed by EVERY visitor in the last 24h — the instance-wide figure."""
    with _lock:
        now = _clock()
        return sum(sum(n for _, n in _prune(ip, now)) for ip in list(_usage))


def check_tokens(ip: str | None) -> Verdict:
    """Is there token budget left for this visitor (``None`` = nobody in particular) and the demo?"""
    if not enabled():
        return ALLOWED
    cfg = get_settings()
    if ip is not None and tokens_used(ip) >= cfg.demo_tokens_per_ip_daily:
        return Verdict(
            allowed=False,
            reason=(
                "You've used this demo's daily allowance for one visitor. It resets on a "
                "rolling 24-hour window — or run your own copy, where there is no limit."
            ),
            retry_after=_DAY_SECONDS,
        )

    if tokens_used_total() >= cfg.demo_tokens_total_daily:
        return Verdict(
            allowed=False,
            reason=(
                "This demo has hit its daily AI budget across all visitors. "
                "Everything else still works — only the assistant is paused."
            ),
            retry_after=_DAY_SECONDS,
        )
    return ALLOWED


def check(ip: str) -> Verdict:
    """May this IP start a turn right now? Always allows when demo mode is off."""
    if not enabled():
        return ALLOWED

    allowed, retry_after = _limiter().check(ip)
    if not allowed:
        return Verdict(
            allowed=False,
            reason=(
                "You're sending messages faster than this demo allows. "
                "Give it a moment and try again."
            ),
            retry_after=retry_after,
        )
    return check_tokens(ip)


def bind_visitor(ip: str) -> None:
    """Attribute model calls made while serving this request to ``ip``."""
    _current_visitor.set(ip)


def bind_session(session_id: str) -> str | None:
    """Attribute model calls made from here on to whoever started ``session_id`` — for turns the
    app spawns with no request behind them (a voice call's, asked for by the voice worker).
    Returns that visitor's address; ``None`` off a demo or for a conversation nobody bound."""
    if not enabled():
        return None
    with _lock:
        ip = _session_ip.get(session_id)
    if ip is not None:
        _current_visitor.set(ip)
    return ip


def assert_model_available() -> None:
    """Refuse to build a model provider once the visitor or the demo is out of tokens.

    The one place EVERY model call passes through, whichever route, tool or background job
    started it. A no-op off a demo."""
    verdict = check_tokens(_current_visitor.get())
    if not verdict.allowed:
        raise ConfigurationError(verdict.reason)


def record_turn(ip: str, session_id: str = "") -> None:
    """Note that ``ip`` started a turn, and bind the session so its tokens come back here."""
    if not enabled():
        return
    _limiter().record(ip)
    if not session_id:
        return
    with _lock:
        if len(_session_ip) >= _MAX_TRACKED:
            for stale in list(_session_ip)[: _MAX_TRACKED // 4]:
                _session_ip.pop(stale, None)
        _session_ip[session_id] = ip


def note_usage(session_id: str, tokens: int) -> None:
    """Charge a finished call's tokens to whoever caused it.

    Called from ``ai/cost_tracker.record`` for EVERY recorded call, including the app's own
    background work (memory, titles, folds, scheduled jobs). The charge goes to the IP that
    started the session, else the visitor whose request this task is serving, else the
    unattributed bucket: it still spends the owner's quota, so it counts toward the instance
    ceiling, but no visitor's own budget is spent by the app talking to itself.
    """
    if not enabled() or tokens <= 0:
        return
    with _lock:
        ip = _session_ip.get(session_id) if session_id else None
        ip = ip or _current_visitor.get() or UNATTRIBUTED
        now = _clock()
        kept = _prune(ip, now)
        kept.append((now, int(tokens)))
        _usage[ip] = kept


def snapshot() -> dict:
    """What the quotas currently hold — for diagnostics and tests, never a user surface."""
    cfg = get_settings()
    with _lock:
        now = _clock()
        per_ip = {ip: sum(n for _, n in _prune(ip, now)) for ip in list(_usage)}
    return {
        "enabled": enabled(),
        "tracked_sessions": len(_session_ip),
        "tokens_by_ip": per_ip,
        "tokens_total": sum(per_ip.values()),
        "limits": {
            "turns_per_window": cfg.demo_turns_per_window,
            "window_seconds": cfg.demo_turn_window_s,
            "tokens_per_ip_daily": cfg.demo_tokens_per_ip_daily,
            "tokens_total_daily": cfg.demo_tokens_total_daily,
        },
    }


def reset(clock: Callable[[], float] | None = None) -> None:
    """Drop all state (tests / restart). ``clock`` injects a fake clock for window tests."""
    global _clock, _burst
    with _lock:
        _session_ip.clear()
        _usage.clear()
    _current_visitor.set(None)
    _clock = clock or time.monotonic
    _burst = None
