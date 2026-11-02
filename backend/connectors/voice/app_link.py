"""The voice worker process's line back to the app (runs in the WORKER process).

The worker owns the call — LiveKit, the front voice — and the app owns everything else: the
call registry, Lucius's turns, the transcript. A job asks the app for one of the operations
``host.CALLS`` / ``host.STREAMS`` name, over the worker's stdin/stdout as JSON lines, and
gets the answer back (or a stream of events). Jobs run on event loops of their own, in threads
of their own, so each request remembers its loop and its reply is handed to that loop.

The app going away (its end of the pipe closes) ends this process: a worker with no app
behind it must not keep answering calls.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any, BinaryIO

from backend.connectors.voice.events import DeskEvent

log = logging.getLogger(__name__)


class AppError(RuntimeError):
    """The app refused or failed an operation."""


class AppLink:
    def __init__(
        self,
        rx: BinaryIO,
        tx: BinaryIO,
        *,
        on_stop: Callable[[], None],
        on_app_gone: Callable[[], None] = lambda: os._exit(0),
    ) -> None:
        self._rx, self._tx = rx, tx
        self._on_app_gone = on_app_gone
        self._send_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._pending_lock = threading.Lock()
        self._on_stop = on_stop
        threading.Thread(target=self._read, name="voice-app-link", daemon=True).start()

    def send(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
        with self._send_lock:
            self._tx.write(line)
            self._tx.flush()

    async def call(self, method: str, **args: Any) -> Any:
        rid, queue = self._open()
        self.send({"id": rid, "op": "call", "method": method, "args": args})
        try:
            msg = await queue.get()
        finally:
            self._close(rid)
        if "error" in msg:
            raise AppError(msg["error"])
        return msg.get("result")

    async def stream(self, method: str, **args: Any) -> AsyncIterator[DeskEvent]:
        rid, queue = self._open()
        self.send({"id": rid, "op": "stream", "method": method, "args": args})
        finished = False
        try:
            while True:
                msg = await queue.get()
                if "error" in msg:
                    finished = True
                    raise AppError(msg["error"])
                if msg.get("end"):
                    finished = True
                    return
                yield DeskEvent(**msg["item"])
        finally:
            self._close(rid)
            if not finished:  # the caller hung up: stop the work on the app's side too
                self.send({"id": rid, "op": "cancel"})

    def _open(self) -> tuple[int, asyncio.Queue]:
        rid = next(self._ids)
        queue: asyncio.Queue = asyncio.Queue()
        with self._pending_lock:
            self._pending[rid] = (asyncio.get_running_loop(), queue)
        return rid, queue

    def _close(self, rid: int) -> None:
        with self._pending_lock:
            self._pending.pop(rid, None)

    def _read(self) -> None:
        for line in self._rx:
            try:
                msg = json.loads(line)
            except ValueError:
                log.warning("voice: an unreadable line from the app was ignored")
                continue
            if msg.get("op") == "stop":
                self._on_stop()
                continue
            with self._pending_lock:
                target = self._pending.get(msg.get("id"))
            if target is not None:
                loop, queue = target
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, msg)
                except RuntimeError:  # that job's loop already closed
                    pass
        log.info("voice: the app closed the link; the worker exits")
        self._on_app_gone()
