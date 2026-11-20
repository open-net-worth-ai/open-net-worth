"""Spawn a child as the root of its own process tree, and stop the whole tree.

POSIX gives the child a new session and signals its process group. Windows has no process
groups in that sense: the child is created in a new console-less group and its tree is
stopped with ``taskkill /T``, which walks the parent/child links the OS keeps.

Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Any

IS_WINDOWS = os.name == "nt"

_TASKKILL_TIMEOUT_SECONDS = 10


def spawn_kwargs() -> dict[str, Any]:
    """``Popen`` / ``create_subprocess_exec`` keywords that make the child a tree root."""
    if IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags}
    return {"start_new_session": True}


def _taskkill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_TASKKILL_TIMEOUT_SECONDS,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )


def terminate_tree(pid: int) -> None:
    """Ask the tree to stop (SIGTERM). Windows cannot ask a console-less child nicely, so
    it stops it. Raises ``OSError`` on POSIX if the group cannot be signalled."""
    if IS_WINDOWS:
        _taskkill(pid)
        return
    os.killpg(os.getpgid(pid), signal.SIGTERM)


def kill_tree(pid: int) -> None:
    """Stop the tree unconditionally. Raises ``OSError`` on POSIX if the group cannot be
    signalled."""
    if IS_WINDOWS:
        _taskkill(pid)
        return
    os.killpg(os.getpgid(pid), signal.SIGKILL)


def group_of(pid: int) -> int:
    """The process group a freshly spawned tree root leads — read it ONCE, at spawn, while the
    root is certainly alive, so later signals never depend on a pid that may have been reused.
    Windows has no groups; the pid stands for the tree."""
    return pid if IS_WINDOWS else os.getpgid(pid)


def hangup_group(pgid: int) -> None:
    """Hang up (SIGHUP) a recorded group, as a closed terminal does: an interactive shell passes it on to its jobs. Windows has no hang-up, so it stops the tree."""
    if IS_WINDOWS:
        _taskkill(pgid)
        return
    os.killpg(pgid, signal.SIGHUP)


def kill_group(pgid: int) -> None:
    """Stop a recorded group unconditionally."""
    if IS_WINDOWS:
        _taskkill(pgid)
        return
    os.killpg(pgid, signal.SIGKILL)


def has_exited(pid: int, poll: Any) -> bool:
    """Whether the tree root has exited, WITHOUT reaping it where the OS allows. A zombie root
    keeps its group id reserved, so signalling the group before the reap can never hit an
    unrelated group that inherited the number. ``poll`` is the Popen's ``poll`` (reaps)."""
    if IS_WINDOWS or not hasattr(os, "waitid"):
        return poll() is not None
    try:
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True


async def kill_and_reap(proc: Any, timeout: float = 5.0) -> None:
    """Best-effort: stop an ``asyncio`` child's whole tree, then reap it. ``proc`` must have been
    spawned with ``spawn_kwargs()`` — on Windows an npm CLI is a ``.cmd`` launcher whose real
    program is a grandchild that killing only the launcher leaves running (holding its cwd).
    Awaiting ``wait()`` matters: left to the GC the transport is torn down after the loop may
    have closed."""
    if proc.returncode is not None:
        # Already exited and reaped: its pid may now name an unrelated process (Windows reuses
        # pids fast), and a tree kill by that pid would take down whatever holds it.
        return
    try:
        await asyncio.to_thread(kill_tree, proc.pid)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            return
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except (asyncio.TimeoutError, OSError):
        pass
