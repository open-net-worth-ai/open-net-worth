"""Take-refresh seam: a finished TradingAgents run asks for Lucius's take to be rewritten.

The runner lives in ``ai``; the take's synthesis (and its on-demand trigger) lives in
``monitoring``, which reaches ``api`` — a direct import would break the dependency rule. So the
refresh is inverted through this leaf seam, like ``core.headless_turn``: ``main.py`` registers
``monitoring.analyst.refresh_take_for_run`` at boot and the runner calls ``request``.

``request`` never blocks and never raises: it schedules the registered coroutine as a tracked
background task (the model call must not run inside the run's own path) and drops the request when
nothing is registered or no loop is running. ``shutdown`` (app teardown) clears the refresher and
cancels what is still running, so no model call outlives the loop it was started on.

This module imports nothing internal so ``core`` stays a leaf.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

TakeRefresher = Callable[[str], Awaitable[object]]

_refresher: Optional[TakeRefresher] = None
_tasks: set[asyncio.Task] = set()


def register(fn: Optional[TakeRefresher]) -> None:
    """Install (or clear, with ``None``) the refresher. Last registration wins."""
    global _refresher
    _refresher = fn


async def _run(fn: TakeRefresher, run_id: str) -> None:
    try:
        await fn(run_id)
    except Exception:  # noqa: BLE001 — a stale take is regenerated on the next page open
        log.warning("take_refresh_failed run=%s", run_id)


def request(run_id: str) -> None:
    """Ask for the take on ``run_id``'s ticker to be refreshed with that finished run."""
    fn = _refresher
    if fn is None:
        return
    try:
        task = asyncio.get_running_loop().create_task(_run(fn, run_id))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def shutdown() -> None:
    """Stop accepting refreshes and cancel every one still in flight."""
    register(None)
    pending = list(_tasks)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
