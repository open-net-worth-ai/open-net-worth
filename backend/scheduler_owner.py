"""Which process runs the periodic jobs: the one holding ``scheduler.lock``.

Under ``uvicorn --workers`` every worker runs the lifespan, but price checks, RSS polls and
Automations must fire ONCE, so the first worker to take an advisory lock owns the scheduler.

A worker that loses the race keeps trying in the background instead of giving up for good.
Live incident 2026-11-16: a restart booted the new server while the old one was still shutting
down and holding the lock; the new server skipped the scheduler, the old one exited two seconds
later, and no Automation ran all morning — nothing held the lock and nothing asked for it again.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import IO

from backend.core.file_lock import lock_exclusive

_log = logging.getLogger("backend")

RETRY_SECONDS = 5.0


class SchedulerOwnership:
    """Holds the scheduler lock (its fd lives as long as this object) and runs ``on_acquired``."""

    def __init__(
        self,
        lock_path: Path,
        on_acquired: Callable[[], None],
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self._lock_path = lock_path
        self._on_acquired = on_acquired
        self._retry_seconds = retry_seconds
        self._handle: IO | None = None
        self._retry_task: asyncio.Task | None = None

    @property
    def owned(self) -> bool:
        return self._handle is not None

    def try_acquire(self) -> bool:
        if self._handle is not None:
            return True
        handle = open(self._lock_path, "a+", encoding="utf-8")  # noqa: SIM115 — held open
        try:
            lock_exclusive(handle)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        self._on_acquired()
        return True

    def claim(self) -> bool:
        """Take the lock now, or keep retrying in the background until it is free."""
        if self.try_acquire():
            _log.info("scheduler: started (this worker owns the scheduler lock)")
            return True
        _log.info(
            "scheduler: another worker owns it — periodic jobs skipped in this worker; "
            "retrying every %.0fs in case that owner is shutting down",
            self._retry_seconds,
        )
        self._retry_task = asyncio.create_task(self._retry(), name="scheduler-lock-retry")
        return False

    async def _retry(self) -> None:
        while True:
            await asyncio.sleep(self._retry_seconds)
            try:
                if self.try_acquire():
                    _log.info("scheduler: started (lock freed by the previous owner)")
                    return
            except Exception:
                _log.exception("scheduler: lock retry failed (will try again)")

    async def stop_retrying(self) -> None:
        """Call first on shutdown, so a late acquire cannot start jobs mid-teardown."""
        if self._retry_task is not None:
            self._retry_task.cancel()
            await asyncio.gather(self._retry_task, return_exceptions=True)
            self._retry_task = None

    async def release(self) -> None:
        await self.stop_retrying()
        if self._handle is not None:
            self._handle.close()
            self._handle = None
