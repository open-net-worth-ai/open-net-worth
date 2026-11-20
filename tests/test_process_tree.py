"""core/process_tree.py — a child is the root of a tree, and the whole tree stops with it."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from backend.core import process_tree
from backend.core.process_tree import kill_tree, spawn_kwargs, terminate_tree

_PARENT = """
import subprocess, sys, time
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(grandchild.pid, flush=True)
time.sleep(120)
"""


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        )
        return str(pid) in listing.stdout
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until_gone(pid: int, seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("stop", [kill_tree, terminate_tree], ids=["kill", "terminate"])
def test_stopping_the_tree_takes_the_grandchildren_with_it(stop):
    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT],
        stdout=subprocess.PIPE,
        text=True,
        **spawn_kwargs(),
    )
    try:
        grandchild = int(parent.stdout.readline())
        assert _alive(grandchild)
        stop(parent.pid)
        parent.wait(timeout=15)
        assert _wait_until_gone(grandchild), "the grandchild outlived its tree"
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.stdout.close()


def test_a_child_is_not_in_this_processes_group():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], **spawn_kwargs()
    )
    try:
        if sys.platform != "win32":
            import os

            assert os.getpgid(child.pid) == child.pid != os.getpgid(0)
    finally:
        child.kill()
        child.wait(timeout=10)


def test_kill_and_reap_never_tree_kills_a_pid_that_already_exited(monkeypatch):
    """A reaped child's pid may already name another process (Windows reuses pids fast); a
    tree kill by it would take that process down. Only a live child is tree-killed."""
    import asyncio

    calls = []
    monkeypatch.setattr(process_tree, "kill_tree", lambda pid: calls.append(pid))

    class _Exited:
        pid = 4242
        returncode = 0

        def kill(self):
            calls.append("kill")

        async def wait(self):
            return 0

    asyncio.run(process_tree.kill_and_reap(_Exited()))

    assert calls == []
