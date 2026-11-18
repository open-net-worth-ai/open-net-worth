"""A worker that loses the scheduler lock takes it over once the owner lets go.

Live incident 2026-11-16: a restart booted the new server while the old one still held
scheduler.lock; the new one skipped the scheduler for good, the old one exited two seconds
later, and no Automation ran all morning.
"""

from __future__ import annotations

import asyncio

from backend.scheduler_owner import SchedulerOwnership


def _ownership(path, started: list[str], name: str) -> SchedulerOwnership:
    return SchedulerOwnership(path, lambda: started.append(name), retry_seconds=0.01)


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


def test_the_first_claim_owns_the_scheduler(tmp_path):
    async def scenario():
        started: list[str] = []
        owner = _ownership(tmp_path / "scheduler.lock", started, "old")
        assert owner.claim() is True
        assert started == ["old"]
        await owner.release()

    asyncio.run(scenario())


def test_a_restart_that_overlaps_the_old_owner_still_gets_a_scheduler(tmp_path):
    async def scenario():
        lock = tmp_path / "scheduler.lock"
        started: list[str] = []
        old = _ownership(lock, started, "old")
        new = _ownership(lock, started, "new")
        assert old.claim() is True
        assert new.claim() is False
        await asyncio.sleep(0.05)
        assert started == ["old"], "only one worker may run the jobs while the lock is held"

        await old.release()
        await _wait_for(lambda: new.owned)
        assert started == ["old", "new"]
        await new.release()

    asyncio.run(scenario())


def test_shutdown_stops_the_retry_so_a_closing_worker_never_starts_jobs(tmp_path):
    async def scenario():
        lock = tmp_path / "scheduler.lock"
        started: list[str] = []
        old = _ownership(lock, started, "old")
        closing = _ownership(lock, started, "closing")
        old.claim()
        closing.claim()
        await closing.stop_retrying()
        await old.release()
        await asyncio.sleep(0.05)
        assert started == ["old"]
        assert not closing.owned

    asyncio.run(scenario())
