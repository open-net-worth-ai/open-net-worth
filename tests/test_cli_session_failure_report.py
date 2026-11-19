"""A Claude Code session that dies says HOW, in the error and in the server log (issue #131).

A Docker install's CLI stopped reading its input on the second message and printed nothing;
the container log held nothing either, and no other machine reproduced it. The report now
carries the one fact that names a silent death — its exit code or signal — plus whether the
session was fresh or reused, how long it lived, how much of the message got through, and the
CLI version it reported.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import pytest

from backend.ai.models import cli_live_session as cls
from backend.ai.models.claude_live_session import LiveSession
from backend.core.errors import ExternalServiceError

posix_only = pytest.mark.skipif(os.name == "nt", reason="the fake CLI kills itself with SIGKILL")

# Answers one turn like Claude Code (init event with its version, then a result), then dies
# by SIGKILL on the next message, printing nothing — the shape of the report.
_ONE_TURN_THEN_KILLED = """
import json, os, signal, sys
def out(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
sys.stdin.readline()
out({"type": "system", "subtype": "init", "claude_code_version": "9.9.9"})
out({"type": "result", "subtype": "success", "result": "OK"})
sys.stdin.readline()
os.kill(os.getpid(), signal.SIGKILL)
"""


async def _one_turn(session: LiveSession) -> None:
    await session.send("hello")
    async for _ in session.events():
        pass


@posix_only
def test_a_reused_session_killed_mid_message_says_so(tmp_path, caplog):
    async def run():
        session = LiveSession([sys.executable, "-c", _ONE_TURN_THEN_KILLED], {}, str(tmp_path))
        await session.start()
        try:
            await _one_turn(session)
            assert session.turns == 1 and session.cli_version == "9.9.9"
            big = "x" * 2_000_000  # more than a pipe holds, so the kill lands mid-write
            with pytest.raises(ExternalServiceError) as info:
                for _ in range(3):
                    await session.send(big)
                    await asyncio.sleep(0.3)
            return str(info.value)
        finally:
            await session.close()

    with caplog.at_level(logging.WARNING, logger=cls.__name__):
        message = asyncio.run(run())

    assert "was stopped by SIGKILL" in message
    assert "printed no reason" in message
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "was stopped by SIGKILL" in logged
    assert "reused turns=1" in logged
    assert "version=9.9.9" in logged


@posix_only
def test_a_session_that_dies_while_idle_is_logged_when_reaped(tmp_path, caplog):
    async def run_via_acquire():
        session = LiveSession([sys.executable, "-c", _ONE_TURN_THEN_KILLED], {}, str(tmp_path))
        await session.start()
        await _one_turn(session)
        pool = cls.SessionPool(lambda: 3600.0)
        pool.add(["claude"], session)
        session.proc.stdin.write(b"bye\n")
        await session.proc.wait()
        with caplog.at_level(logging.WARNING, logger=cls.__name__):
            fresh, *_ = await cls.acquire(
                pool,
                LiveSession,
                "onw-test-",
                [sys.executable, "-c", "import time; time.sleep(5)"],
                {},
                [{"role": "user", "content": "hi"}],
                key_argv=["other"],
            )
        await fresh.close()

    asyncio.run(run_via_acquire())
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ended while idle" in logged
    assert "was stopped by SIGKILL" in logged
    assert "reused turns=1" in logged
