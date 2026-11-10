"""Transport-agnostic machinery for a live CLI process reused across one conversation.

Claude Code and Antigravity both speak a stream-json protocol on stdin/stdout, and both would
otherwise be spawned per model round with the whole transcript re-sent. This module owns what
is the same for any such CLI; the transport modules (``claude_live_session``,
``antigravity_live_session``) own the wire format of one user message, which event ends a
turn, and their env knobs. The reasoning — in particular why a session is keyed by the exact
PREFIX of the transcript it has consumed, so a fold / decay / checkpoint restore can never be
answered from stale CLI-side history — is in ``claude_live_session``'s docstring and ADR
``2026-08-01-claude-cli-live-sessions``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from typing import AsyncGenerator, Callable, Optional

from backend.core.errors import ExternalServiceError, ProviderTimeoutError

from .cli_tool_protocol import _content_to_text, flatten_messages_with_images

log = logging.getLogger(__name__)

STDOUT_LIMIT = 8 * 1024 * 1024
DEFAULT_IDLE_SECONDS = 1800.0

RenderFull = Callable[[list[dict]], "tuple[str, list[dict]]"]


def env_flag(name: str) -> bool:
    """A boolean env switch that defaults ON; ``0``/``false``/``no`` turns it off."""
    return (os.getenv(name) or "1").strip().lower() not in ("0", "false", "no")


def env_seconds(name: str, default: float, floor: float) -> float:
    try:
        return max(floor, float(os.getenv(name) or default))
    except ValueError:
        return default


def _fingerprint(message: dict) -> str:
    """Identity of one transcript message, for prefix matching.

    Role + rendered content, hashed. It must change whenever the message the model would
    see changes — an edited tool result or a folded summary has to MISS, or the session
    would keep answering from a history we no longer hold.
    """
    content = message.get("content")
    digest = hashlib.sha256()
    digest.update(f"{message.get('role') or 'user'}\x00".encode())
    digest.update(_content_to_text(content).encode("utf-8"))
    # `_content_to_text` deliberately represents every image with the same short marker.
    # Add the actual attachment identity incrementally so two 10MB images do not require
    # building a second 10MB JSON string merely to invalidate a stale live session.
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            source = block.get("source") or {}
            if not isinstance(source, dict):
                continue
            digest.update(b"\x00image\x00")
            digest.update(str(source.get("media_type") or "").encode("utf-8"))
            digest.update(b"\x00")
            digest.update(str(source.get("data") or "").encode("ascii", "replace"))
    return digest.hexdigest()


def fingerprints(messages: list[dict]) -> list[str]:
    return [_fingerprint(m) for m in messages]


class LiveSession:
    """One long-lived CLI child, holding one conversation. Subclasses fix the wire format."""

    LABEL = "CLI"
    LOG_PREFIX = "cli"
    TIMEOUT_ENV = ""
    TIMEOUT_DEFAULT = 300.0
    # How long the child may emit NOTHING (first event included) before the attempt is
    # abandoned as stalled. 0 disables it for a transport that has no such budget.
    STALL_ENV = ""
    STALL_DEFAULT = 0.0

    def __init__(self, argv: list[str], env: dict, workdir: str):
        self.argv = argv
        self.env = env
        self.workdir = workdir
        self.proc: Optional[asyncio.subprocess.Process] = None
        # The transcript prefix this process has already seen — ours and its own replies.
        self.consumed: list[str] = []
        self.last_used = 0.0
        self._busy = False
        self.timed_out = False
        # The child's last stderr lines, for a failure report.
        self.stderr_tail: list[str] = []

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    @classmethod
    def timeout_seconds(cls) -> float:
        return env_seconds(cls.TIMEOUT_ENV, cls.TIMEOUT_DEFAULT, 5.0)

    @classmethod
    def stall_seconds(cls) -> float:
        if not cls.STALL_DEFAULT:
            return 0.0
        return env_seconds(cls.STALL_ENV, cls.STALL_DEFAULT, 10.0)

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workdir,
            env=self.env,
            limit=STDOUT_LIMIT,
        )
        asyncio.ensure_future(_drain(self.proc.stderr, self.LOG_PREFIX, self.stderr_tail))
        self.last_used = time.monotonic()

    def matches(self, fps: list[str]) -> bool:
        """True when ``fps`` still begins with everything this session has consumed."""
        n = len(self.consumed)
        return bool(n) and len(fps) >= n and fps[:n] == self.consumed

    async def send(self, text: str, images: Optional[list[dict]] = None) -> None:
        raise NotImplementedError

    def is_final(self, event: dict) -> bool:
        """True for the event that ends one turn (the process stays up after it)."""
        raise NotImplementedError

    async def _write(self, payload: bytes) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            await asyncio.sleep(0.2)  # let the drain collect the exit message
            said = " | ".join(self.stderr_tail)[-300:]
            raise ExternalServiceError(
                f"{self.LABEL} stopped right after starting"
                + (f": {said}" if said else " (it printed no reason)")
            )
        try:
            proc.stdin.write(payload)
            await proc.stdin.drain()
        except OSError as err:
            # The child exited between the liveness check and the write.
            raise ExternalServiceError(f"{self.LABEL} session closed its input: {err}") from err

    async def _timed_out(self, stalled: bool = False) -> ProviderTimeoutError:
        self.timed_out = True
        await self.close()
        if stalled:
            return ProviderTimeoutError(
                f"{self.LABEL} produced no output for {self.stall_seconds():.0f}s "
                f"(raise {self.STALL_ENV} if this is expected).",
                stalled=True,
            )
        return ProviderTimeoutError(
            f"{self.LABEL} timed out after {self.timeout_seconds():.0f}s "
            f"(raise {self.TIMEOUT_ENV} if this is expected)."
        )

    async def events(self) -> AsyncGenerator[dict, None]:
        """Yield NDJSON events up to and including this turn's final event.

        The stream ENDS at the final event instead of at EOF — the process stays up for
        the next turn. Two clocks run: the whole-turn ceiling, and a stall watchdog that
        restarts at every event, so a child that goes quiet is abandoned in minutes and
        not after the ceiling.
        """
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        deadline = time.monotonic() + self.timeout_seconds()
        stall = self.stall_seconds()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise await self._timed_out()
            stalled = bool(stall) and stall < remaining
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=stall if stalled else remaining
                )
            except asyncio.TimeoutError:
                raise await self._timed_out(stalled=stalled) from None
            if not line:
                # The child died mid-turn. Surface a non-zero exit the way a one-shot run
                # would — "it exited with status 3" is a far better report than "the turn
                # was truncated", and it is the same failure.
                rc = await proc.wait() if proc.returncode is None else proc.returncode
                await self.close()
                if rc:
                    detail = " | ".join(self.stderr_tail)[-500:]
                    raise ExternalServiceError(
                        f"{self.LABEL} exited with status {rc}" + (f": {detail}" if detail else "")
                    )
                return
            event = _parse_event(line, self.LOG_PREFIX)
            if event is None:
                continue
            yield event
            if self.is_final(event):
                self.last_used = time.monotonic()
                return

    async def close(self) -> None:
        proc, self.proc = self.proc, None
        self.consumed = []
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (ProcessLookupError, OSError, asyncio.TimeoutError):
                pass
        shutil.rmtree(self.workdir, ignore_errors=True)


class SessionPool:
    """Live sessions for one event loop, keyed by their spawn parameters."""

    def __init__(self, idle_seconds: Callable[[], float]) -> None:
        self._idle_seconds = idle_seconds
        # argv-key → sessions holding different conversations under identical flags.
        self._pool: dict[str, list[LiveSession]] = {}

    @staticmethod
    def key(argv: list[str]) -> str:
        # The system prompt, model, effort and tool manifest are all fixed at spawn. A
        # session may only serve a turn that would have spawned it identically.
        return hashlib.sha256("\x00".join(argv).encode("utf-8")).hexdigest()

    def reap_idle(self) -> list[LiveSession]:
        """Return sessions to close: dead ones, and any idle past the cutoff."""
        cutoff = time.monotonic() - self._idle_seconds()
        doomed: list[LiveSession] = []
        for key, sessions in list(self._pool.items()):
            keep = []
            for s in sessions:
                if not s.alive or (not s._busy and s.last_used < cutoff):
                    doomed.append(s)
                else:
                    keep.append(s)
            if keep:
                self._pool[key] = keep
            else:
                self._pool.pop(key, None)
        return doomed

    def find(self, argv: list[str], fps: list[str]) -> Optional[LiveSession]:
        for s in self._pool.get(self.key(argv), []):
            if s.alive and not s._busy and s.matches(fps):
                return s
        return None

    def add(self, argv: list[str], session: LiveSession) -> None:
        self._pool.setdefault(self.key(argv), []).append(session)

    def drop(self, argv: list[str], session: LiveSession) -> None:
        sessions = self._pool.get(self.key(argv))
        if not sessions:
            return
        if session in sessions:
            sessions.remove(session)
        if not sessions:
            self._pool.pop(self.key(argv), None)

    def all(self) -> list[LiveSession]:
        return [s for sessions in self._pool.values() for s in sessions]


def pool_for(registry: "dict[int, SessionPool]", idle_seconds: Callable[[], float]) -> SessionPool:
    """The calling event loop's pool from a transport's registry, created on first use."""
    loop_id = id(asyncio.get_running_loop())
    p = registry.get(loop_id)
    if p is None:
        p = SessionPool(idle_seconds)
        registry[loop_id] = p
    return p


def pending_turn(messages: list[dict], consumed: int) -> tuple[str, list[dict]]:
    """Return the unseen text and images after skipping the reply this session generated.

    The one assistant message at the boundary is skipped: the CLI produced it, so it already
    holds it. Any other there is an alert or a briefing pushed since that reply. The CLI has
    never seen those, and the person may be answering one, so they are sent.
    """
    i = consumed
    if i < len(messages) and (messages[i].get("role") or "user") == "assistant":
        i += 1
    return flatten_messages_with_images(messages[i:])


async def spawn(session_cls: type[LiveSession], workdir_prefix: str, argv: list[str], env: dict):
    """Start a session in a fresh scratch directory, removed again if the start fails."""
    session = session_cls(argv, env, tempfile.mkdtemp(prefix=workdir_prefix))
    try:
        await session.start()
    except BaseException:
        await session.close()
        raise
    return session


async def acquire(
    p: SessionPool,
    session_cls: type[LiveSession],
    workdir_prefix: str,
    argv: list[str],
    env: dict,
    messages: list[dict],
    key_argv: Optional[list[str]] = None,
    render_full: RenderFull = flatten_messages_with_images,
    fresh: bool = False,
) -> "tuple[LiveSession, str, list[dict], bool]":
    """Get a session for this turn.

    Returns ``(session, text_to_send, images_to_send, reused)``. On a miss — first turn,
    or any rewrite of the history the session had consumed — a fresh process is spawned
    and ``render_full``'s rendering of the FULL transcript is sent. ``fresh`` skips the
    lookup altogether — a retry after a failed reuse must not land on another idle session.
    """
    key_argv = key_argv or argv
    for dead in p.reap_idle():
        await dead.close()

    session = None if fresh else p.find(key_argv, fingerprints(messages))
    if session is not None:
        text, images = pending_turn(messages, len(session.consumed))
        if text.strip() and text.strip() != "Assistant:":
            session._busy = True
            return session, text, images, True
        # Nothing new to say — the transcript did not advance. Rebuild rather than send an
        # empty turn into a live session.
        p.drop(key_argv, session)
        await session.close()

    session = await spawn(session_cls, workdir_prefix, argv, env)
    session._busy = True
    p.add(key_argv, session)
    text, images = render_full(messages)
    return session, text, images, False


async def release(
    p: SessionPool, key_argv: list[str], session: LiveSession, messages: list[dict], ok: bool
) -> None:
    """Hand the session back. A failed turn retires it rather than reusing a poisoned one."""
    session._busy = False
    if not ok or not session.alive:
        p.drop(key_argv, session)
        await session.close()
        return
    # It has now seen everything we were given, plus the reply it just generated — which
    # arrives in OUR list on the next turn and is stepped over by `pending_turn`.
    session.consumed = fingerprints(messages)
    session.last_used = time.monotonic()


async def close_all(registry: "dict[int, SessionPool]") -> None:
    for p in registry.values():
        for s in p.all():
            await s.close()
    registry.clear()


def _parse_event(line: bytes, log_prefix: str) -> Optional[dict]:
    try:
        event = json.loads(line.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        log.debug("%s: skipping non-JSON stdout line: %.200s", log_prefix, line)
        return None
    return event if isinstance(event, dict) else None


async def _drain(stream, log_prefix: str, tail: list[str]) -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = await stream.readline()
            if not chunk:
                return
            text = chunk.decode("utf-8", "replace").strip()
            if text:
                log.debug("%s stderr: %.300s", log_prefix, text)
                tail.append(text)
                del tail[:-20]
    except (asyncio.CancelledError, OSError, ValueError):
        return
