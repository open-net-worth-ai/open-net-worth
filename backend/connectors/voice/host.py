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

from backend.connectors.voice.call_log import AFTER, PLACES
from backend.core.child_env import scrubbed_environ
from backend.core.process_tree import kill_tree, spawn_kwargs

log = logging.getLogger(__name__)

CALLS = frozenset(
    {
        "is_expected",
        "call_settings",
        "stop_request",
        "calls_allowed",
        "claim_call",
        "call_limit",
        "end_call",
        "call_failed",
        "call_connection",
        "part_of_day",
        "record_said",
        "record_exchange",
        "record_lines",
        "answer_unheard",
        "waiting_cards",
        "answer_card",
    }
)
STREAMS = frozenset({"ask", "follow_next_turn"})
_WORKER_ARGV = (sys.executable, "-m", "backend.connectors.voice.worker")
_PACKAGE_ROOT = str(Path(__file__).resolve().parents[3])
# The limits the APP holds, never trusting the worker for them: LiveKit's native layer parses
# remote media in that process and has crashed once, so it is the part assumed compromised
# (security review, 2026-11-03). The worker keeps its own, stricter checks too.
CALL_TTL_S = 2 * 3600  # a call's conversation stays reachable this long at most
MAX_ASKS_PER_MINUTE = 10  # the chat channels' per-sender ceiling
MAX_LINE = 1 << 20  # bytes; a longer line from the worker is a broken (or hostile) worker
MAX_LINES = 50  # spoken lines at once
MAX_LINE_CHARS = 4000
MAX_LINE_AGE_S = 24 * 3600
MAX_CALL_LINE_CHARS = 200_000


class Host:
    """The app's side of one worker process's link."""

    def __init__(self, tx: BinaryIO, app_loop: asyncio.AbstractEventLoop) -> None:
        self._line_chars: dict[str, int] = {}
        self._tx, self._loop = tx, app_loop
        self._send_lock = threading.Lock()
        self._tasks: dict[int, Any] = {}
        # Conversations the app handed this worker for a call (``claim_call``) → each call's room
        # → when: Lucius's side runs only on these, until EACH of its calls ends (``end_call``)
        # or ``CALL_TTL_S`` passes. Per call, not per conversation: the end of a call a
        # conversation had a moment ago shut the next call in it out, so it went silent on
        # every request (owner, 2026-11-04).
        self._sessions: dict[str, dict[str, float]] = {}
        # Rooms whose call this worker took → their conversation (a public demo meters calls by
        # room: ``services.demo_calls``).
        self._rooms: dict[str, str] = {}
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
        from backend.services import demo_calls

        if method == "is_expected":
            room = str(args.get("room") or "")
            expected = service.is_expected(room)
            if not expected:
                service.close_refused(room)
            return expected
        if method == "calls_allowed":
            return service.calls_allowed()
        if method == "call_settings":
            return await asyncio.to_thread(service.call_settings)
        if method == "stop_request":
            desk.stop_call_turn(self._owned(args))
            return None
        if method == "claim_call":
            return self._claim(str(args.get("room") or ""))
        if method == "part_of_day":
            return service.part_of_day()
        if method == "call_limit":
            return demo_calls.remaining(self._room_of(self._owned(args), args))
        if method == "end_call":
            session_id = str(args.get("session_id") or "")
            ended = [str(args.get("room") or "")] if args.get("room") else list(self._rooms)
            calls = self._sessions.get(session_id, {})
            for room in ended if args.get("room") else list(calls):
                calls.pop(room, None)
            if not calls:
                self._sessions.pop(session_id, None)
            for room in [r for r in ended if self._rooms.get(r) == session_id]:
                del self._rooms[room]
                demo_calls.end(room)
            for room in ended:
                service.note_call_connection(room, False)
            return None
        if method == "call_connection":
            room = self._room_of(self._owned(args), args)
            service.note_call_connection(room, bool(args.get("trouble")))
            return None
        if method == "call_failed":
            self._owned(args)
            # The service's own words, as text only: printable, one line, bounded.
            detail = "".join(c for c in str(args.get("detail") or "") if c.isprintable())[:400]
            service.note_call_problem(str(args.get("kind") or ""), detail)
            return None
        if method == "record_said":
            await desk.record_said(self._owned(args), str(args.get("said") or ""))
            return None
        if method == "waiting_cards":
            return await asyncio.to_thread(desk.waiting_cards, self._owned(args))
        if method == "answer_card":
            session_id = self._owned(args)
            self._count_ask(session_id)
            if _out_of_budget(session_id):
                return "busy"
            return await desk.answer_card(
                session_id, str(args.get("card_id") or ""), str(args.get("decision") or "")
            )
        if method == "record_lines":
            session_id = self._owned(args)
            await desk.record_lines(session_id, self._within_budget(session_id, args.get("lines")))
            return None
        if method == "answer_unheard":
            await desk.answer_unheard(self._owned(args), str(args.get("text") or ""))
            return None
        await desk.record_exchange(
            self._owned(args), str(args.get("said") or ""), str(args.get("reply") or "")
        )
        return None

    def _claim(self, room: str) -> Optional[str]:
        """Take a call the app minted. On a public demo it also starts the call's clock: a
        call past its admission window is refused, and one taken is closed by the app at its
        limit if the worker doesn't hang up (``service.close_when_over``)."""
        from backend.connectors.voice import service
        from backend.services import demo_calls

        session_id = service.claim_call(room)
        limit = demo_calls.claim(room) if session_id else 0
        if not session_id or (limit is not None and limit <= 0):
            # A room the worker won't take — a late join, or a visitor rejoining a call that
            # ended — would otherwise stay open on the demo's project with nobody closing it.
            service.close_refused(room)
            return None
        self._sessions.setdefault(session_id, {})[room] = time.monotonic()
        self._rooms[room] = session_id
        if limit is not None:
            service.close_when_over(room, limit)
        return session_id

    def _room_of(self, session_id: str, args: dict) -> str:
        room = str(args.get("room") or "")
        if self._rooms.get(room) != session_id:
            raise PermissionError("not a call this app started")
        return room

    def _stream(self, method: str, args: dict):
        from backend.connectors.voice import desk

        session_id = self._owned(args)
        if method == "ask":
            self._count_ask(session_id)
            refused = _out_of_budget(session_id)
            if refused:
                return _spoken(refused)
            return desk.ask(
                session_id,
                str(args.get("said") or ""),
                understood=str(args.get("understood") or ""),
                shown=bool(args.get("shown")),
                unheard=bool(args.get("unheard")),
                heard_part=str(args.get("heard_part") or "")[: desk.HEARD_PART_MAX],
                continues=bool(args.get("continues")),
                task_id=str(args.get("task_id") or "")[:32],
            )
        return desk.follow_next_turn(session_id)

    def _owned(self, args: dict) -> str:
        """The conversation a request names, if it is one this app handed this worker for a
        call that is still within its time — and only while voice calls are allowed."""
        from backend.connectors.voice import service

        session_id = str(args.get("session_id") or "")
        now = time.monotonic()
        calls = {
            r: t for r, t in self._sessions.get(session_id, {}).items() if now - t <= CALL_TTL_S
        }
        if not calls:
            self._sessions.pop(session_id, None)
            raise PermissionError("not the conversation of a call this app started")
        self._sessions[session_id] = calls
        if not service.calls_allowed():
            raise PermissionError("voice calls are off")
        if self._time_up(session_id):
            raise PermissionError("this call's time is up")
        return session_id

    def _time_up(self, session_id: str) -> bool:
        """A public demo's call past its limit (and grace) does nothing more for the worker."""
        from backend.services import demo_calls

        rooms = [r for r, s in self._rooms.items() if s == session_id]
        return bool(rooms) and all(demo_calls.over(r, grace_s=demo_calls.GRACE_S) for r in rooms)

    def _within_budget(self, session_id: str, raw: object) -> list[dict]:
        """A call can't grow a conversation without end, however long its worker keeps sending."""
        left = self._line_chars.get(session_id, MAX_CALL_LINE_CHARS)
        lines = []
        for line in _spoken_lines(raw):
            left -= len(line["text"])
            if left < 0:
                log.warning("voice: a call's spoken lines went past their limit")
                break
            lines.append(line)
        self._line_chars[session_id] = max(0, left)
        return lines

    def _count_ask(self, session_id: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._asks.get(session_id, []) if now - t < 60]
        if len(recent) >= MAX_ASKS_PER_MINUTE:
            raise PermissionError("too many requests on this call")
        self._asks[session_id] = [*recent, now]


def _line_time(value: object) -> float:
    """A line's time, if it could be one: a stored timestamp a date can't be made from fails every
    later save of the conversation."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if abs(value - time.time()) <= MAX_LINE_AGE_S else 0.0


def _spoken_lines(raw: object) -> list[dict]:
    """A worker's lines, as only what a line can be: shown in the chat, never read by a model."""
    lines = []
    for line in raw[:MAX_LINES] if isinstance(raw, list) else []:
        if not isinstance(line, dict) or not str(line.get("text") or "").strip():
            continue
        lines.append(
            {
                "text": str(line["text"])[:MAX_LINE_CHARS],
                "speaker": "owner" if line.get("speaker") == "owner" else "lucius",
                "task_id": str(line.get("task_id") or "")[:32] or None,
                "place": line.get("place") if line.get("place") in PLACES else AFTER,
                "started_at": _line_time(line.get("started_at")),
            }
        )
    return lines


def _out_of_budget(session_id: str) -> str:
    """On a public demo, the call's turns spend the visitor's own token budget (bound when the
    call started): the reason, to be said on the call, once it is spent; else ``""``."""
    from backend.services import demo_quota

    ip = demo_quota.bind_session(session_id)
    verdict = demo_quota.check_tokens(ip) if demo_quota.enabled() else None
    return verdict.reason if verdict is not None and not verdict.allowed else ""


async def _spoken(text: str):
    from backend.connectors.voice.events import DeskEvent

    yield DeskEvent("text", text)
    yield DeskEvent("end")


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
