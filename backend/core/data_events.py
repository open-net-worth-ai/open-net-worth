"""App-wide data-change broadcast — every tool write, whoever made it, reaches every open UI.

A chat turn already streams ``data_changed`` to the ONE browser tab driving it. A write made
anywhere else — inbound MCP (Claude Desktop), a Telegram turn, a scheduled Automation, a chat
in another tab — never reached an open screen, which kept showing the old numbers until a
reload. ``ai.tools.registry.dispatch`` (the seam every tool call crosses) publishes each
executed write's domains here; ``GET /api/events`` fans them out to every connected client.

In-process only: one uvicorn process owns the app (the scheduler already assumes it). Leaf
module — stdlib only — so ``ai`` publishes and ``api`` subscribes without importing each other.

A subscriber stream never ends on its own, and uvicorn's graceful shutdown waits for every
open response — so an idle stream would hang every restart. ``install_shutdown_watch`` chains
onto uvicorn's stop-signal handlers (SIGTERM/SIGINT, plus SIGBREAK on Windows) to raise
``closing()``, which each stream polls.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterable, Iterator

_QUEUE_MAX = 64

_lock = threading.Lock()
_subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
_seq = 0
_closing = threading.Event()
# uvicorn also handles Ctrl-Break on Windows, the only stop signal a console child can be sent there.
_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT) + (
    (signal.SIGBREAK,) if sys.platform == "win32" else ()
)


def publish(domains: Iterable[str]) -> None:
    """Fan one write's domains out to every subscriber. Never raises; safe from any thread."""
    names = sorted({str(d) for d in domains if d})
    if not names:
        return
    global _seq
    with _lock:
        _seq += 1
        event = {"domains": names, "seq": _seq, "at": time.time()}
        targets = list(_subscribers)
    for loop, queue in targets:
        try:
            loop.call_soon_threadsafe(_offer, queue, event)
        except RuntimeError:  # that subscriber's loop is closed — it is on its way out
            pass


def publish_request(event: dict) -> None:
    """Fan a request to every open app (``event["type"]`` names it; see ``screen_read``). Same
    stream, no seq: it is not a write, and an app that missed it simply doesn't answer."""
    with _lock:
        targets = list(_subscribers)
    for loop, queue in targets:
        try:
            loop.call_soon_threadsafe(_offer, queue, event)
        except RuntimeError:
            pass


def _offer(queue: asyncio.Queue, event: dict) -> None:
    # A client too slow to drain 64 events loses the OLDEST, never the newest: the newest is
    # the one that says what to refetch now, and any refetch covers everything older.
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(event)


@contextmanager
def subscribe() -> Iterator[asyncio.Queue]:
    """Register a queue on the RUNNING loop for the duration of the ``with`` block."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    entry = (loop, queue)
    with _lock:
        _subscribers.add(entry)
    try:
        yield queue
    finally:
        with _lock:
            _subscribers.discard(entry)


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def closing() -> bool:
    """True once the server has been asked to stop — open streams must end."""
    return _closing.is_set()


def mark_closing() -> None:
    _closing.set()


def install_shutdown_watch() -> None:
    """Chain onto the process's current stop-signal handlers (uvicorn's, when called from
    the app lifespan) so a stop request flips ``closing()`` before the server starts waiting
    on open connections. Main thread only — elsewhere the stream's own disconnect check and
    the lifespan's ``mark_closing`` are the fallbacks."""
    _closing.clear()
    if threading.current_thread() is not threading.main_thread():
        return
    for sig in _STOP_SIGNALS:
        previous = signal.getsignal(sig)
        if getattr(previous, "_onw_data_events", False):
            continue

        def _chained(signum, frame, _previous=previous):
            _closing.set()
            if callable(_previous):
                _previous(signum, frame)
            elif _previous != signal.SIG_IGN:
                signal.signal(signum, _previous or signal.SIG_DFL)
                signal.raise_signal(signum)

        _chained._onw_data_events = True  # type: ignore[attr-defined]
        signal.signal(sig, _chained)
