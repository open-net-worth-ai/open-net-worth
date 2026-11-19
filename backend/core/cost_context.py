"""Cost-attribution surface seam.

Mirrors ``core.audit_context``: upper layers SET the surface before calling the
assistant, and ``_record_cost`` / ``cost_tracker.record`` READ it to stamp each
cost record.  Lives in ``core`` (a leaf) so it can be imported from ``ai``,
``api``, and ``connectors`` without creating a layering violation.

Surfaces:
  - ``'web'``              — SSE chat endpoint (api/chat.py)
  - ``'connector:<name>'`` — inbound connector turn (telegram / slack / discord / …)
  - ``'connector'``        — connector turn when channel name is unavailable
  - ``'scheduled'``        — scheduled prompt_lucius run (main.py runner)
  - ``None``               — unset (uninstrumented path); recorded as ``'chat'``
                             by the legacy _record_cost fallback.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional

_surface: ContextVar[Optional[str]] = ContextVar("cost_surface", default=None)


def set_cost_surface(surface: str) -> None:
    """Bind the cost-attribution surface for the current async task."""
    _surface.set(surface)


def get_cost_surface() -> Optional[str]:
    """The active surface, or ``None`` when no entrypoint set one."""
    return _surface.get()


def clear_cost_surface() -> None:
    """Reset the surface (call in a ``finally`` at the entrypoint)."""
    _surface.set(None)


@dataclass(frozen=True)
class CostAttribution:
    """How a caller that is not a chat turn wants its model calls recorded."""

    source: str
    surface: str
    session_id: str


_attribution: ContextVar[Optional[CostAttribution]] = ContextVar("cost_attribution", default=None)


def get_cost_attribution() -> Optional[CostAttribution]:
    """The attribution the current task runs under, or ``None`` for an ordinary call."""
    return _attribution.get()


@contextmanager
def cost_attribution(source: str, surface: str, session_id: str) -> Iterator[None]:
    """Stamp every cost record a provider writes ITSELF inside the block with these labels.

    A provider that records inside ``stream_chat`` (Antigravity) cannot be told apart from the
    chat turn's own record afterwards; a caller that records the call itself uses this only to
    keep a self-recording provider from writing a second, differently-labelled row."""
    token = _attribution.set(CostAttribution(source, surface, session_id))
    try:
        yield
    finally:
        _attribution.reset(token)
