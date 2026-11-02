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
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, BinaryIO, Callable, Optional

from backend.core.child_env import scrubbed_environ
from backend.core.process_tree import kill_tree, spawn_kwargs

log = logging.getLogger(__name__)

CALLS = frozenset({"is_expected", "calls_allowed", "claim_call", "end_call", "record_exchange"})
STREAMS = frozenset({"ask", "follow_next_turn"})
_WORKER_ARGV = (sys.executable, "-m", "backend.connectors.voice.worker")
_PACKAGE_ROOT = str(Path(__file__).resolve().parents[3])
# The limits the APP holds, never trusting the worker for them: LiveKit's native layer parses
# remote media in that process and has crashed once, so it is the part assumed compromised
# (security review, 2026-11-03). The worker keeps its own, stricter checks too.
CALL_TTL_S = 2 * 3600  # a call's conversation stays reachable this long at most
MAX_ASKS_PER_MINUTE = 10  # the chat channels' per-sender ceiling
MAX_LINE = 1 << 20  # bytes; a longer line from the worker is a broken (or hostile) worker


class Host:
    """The app's side of one worker process's link."""

    def __init__(self, tx: BinaryIO, app_loop: asyncio.AbstractEventLoop) -> None:
        self._tx, self._loop = tx, app_loop
        self._send_lock = threading.Lock()
        self._tasks: dict[int, Any] = {}
        # Conversations the app handed this worker for a call (``claim_call``) → when: Lucius's
        # side runs only on these, until the call ends (``end_call``) or ``CALL_TTL_S`` passes.
        self._sessions: dict[str, float] = {}
        self._asks: dict[str, list[float]] = {}

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
                self._sessions[session_id] = time.monotonic()
            return session_id
        if method == "end_call":
            self._sessions.pop(str(args.get("session_id") or ""), None)
            return None
        await desk.record_exchange(
            self._owned(args), str(args.get("said") or ""), str(args.get("reply") or "")
        )
        return None

    def _stream(self, method: str, args: dict):
        from backend.connectors.voice import desk

        session_id = self._owned(args)
        if method == "ask":
            self._count_ask(session_id)
            return desk.ask(
                session_id,
                str(args.get("said") or ""),
                understood=str(args.get("understood") or ""),
                model_ref=_lucius_model(),
            )
        return desk.follow_next_turn(session_id)

    def _owned(self, args: dict) -> str:
        """The conversation a request names, if it is one this app handed this worker for a
        call that is still within its time — and only while voice calls are allowed."""
        from backend.connectors.voice import service

        session_id = str(args.get("session_id") or "")
        claimed = self._sessions.get(session_id)
        if claimed is None or time.monotonic() - claimed > CALL_TTL_S:
            self._sessions.pop(session_id, None)
            raise PermissionError("not the conversation of a call this app started")
        if not service.calls_allowed():
            raise PermissionError("voice calls are off")
        return session_id

    def _count_ask(self, session_id: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._asks.get(session_id, []) if now - t < 60]
        if len(recent) >= MAX_ASKS_PER_MINUTE:
            raise PermissionError("too many requests on this call")
        self._asks[session_id] = [*recent, now]


def _lucius_model() -> str:
    """The owner's setting for Lucius on calls, read here — never a model the worker names."""
    from backend.db.voice_config_store import VoiceConfigStore

    return VoiceConfigStore().get().get("lucius_model") or ""


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
        # From the app's own folder, in safe-path mode: `-m` put the CURRENT directory first on
        # the path, so a `backend/` or `json.py` wherever the app was started from would run in
        # the process holding the LiveKit secret (security review, 2026-11-03).
        self._proc = subprocess.Popen(
            list(self._argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=_PACKAGE_ROOT,
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
        while line := proc.stdout.readline(MAX_LINE):
            if not line.endswith(b"\n"):
                log.error("voice: the worker sent an over-long line; stopping it")
                kill_tree(proc.pid)
                break
            try:
                self._take(host, json.loads(line))
            except Exception:  # noqa: BLE001 — one bad line must never stall the link
                log.warning("voice: ignored an unreadable line from the worker")
        code = proc.wait()
        self._ready.set()
        if not self._stopping:
            log.error("voice: the worker process ended unexpectedly (exit code %s)", code)
            self._on_exit(code)

    def _take(self, host: Host, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        if msg.get("op") == "ready":
            self._ready.set()
        elif isinstance(msg.get("id"), int):
            host.handle(msg)

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
    env["PYTHONPATH"] = _PACKAGE_ROOT
    env["PYTHONSAFEPATH"] = "1"  # never the working directory ahead of PYTHONPATH
    return env
