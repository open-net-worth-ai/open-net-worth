"""Ask the open app what is on screen right now, and wait briefly for the answer.

A typed message carries a screen snapshot; a voice call's turn has none, and no turn can look
again once it has started. ``request_snapshot`` asks over ``GET /api/events``: every open app
hears it, each one that has this conversation open and is in view answers on
``POST /api/screen-reads/{id}``, and the device the owner is actually on wins.

Which device: an answer carries a rank (``RANK_*``). The device running the call ends the wait at
once; otherwise the first answer opens a short settle window, so a focused laptop on a slower link
still beats an idle tablet that answered first. Rank-0 answers ("the page is covered") open no
window, because a device in view may still be on its way. A device whose owner turned sharing off
answers at its full rank, so it wins as a refusal and no other device is read in its place.

In-process and ephemeral, like ``core.data_events``: nothing is stored, a request lives only
while the turn waits on it, and its id is single-use and unguessable.
"""

from __future__ import annotations

import asyncio
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from backend.core import data_events
from backend.models.screen import ScreenSnapshot

REPLY_TIMEOUT_S = 3.0
SETTLE_S = 0.25
MAX_READS_PER_TURN = 3
_MAX_REPLIES = 16

STATUS_OK = "ok"
STATUS_SHARING_OFF = "sharing_off"
STATUS_NO_SCREEN = "no_screen"

RANK_COVERED = 0
RANK_VISIBLE = 1
RANK_FOCUSED = 2
RANK_CALL = 3


@dataclass(frozen=True)
class ScreenReply:
    status: str
    rank: int = RANK_COVERED
    snapshot: Optional[ScreenSnapshot] = None


@dataclass
class _Pending:
    loop: asyncio.AbstractEventLoop
    wake: asyncio.Event
    replies: list[ScreenReply]


_pending: dict[str, _Pending] = {}
# Reads used this turn. Counted up from 0, so a path that never reset it is still bounded.
_reads_used: ContextVar[int] = ContextVar("screen_reads_used", default=0)


def reset_read_budget() -> None:
    """Called once at turn start."""
    _reads_used.set(0)


def consume_read() -> bool:
    used = _reads_used.get()
    if used >= MAX_READS_PER_TURN:
        return False
    _reads_used.set(used + 1)
    return True


def _best(replies: list[ScreenReply]) -> Optional[ScreenReply]:
    if not replies:
        return None
    return max(
        replies, key=lambda r: (r.rank, r.status == STATUS_SHARING_OFF, r.status == STATUS_OK)
    )


async def _collect(pending: _Pending) -> Optional[ScreenReply]:
    loop = pending.loop
    deadline = loop.time() + REPLY_TIMEOUT_S
    settle_at: Optional[float] = None
    while True:
        best = _best(pending.replies)
        if best is not None and best.rank >= RANK_CALL:
            return best
        if settle_at is None and best is not None and best.rank > RANK_COVERED:
            settle_at = min(deadline, loop.time() + SETTLE_S)
        remaining = (settle_at if settle_at is not None else deadline) - loop.time()
        if remaining <= 0:
            return best
        pending.wake.clear()
        try:
            await asyncio.wait_for(pending.wake.wait(), remaining)
        except asyncio.TimeoutError:
            return _best(pending.replies)


async def request_snapshot(session_id: str) -> Optional[ScreenReply]:
    """The winning answer from the apps showing ``session_id``; ``None`` when none answered."""
    request_id = secrets.token_urlsafe(24)
    pending = _Pending(asyncio.get_running_loop(), asyncio.Event(), [])
    _pending[request_id] = pending
    try:
        data_events.publish_request(
            {"type": "screen_request", "request_id": request_id, "session_id": session_id}
        )
        return await _collect(pending)
    finally:
        _pending.pop(request_id, None)


def submit_reply(request_id: str, reply: ScreenReply) -> bool:
    """Record one app's answer. ``False`` for an unknown or finished request."""
    pending = _pending.get(request_id)
    if pending is None or len(pending.replies) >= _MAX_REPLIES:
        return False
    if reply.status == STATUS_OK and reply.snapshot is None:
        reply = ScreenReply(STATUS_NO_SCREEN)
    pending.replies.append(reply)
    pending.loop.call_soon_threadsafe(pending.wake.set)
    return True
