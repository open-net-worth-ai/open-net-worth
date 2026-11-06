"""Headless-turn seam (RFC P2-SK2 §B.4).

``services`` must not import ``ai`` (import-linter Contract 1). But a service sometimes needs
to run one LLM turn — ``services.self_harness`` distils a routine suggestion that way, and it
is live, reached from ``services/automations/executors.py``. So the runner is inverted through
this leaf seam, exactly like ``core.channels`` (notify) and ``core.io`` (the checkpoint write
hook): ``ai/`` registers a runner at import time and the service invokes it via ``get_runner()``.

A runner is an **async** callable
``(prompt, *, allowed_tools=None, model=None, lean_prompt=False, enforce_offer=False,
effort="medium") -> str``
returning the assistant's text output.

* ``allowed_tools`` is the EXPLICIT tool offer. ``None`` = the ordinary interactive offer;
  ``[]`` (what self-harness passes) = offer NOTHING, a pure completion. ``None`` and ``[]``
  mean different things and must never be collapsed with ``or``.
* ``enforce_offer=True`` makes that offer a real ceiling; without it an offer is advisory and a
  call outside it still dispatches. Required for a turn that reads untrusted text unattended.
* ``model=None`` resolves to the instance default inside the runner.
* ``lean_prompt=True`` selects the short system prompt instead of the full conversational
  one — right for a zero-tool completion that only has to return text.

If no runner is registered (a unit test importing ``services`` without ``ai``, or an install
with the assistant disabled), ``get_runner()`` returns ``None`` and the caller records a
graceful skip rather than raising.

This module was ``core/agent_runner.py`` until 2026-08-02. Agents are retired; the seam is not
agent machinery and never was — only its busiest caller had been one.

This module imports nothing internal so ``core`` stays a leaf.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Iterable, Optional

# An async runner: (prompt, *, allowed_tools, model, lean_prompt, enforce_offer, effort) -> text.
HeadlessRunner = Callable[..., Awaitable[str]]

# A per-caller tool scope (a list/set/frozenset of tool names), or None for the
# default headless ceiling. Typed loosely so core stays free of ai/ symbols.
ToolScope = Optional[Iterable[str]]

_runner: Optional[HeadlessRunner] = None


def register_runner(fn: Optional[HeadlessRunner]) -> None:
    """Install (or clear, with ``None``) the headless-turn runner. Last registration wins."""
    global _runner
    _runner = fn


def get_runner() -> Optional[HeadlessRunner]:
    """The registered headless-turn runner, or ``None`` if none was installed."""
    return _runner
