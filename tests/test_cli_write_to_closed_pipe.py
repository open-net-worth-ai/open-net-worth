"""A Claude Code CLI that goes away before its prompt is written fails the same way on uvloop.

uvicorn[standard] runs the server on uvloop, which reports a write to a child's closed stdin
as ``RuntimeError("unable to perform operation on <WriteUnixTransport closed=True …>; the
handler is closed")`` where stdlib asyncio raises an ``OSError``. Only the OSError was
handled, so on Linux installs the raw uvloop text reached the chat and the start retry never
ran (issue #131).
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from backend.ai.models import claude_cli_provider as ccp
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.ai.models.claude_live_session import LiveSession
from backend.core.errors import ExternalServiceError

uvloop = pytest.importorskip("uvloop")

# Alive, but its input is already gone: the liveness check passes and the write fails.
_CLOSES_STDIN = (
    "import os, sys, time; os.close(0); "
    "sys.stderr.write('Please run claude login\\n'); sys.stderr.flush(); time.sleep(5)"
)


# asyncio only: the server never runs on uvloop (SERVER_EVENT_LOOP, test_server_event_loop.py),
# and on Linux uvloop's child keeps a copy of the pipe open, so the write never fails there.
@pytest.mark.parametrize("loop", ["asyncio"])
def test_a_closed_input_is_an_external_service_error_with_the_clis_reason(tmp_path, loop):
    async def run():
        session = LiveSession([sys.executable, "-c", _CLOSES_STDIN], {}, str(tmp_path))
        await session.start()
        await asyncio.sleep(0.5)
        assert session.proc.returncode is None
        try:
            with pytest.raises(ExternalServiceError, match="closed its input") as info:
                for _ in range(5):  # stdlib asyncio may take one write to notice
                    await session.send("hello")
            assert "Please run claude login" in str(info.value)
        finally:
            await session.close()

    uvloop.run(run()) if loop == "uvloop" else asyncio.run(run())


@pytest.mark.parametrize("loop", ["asyncio", "uvloop"])
def test_a_one_shot_cli_that_exits_at_start_reports_its_exit(tmp_path, monkeypatch, loop):
    argv = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('Please run claude login\\n'); sys.exit(3)",
    ]
    provider = ClaudeCliProvider(api_key="")
    workdir = tmp_path / "one-shot"

    def make_workdir(**_kwargs):
        workdir.mkdir()
        return str(workdir)

    monkeypatch.setattr(ccp.tempfile, "mkdtemp", make_workdir)

    real_exec = asyncio.create_subprocess_exec

    async def exec_then_wait(*_args, **kwargs):
        proc = await real_exec(*argv, **kwargs)
        await proc.wait()
        await asyncio.sleep(0.1)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_then_wait)

    async def run():
        with pytest.raises(ExternalServiceError, match=r"exited with status 3.*claude login"):
            async for _ in provider._events(["claude"], "hello"):
                pass

    uvloop.run(run()) if loop == "uvloop" else asyncio.run(run())
    assert not workdir.exists()
