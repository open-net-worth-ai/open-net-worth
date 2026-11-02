"""Run the voice worker as a process of its own, and answer it (runs in the APP).

LiveKit's native layer once crashed (SIGSEGV) inside the app's own process and took the whole
app down with it (2026-11-03). So the worker — LiveKit, the front voice, every call — runs in a
child process, and the app answers it over the child's stdin/stdout (``app_link``): only the
operations named below, and Lucius's side only on a conversation the app itself handed that
worker for a call. A crash ends the calls, never the app; ``service`` restarts the worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, BinaryIO, Callable, Optional

from backend.core.child_env import scrubbed_environ
from backend.core.process_tree import kill_tree, spawn_kwargs

log = logging.getLogger(__name__)

CALLS = frozenset({"is_expected", "calls_allowed", "claim_call", "record_exchange"})
STREAMS = frozenset({"ask", "follow_next_turn"})
_WORKER_ARGV = (sys.executable, "-m", "backend.connectors.voice.worker")


class Host:
    """The app's side of one worker process's link."""

    def __init__(self, tx: BinaryIO, app_loop: asyncio.AbstractEventLoop) -> None:
        self._tx, self._loop = tx, app_loop
        self._send_lock = threading.Lock()
        self._tasks: dict[int, Any] = {}
        # Conversations the app handed this worker for a call (``claim_call``): Lucius's side
        # runs only on these, so a worker talked into anything else can't reach another one.
        self._sessions: set[str] = set()

    def send(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
        with self._send_lock:
            self._tx.write(line)
            self._tx.flush()

    def handle(self, msg: dict) -> None:
        """One request from the worker (called on the reader thread)."""
        rid = msg.get("id")
        if msg.get("op") == "cancel":
            task = self._tasks.pop(rid, None)
            if task is not None:
                task.cancel()
            return
        self._tasks[rid] = asyncio.run_coroutine_threadsafe(self._serve(msg), self._loop)

    async def _serve(self, msg: dict) -> None:
        rid, op, method = msg.get("id"), msg.get("op"), msg.get("method")
        args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
        try:
            if op == "call" and method in CALLS:
                self._reply({"id": rid, "result": await self._call(method, args)})
            elif op == "stream" and method in STREAMS:
                async for event in self._stream(method, args):
                    self._reply({"id": rid, "item": asdict(event)})
                self._reply({"id": rid, "end": True})
            else:
                self._reply({"id": rid, "error": "That isn't something the voice worker can ask."})
        except asyncio.CancelledError:
            pass
        except PermissionError as exc:
            log.warning("voice: refused a worker request: %s", exc)
            self._reply({"id": rid, "error": str(exc)})
        except Exception:  # noqa: BLE001 — one failed request must not end the call
            log.exception("voice: a worker request failed (%s)", method)
            self._reply({"id": rid, "error": "The app couldn't do that."})
        finally:
            self._tasks.pop(rid, None)

    def _reply(self, msg: dict) -> None:
        try:
            self.send(msg)
        except (OSError, ValueError):  # the worker is gone; its calls ended with it
            pass

    async def _call(self, method: str, args: dict) -> Any:
        from backend.connectors.voice import desk, service

        if method == "is_expected":
            return service.is_expected(str(args.get("room") or ""))
        if method == "calls_allowed":
            return service.calls_allowed()
        if method == "claim_call":
            session_id = service.claim_call(str(args.get("room") or ""))
            if session_id:
                self._sessions.add(session_id)
            return session_id
        await desk.record_exchange(
            self._owned(args), str(args.get("said") or ""), str(args.get("reply") or "")
        )
        return None

    def _stream(self, method: str, args: dict):
        from backend.connectors.voice import desk

        session_id = self._owned(args)
        if method == "ask":
            return desk.ask(
                session_id,
                str(args.get("said") or ""),
                understood=str(args.get("understood") or ""),
                model_ref=str(args.get("model_ref") or ""),
            )
        return desk.follow_next_turn(session_id)

    def _owned(self, args: dict) -> str:
        session_id = str(args.get("session_id") or "")
        if session_id not in self._sessions:
            raise PermissionError("not the conversation of a call this app started")
        return session_id


class WorkerProcess:
    """One worker process: start it, watch it, stop it. ``on_exit`` runs (on a background
    thread) when it ends without being asked to."""

    def __init__(
        self,
        app_loop: asyncio.AbstractEventLoop,
        on_exit: Callable[[Optional[int]], None],
        argv: Optional[tuple[str, ...]] = None,
    ) -> None:
        self._loop, self._on_exit, self._argv = app_loop, on_exit, argv or _WORKER_ARGV
        self._proc: Optional[subprocess.Popen] = None
        self._ready = threading.Event()
        self._stopping = False

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, init: dict, timeout: float = 60.0) -> bool:
        """Spawn it and hand it its settings (over the pipe, never argv or the environment);
        ``True`` once it says it is ready. Blocking: call it off the app loop."""
        self._proc = subprocess.Popen(
            list(self._argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=_worker_env(),
            **spawn_kwargs(),
        )
        host = Host(self._proc.stdin, self._loop)
        threading.Thread(
            target=self._read, args=(host,), name="voice-worker-link", daemon=True
        ).start()
        host.send({"op": "init", **init})
        return self._ready.wait(timeout) and self.alive()

    def _read(self, host: Host) -> None:
        proc = self._proc
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("op") == "ready":
                self._ready.set()
            elif isinstance(msg, dict):
                host.handle(msg)
        code = proc.wait()
        self._ready.set()
        if not self._stopping:
            log.error("voice: the worker process ended unexpectedly (exit code %s)", code)
            self._on_exit(code)

    def stop(self, timeout: float = 10.0) -> None:
        """Ask it to finish, and make sure it does. Blocking: call it off the app loop."""
        self._stopping = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(b'{"op":"stop"}\n')
            proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            proc.wait(5)


def _worker_env() -> dict[str, str]:
    """The child gets no app settings or logins from the environment — it is handed only its
    own (the LiveKit project's) over the pipe — just enough to find Python and the network."""
    env = scrubbed_environ()
    package_root = str(Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = package_root
    return env
