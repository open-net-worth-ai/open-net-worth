"""A Codex that dies during start-up says HOW it died.

Scar (2026-11-11): a Docker install reported only "Codex app-server is not running" on every
turn. Codex had printed nothing, and no fresh container reproduced it, so the one fact that
would have named the cause — its exit code or signal — was the one we threw away.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from backend.ai.models import codex_app_server as cas
from backend.core.errors import ExternalServiceError

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake CLI is a POSIX script")


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [
        (None, "was still running but never answered"),
        (1, "exited with code 1"),
        (-9, "was stopped by SIGKILL (usually the system ran out of memory"),
        (137, "was stopped by SIGKILL (exit code 137; usually"),
        (139, "was stopped by SIGSEGV (exit code 139; Codex crashed"),
        (-15, "was stopped by SIGTERM"),
    ],
)
def test_describe_exit(returncode, expected):
    assert cas.describe_exit(returncode).startswith(expected)


def _fake_cli(tmp_path, body: str) -> str:
    """A `codex` that dies silently before answering `initialize`."""
    script = tmp_path / "die.py"
    script.write_text(body, encoding="utf-8")
    launcher = tmp_path / "codex"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n')
    launcher.chmod(0o755)
    return str(launcher)


async def _start(cli: str) -> str:
    client = cas.AppServerClient(cli)
    try:
        await client.ensure_started()
    except ExternalServiceError as exc:
        return str(exc)
    finally:
        await client.aclose()
    raise AssertionError("a dying Codex must not start")


@posix_only
def test_a_silent_exit_names_its_code(tmp_path):
    cli = _fake_cli(tmp_path, "import sys\nsys.exit(3)\n")
    message = asyncio.run(_start(cli))
    assert "did not complete its handshake" in message
    assert "Codex exited with code 3" in message


@posix_only
def test_a_kill_names_the_signal_and_the_likely_cause(tmp_path):
    cli = _fake_cli(tmp_path, "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
    message = asyncio.run(_start(cli))
    assert "Codex was stopped by SIGKILL" in message
    assert "ran out of memory" in message
