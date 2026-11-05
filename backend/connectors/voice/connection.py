"""Whether one call's line is broken on this side, told to the app so the call screen can say so.

A wifi drop of a few seconds left the room reconnecting for twenty more, while the phone could
already reach the app again and showed a healthy call that heard nothing (owner, 2026-11-05).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)


class CallConnection:
    # Speech services retry within about two seconds and say nothing when one finally works, so
    # their trouble ends when they have gone this long without failing.
    FAILURE_HOLD_S = 5.0

    def __init__(self, report: Callable[[bool], Awaitable[None]]) -> None:
        self._report = report
        self._reconnecting = False
        self._failing = False
        self._told = False
        self._hold: Optional[asyncio.TimerHandle] = None
        self._sending: Optional[asyncio.Task] = None

    @property
    def trouble(self) -> bool:
        return self._reconnecting or self._failing

    def reconnecting(self, on: bool) -> None:
        self._reconnecting = on
        self._changed()

    def service_failed(self) -> None:
        self._failing = True
        if self._hold is not None:
            self._hold.cancel()
        self._hold = asyncio.get_running_loop().call_later(self.FAILURE_HOLD_S, self._failure_over)
        self._changed()

    def aclose(self) -> None:
        if self._hold is not None:
            self._hold.cancel()

    def _failure_over(self) -> None:
        self._failing = False
        self._changed()

    def _changed(self) -> None:
        if self._told != self.trouble and (self._sending is None or self._sending.done()):
            self._sending = asyncio.create_task(self._send())

    async def _send(self) -> None:
        # One at a time, so a quick on-then-off can't arrive at the app the wrong way round.
        while self._told != self.trouble:
            want = self.trouble
            log.info("voice: the call's connection is %s", "in trouble" if want else "back")
            try:
                await self._report(want)
            except Exception as exc:  # noqa: BLE001 — never let this end a call
                log.warning("voice: couldn't tell the app about the connection: %s", exc)
                return
            self._told = want
