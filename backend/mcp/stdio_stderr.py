"""Drain a spawned MCP server's stderr into a bounded rolling tail.

WHY A DEDICATED READER THREAD, NOT A PIPE WE READ WHEN CONVENIENT. Attaching
``stderr=PIPE`` and not draining it deadlocks the child the moment the OS pipe buffer fills
(64 KiB on Linux, less on macOS) — and a chatty installer (``npx -y`` on a 264 MB package)
fills it easily. The child blocks in ``write``, never answers ``initialize``, and the probe
times out for a reason that has nothing to do with the server. So: one daemon thread per
child, draining continuously from spawn to EOF. ``proc.communicate()`` is not an option
either — it blocks the caller, and the caller is in the middle of a JSON-RPC conversation on
the other two pipes.

WHY BOUNDED, TWICE OVER. This is untrusted output from a locally spawned process; a hostile
or merely broken server can stream forever. ``stdio_client`` already caps a single stdout
line at ``MAX_LINE_BYTES`` for exactly that reason, and stderr gets the equivalent
discipline: a per-line cap (bytes past it are dropped, never buffered) AND a cap on the total
retained, held as a rolling window of the last ``TAIL_LINES`` lines. Memory here is O(cap),
independent of how much the child writes.

WHO CLOSES THE PIPE. The reader thread does, in its own ``finally`` — never the caller.
Closing an fd from another thread while this one sits in ``read1`` is an fd-reuse hazard (the
number can be handed to an unrelated ``open`` before the blocked read returns). The caller
kills the process group first, which closes the write end, which ends the read cleanly; the
thread then closes its own end. ``shutdown()`` joins with a bound and gives up rather than
waiting, and the thread is a daemon, so a grandchild that somehow escaped the process group
and still holds the pipe open can delay the fd's close but can NEVER keep the process alive
or block a teardown.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

# One line of the child's stderr is retained at most this long. An npm/pip progress line is
# well under it; anything past it is dropped as it arrives (never buffered, then trimmed).
MAX_LINE_BYTES = 2048

# Total retained across the whole rolling window — the second, independent bound, so many
# medium lines cannot add up to an unbounded buffer the per-line cap alone would allow.
MAX_TAIL_BYTES = 8192

# How many lines the window keeps. The tail exists to explain a wait, not to log a build.
TAIL_LINES = 12

# What the error message quotes: the LAST couple of lines, which is where "npm ERR! 404" or
# the download that is still running actually lives.
ERROR_TAIL_LINES = 2

# A line that states what failed: "Error: Cannot find module …", "TypeError: …",
# "ModuleNotFoundError: …", "npm error 404 …", "error: unrecognized argument", "ERR_…".
_FAILURE_LINE = re.compile(
    r"(^|\s)(\w*(Error|Exception)\b:?|error\b:?|ERR_[A-Z_]+|Cannot find module|command not found)",
)

_READ_CHUNK_BYTES = 8192


class _Channel(Protocol):
    def note(self, text: str) -> None: ...


class StderrTail:
    """A daemon-drained, bounded rolling tail of one child's stderr.

    Construct it right after ``Popen`` (draining must start immediately — see the deadlock
    note above) and ``shutdown()`` it after the process group has been killed.
    """

    def __init__(self, stream, channel: Optional[_Channel] = None):
        self._stream = stream
        self._channel = channel
        self._lines: deque[str] = deque()
        self._bytes = 0
        self._partial = bytearray()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, name="mcp-stdio-stderr", daemon=True)
        self._thread.start()

    # ── read side ────────────────────────────────────────────────────────────────

    def lines(self) -> list[str]:
        """The retained tail, oldest first."""
        with self._lock:
            return list(self._lines)

    def error_tail(self, max_chars: int) -> str:
        """The last line or two, joined and capped — ready to quote in an error message.

        Bounded here as well as at capture, because this string reaches the UI and is
        persisted as ``last_error``. Sanitizing is the CALLER's (it owns the surrounding
        message), which is what ``stdio_client._StdioSession._explain`` does.
        """
        lines = self.lines()
        # A crashed runtime ends its report with boilerplate — Node prints a stack, a closing
        # brace and "Node.js v26.0.0" — so the last lines said nothing ("} • Node.js v26.0.0",
        # owner, 2026-10-31). Quote the last line that names the failure instead, when there is
        # one; otherwise the end of the output, as before.
        named = [ln for ln in lines if _FAILURE_LINE.search(ln)]
        if named:
            return named[-1].strip()[:max_chars]
        tail = lines[-ERROR_TAIL_LINES:]
        if not tail:
            return ""
        joined = " • ".join(tail)
        return joined[:max_chars]

    # ── write side (the reader thread) ───────────────────────────────────────────

    def _pump(self) -> None:
        try:
            while True:
                chunk = self._stream.read1(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                self._absorb(chunk)
        except (OSError, ValueError) as exc:
            # A closed/broken pipe is the normal end of a killed child, not an event.
            logger.debug("mcp stderr reader ended: %s", exc)
        finally:
            self._commit()  # whatever arrived with no trailing newline is still output
            try:
                self._stream.close()
            except Exception:  # noqa: BLE001 — teardown must never raise into a daemon thread
                pass

    def _absorb(self, chunk: bytes) -> None:
        # ``\r`` counts as a line break: an npm/pip download bar redraws in place with bare
        # carriage returns and may not emit a single ``\n`` for a minute — treating only
        # ``\n`` as a break would hold the ONE line that explains the wait until it ended.
        parts = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
        for piece in parts[:-1]:
            self._extend(piece)
            self._commit()
        self._extend(parts[-1])

    def _extend(self, piece: bytes) -> None:
        """Grow the in-progress line, DROPPING anything past the per-line cap.

        Dropped rather than trimmed later: a server emitting one endless line with no
        separator must cost O(cap), not O(what it sent)."""
        room = MAX_LINE_BYTES - len(self._partial)
        if room > 0 and piece:
            self._partial += piece[:room]

    def _commit(self) -> None:
        raw, self._partial = bytes(self._partial), bytearray()
        text = " ".join(raw.decode("utf-8", errors="replace").split())
        if not text:
            return
        with self._lock:
            self._lines.append(text)
            self._bytes += len(text)
            while self._lines and (len(self._lines) > TAIL_LINES or self._bytes > MAX_TAIL_BYTES):
                self._bytes -= len(self._lines.popleft())
        channel = self._channel
        if channel is not None:
            # Outside our lock — the channel holds its own, and its sanitize is the ingress
            # guard for everything that leaves this process.
            try:
                channel.note(text)
            except Exception:  # noqa: BLE001 — progress must never break the drain
                logger.debug("mcp probe progress publish failed", exc_info=True)

    def settle(self, timeout: float = 0.3) -> None:
        """Let the reader catch up before its output is QUOTED. Bounded; never required.

        Both paths that quote the tail (a timeout, whose watchdog has just SIGKILLed the
        process group, and an EOF, meaning the child already exited) reach it while the
        reader may still be absorbing the child's last chunk — a `npm ERR! 404` written
        milliseconds before exit would otherwise be missed by a scheduling accident. The
        write end is closed on both paths, so EOF is immediate and this returns at once; the
        bound is there for the case where it somehow is not."""
        self._thread.join(timeout=timeout)

    # ── teardown ─────────────────────────────────────────────────────────────────

    def shutdown(self, timeout: float = 1.0) -> None:
        """Wait briefly for the reader to finish; never block teardown on it.

        Called AFTER the process group is killed, so the write end is already gone and EOF
        is imminent. If it somehow is not (a grandchild that escaped the group still holds
        the pipe), we give up: the thread is a daemon holding one fd, which cannot keep the
        process alive, and closing the fd from here instead would risk closing a number that
        has since been reused."""
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.debug("mcp stderr reader still draining after %.1fs — left to finish", timeout)
