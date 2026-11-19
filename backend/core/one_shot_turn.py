"""One-shot model turns: a caller that sends the whole transcript with every request.

Lucius keeps a CLI process (Claude Code, Antigravity) or a Codex thread alive across the rounds
of one turn, and a Codex turn PARKS at a tool call so the next round can answer it in-thread (ADR
2026-10-05-codex-turns-stay-open-across-tool-rounds). A caller that never runs the tool — the
TradingAgents bridge, which hands each call back to a program that re-sends its own transcript —
must get none of that: a fresh CLI turn per request, nothing left parked or pooled behind it.

``one_shot_turn()`` marks the current async task; the CLI transports' own "may I keep this open"
switches (``sessions_enabled`` / ``live_turns_enabled``) read ``one_shot_active()`` and say no.
Lives in ``core`` (a leaf) like ``cost_context``, so ``ai`` can import it from either side.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_one_shot: ContextVar[bool] = ContextVar("one_shot_turn", default=False)


def one_shot_active() -> bool:
    """Whether the current task asked for fresh, non-pooled, non-parking model turns."""
    return _one_shot.get()


@contextmanager
def one_shot_turn() -> Iterator[None]:
    token = _one_shot.set(True)
    try:
        yield
    finally:
        _one_shot.reset(token)
