"""Voice-call caps for a PUBLIC demo (``DEMO_MODE``).

A call spends the demo's LiveKit inference — speech-to-text, the front voice, text-to-speech —
by the MINUTE, whether or not Lucius is asked anything, and the token budgets in
``demo_quota`` see none of it (one person's testing used ~100 call-minutes of a project's
credit, 2026-11-03). So calls have caps of their own, each closing a hole the others leave:

* a longest call (``demo_call_max_s``) — one forgotten tab cannot run all day;
* calls and call-minutes per visitor per rolling day — one visitor cannot take the whole budget;
* call-minutes across all visitors per day — the only number that bounds what the demo costs;
* calls at once — a crowd cannot run the bill up faster than the day's minutes allow.

**A call reserves its whole limit while it is on.** Each call is given its own limit at start
— the shortest of the longest call, the visitor's minutes left and the demo's minutes left —
and counts that whole limit until it ends, then only what it really used. So two calls at once
can never add up past an allowance, and a call whose end is never reported (a crashed worker)
counts as having run to its limit. A minted call the worker never picks up within
``CLAIM_WINDOW_S`` counts one call and no minutes.

Keyed by the visitor's network address (``demo_visitors.client_key``), like the token budgets:
a cookie is cleared in a click. In-memory, like ``demo_quota``: a restart forgets the day.
Inert unless ``settings.demo_mode``.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from backend.core.config import get_settings

_DAY_SECONDS = 24 * 60 * 60
# A minted call the worker hasn't joined by then never will: on a demo the app registers the
# room for only this long, so the slot it reserved is free again soon after.
CLAIM_WINDOW_S = 60
# Less time than this left is not worth a call: refused instead of a call that hangs up at once.
MIN_CALL_S = 60
# The app's backstop after a call's limit (``connectors.voice.service``): what a call may still
# do once the worker should have hung up — say goodbye and have it saved.
GRACE_S = 20

_lock = threading.Lock()
_clock: Callable[[], float] = time.monotonic


@dataclass
class _Call:
    visitor: str
    started: float
    limit_s: int
    claimed: bool = False
    ended: Optional[float] = None

    def stops_at(self) -> float:
        if self.ended is not None:
            return self.ended
        return self.started + (self.limit_s if self.claimed else CLAIM_WINDOW_S)

    def live(self, now: float) -> bool:
        return now < self.stops_at()

    def charged_s(self, now: float) -> float:
        """Seconds this call counts against the caps: its whole limit while it is on."""
        if self.live(now):
            return float(self.limit_s)
        if not self.claimed:
            return 0.0
        return max(0.0, min(self.stops_at() - self.started, float(self.limit_s)))


_calls: dict[str, _Call] = {}  # room → call


@dataclass(frozen=True)
class Admission:
    """May this visitor start a call, and for how long (``None`` = no limit: not a demo)."""

    allowed: bool
    reason: str = ""
    retry_after: int = 0
    limit_s: Optional[int] = None


def enabled() -> bool:
    return bool(get_settings().demo_mode)


def _refuse(reason: str, retry_after: float) -> Admission:
    return Admission(allowed=False, reason=reason, retry_after=max(30, int(retry_after)))


def _prune(now: float) -> None:
    for room in [r for r, c in _calls.items() if c.started <= now - _DAY_SECONDS]:
        del _calls[room]


def _admit(visitor: str, now: float) -> Admission:
    cfg = get_settings()
    calls = list(_calls.values())
    live = [c for c in calls if c.live(now)]
    if len(live) >= cfg.demo_calls_concurrent:
        return _refuse(
            "Lucius is on as many demo calls as he takes at once. Try again in a few minutes.",
            min(c.stops_at() for c in live) - now,
        )
    mine = [c for c in calls if c.visitor == visitor]
    if len(mine) >= cfg.demo_calls_per_visitor_daily:
        return _refuse(
            f"You've made this demo's {cfg.demo_calls_per_visitor_daily} calls for today. "
            "They reset on a rolling 24-hour window — or run your own copy, with no limit.",
            min(c.started for c in mine) + _DAY_SECONDS - now,
        )
    mine_left = cfg.demo_call_minutes_per_visitor_daily * 60 - sum(c.charged_s(now) for c in mine)
    if mine_left < MIN_CALL_S:
        return _refuse(
            f"You've used this demo's {cfg.demo_call_minutes_per_visitor_daily} call minutes "
            "for today. They reset on a rolling 24-hour window — or run your own copy.",
            min(c.started for c in mine) + _DAY_SECONDS - now if mine else _DAY_SECONDS,
        )
    total_left = cfg.demo_call_minutes_total_daily * 60 - sum(c.charged_s(now) for c in calls)
    if total_left < MIN_CALL_S:
        return _refuse(
            "This demo has used its call minutes for today across all visitors. Chat with "
            "Lucius still works — only calls are paused.",
            min(c.started for c in calls) + _DAY_SECONDS - now if calls else _DAY_SECONDS,
        )
    return Admission(allowed=True, limit_s=int(min(cfg.demo_call_max_s, mine_left, total_left)))


def visitor_key(address: str) -> str:
    """One visitor per IPv4 address, and per IPv6 /64 — a home or VM gets a whole /64, so a key
    per IPv6 address would be a fresh allowance per address (security review, 2026-11-03)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def start(room: str, visitor: str) -> Admission:
    """Admit a call into ``room`` for ``visitor`` (their address) and record its start — one
    step, so two calls started at the same moment cannot both take the last slot. Always allows
    off a demo."""
    if not enabled():
        return Admission(allowed=True)
    visitor = visitor_key(visitor)
    with _lock:
        now = _clock()
        _prune(now)
        admission = _admit(visitor, now)
        if admission.allowed:
            _calls[room] = _Call(visitor=visitor, started=now, limit_s=admission.limit_s or 0)
        return admission


def claim(room: str) -> Optional[float]:
    """The worker picked the call up: the seconds it may still run (``None`` = no limit, not a
    demo). ``0`` refuses it — a room never admitted here, or one whose window has passed."""
    if not enabled():
        return None
    with _lock:
        call = _calls.get(room)
        now = _clock()
        if call is None or call.claimed or not call.live(now):
            return 0.0
        call.claimed = True
        return max(0.0, call.stops_at() - now)


def remaining(room: str) -> Optional[float]:
    """Seconds left on a call (``None`` off a demo; ``0`` once it is over or unknown)."""
    if not enabled():
        return None
    with _lock:
        call = _calls.get(room)
        now = _clock()
        return max(0.0, call.stops_at() - now) if call is not None and call.live(now) else 0.0


def over(room: str, *, grace_s: float = 0.0) -> bool:
    """Has this call ended, or run ``grace_s`` past its limit? Never, off a demo."""
    if not enabled():
        return False
    with _lock:
        call = _calls.get(room)
        return call is None or not call.live(_clock() - grace_s) or call.ended is not None


def ended(room: str) -> bool:
    """Was this call's end reported (by the worker, or the app closing it)? Unlike ``over``, a
    call past its limit that nobody ended is not."""
    with _lock:
        call = _calls.get(room)
        return call is not None and call.ended is not None


def end(room: str) -> None:
    """The call ended (the worker said so, or the app closed it): it counts what it used."""
    with _lock:
        call = _calls.get(room)
        if call is not None and call.ended is None:
            call.ended = min(_clock(), call.stops_at())


def snapshot() -> dict:
    """What the caps hold — diagnostics and tests, never a user surface."""
    with _lock:
        now = _clock()
        return {
            "live": sum(1 for c in _calls.values() if c.live(now)),
            "seconds_by_visitor": {
                v: sum(c.charged_s(now) for c in _calls.values() if c.visitor == v)
                for v in {c.visitor for c in _calls.values()}
            },
            "seconds_total": sum(c.charged_s(now) for c in _calls.values()),
        }


def reset(clock: Callable[[], float] | None = None) -> None:
    """Drop all state (tests / restart). ``clock`` injects a fake clock."""
    global _clock
    with _lock:
        _calls.clear()
    _clock = clock or time.monotonic
