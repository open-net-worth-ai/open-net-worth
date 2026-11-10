"""StdioMcpClientConnection — JSON-RPC 2.0 over a SPAWNED LOCAL command's stdin/stdout.

The stdio sibling of ``backend/mcp/client.McpClientConnection`` (HTTP). It exposes the
SAME minimal client surface (``initialize`` / ``list_tools`` / ``call_tool``) so the
``McpClientManager`` is transport-agnostic — only HOW the JSON-RPC frames travel
differs. A stdio MCP server is a LOCAL process (``npx``/``uvx``/a script) the user
configured; we spawn it, speak **newline-delimited JSON** over its pipes (the MCP stdio
framing — one JSON message per line; mirrors ``backend/mcp/stdio_bridge`` and explicitly
NOT LSP ``Content-Length`` framing, which makes clients hang on ``initialize``), and
shut it down.

**SECURITY — this is ARBITRARY LOCAL COMMAND EXECUTION on the ONW backend host.** Two
invariants live here:
  * **No ambient env.** The child is spawned with a SCRUBBED environment — a tiny base
    (``PATH``/``HOME``/locale, the minimum to find + run ``npx``/``uvx``) plus ONLY the
    keys the user put in the server config's ``env`` overlay. ``os.environ`` is NEVER
    passed through, so an ONW secret in the parent process can't leak into the child.
  * **Bounded + killable.** Each call is bounded by a timeout, and the child is started
    as the root of its own process tree (``core.process_tree``) so we can kill the whole tree
    on close — a wedged/forking server can't hang the turn or linger.

A short-lived session per call (spawn → initialize → method → close) mirrors the HTTP
connection's stateless-per-call model: there is no shared long-lived child driven from
multiple worker threads (which would be a concurrency hazard), and a crashed server just
fails that one call cleanly. Robustness contract is identical to the HTTP client: any
spawn/transport/protocol failure raises ``McpCallError`` (never a raw exception into the
chat loop); the manager fences it into a tool-error string.

Stdlib only (``subprocess``/``json``/``os``/``signal``) — NO ``mcp`` SDK, no new dep.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from contextlib import contextmanager
from typing import Any, Literal, Optional, Tuple

from backend.core.config import get_settings
from backend.core.process_tree import kill_tree, spawn_kwargs, terminate_tree
from backend.core.redact_error import redact_error
from backend.core.untrusted import sanitize_untrusted
from backend.mcp import probe_progress
from backend.mcp.client import (
    CLIENT_INFO,
    PROTOCOL_VERSION,
    Deadline,
    McpCallError,
    split_result_content,
)
from backend.mcp.stdio_stderr import StderrTail
from backend.models.mcp_client import McpServerConfig, redact_endpoint

logger = logging.getLogger(__name__)

# Owner's explicit request: 1 hour, not a short bound.
STDIO_CALL_TIMEOUT_SECONDS = 3600

# The budget for a POOLED connection's ONE-TIME first spawn + `initialize()` handshake — the
# only place a cold `npx -y <package>`/`uvx` package download belongs (mirrors
# `client.PROBE_TIMEOUT_STDIO_SECONDS`'s own 300s rationale for the identical cold-start cost,
# measured against a real ~264MB package). Passed as an explicit PER-CALL override
# (`initialize(timeout=...)`), never stored as the connection's own `_deadline`: a `Deadline` is
# an ABSOLUTE, decaying budget, and a pooled connection can live for the app's entire uptime —
# sharing one `Deadline` across that whole lifetime would leave calls made long after the first
# spawn with near-zero remaining budget. Every later call uses the ever-renewing
# `STDIO_CALL_TIMEOUT_SECONDS` above instead.
STDIO_CONNECT_TIMEOUT_SECONDS = 300

# Hard byte cap on a SINGLE NDJSON line read off the child's stdout, applied at the
# TRANSPORT layer BEFORE json.loads + the 4000-char result fence (security-review B1): a
# malicious local stdio server that never sends a newline (or sends a multi-GB line) would
# make an unbounded ``stdout.readline()`` buffer it all → OOM the ONW host. We read in
# bounded chunks up to this cap; if a newline hasn't arrived by then the message/connection
# is aborted with a typed ``McpCallError`` instead of buffering unboundedly. 1 MiB is ample
# for any legitimate JSON-RPC frame; newline-delimited framing is preserved for well-behaved
# servers (a normal small line still round-trips).
MAX_LINE_BYTES = 1_048_576  # 1 MiB

# Chunk size for the bounded line read (kept small so we never over-read past the cap by
# more than one chunk).
_READ_CHUNK_BYTES = 65536

# How much of the child's stderr an ERROR MESSAGE is allowed to quote. The message reaches
# the UI and is persisted as ``last_error``, so the quoted text is sanitized as well as
# capped — a stdio server's stderr is server-chosen text like any other, and an unsanitized
# one could carry ``«user_msg:hex»``, the one fence that marks the OPERATOR speaking.
MAX_ERROR_TAIL_CHARS = 300

# The ONLY ambient env keys the spawned child inherits (the minimum to locate + run a
# typical node/python MCP server). EVERYTHING else comes from the user's ``env`` overlay
# — os.environ is never passed through (no ONW-secret leak into the child).
_BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SystemRoot")
# Windows needs its profile and temp locations for npx/uvx to find a cache, PATHEXT to resolve
# ``npx.cmd``, and COMSPEC for the .cmd shim itself — still no secret among them.
if os.name == "nt":
    _BASE_ENV_KEYS += (
        "PATHEXT",
        "COMSPEC",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramData",
        "SystemDrive",
        "WINDIR",
    )


def _scrubbed_env(overlay: dict[str, str]) -> dict[str, str]:
    """A minimal child environment: a tiny ambient base + the user's explicit overlay.

    Constructed (never ``os.environ.copy()``), so the child sees no parent secret it
    wasn't explicitly granted. The overlay wins on a key collision (the user is
    deliberately setting it). All values are coerced to ``str`` (subprocess requires it).
    """
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    for key, value in (overlay or {}).items():
        if key:
            env[str(key)] = "" if value is None else str(value)
    return env


_TOOLS_LIST_CHANGED = "notifications/tools/list_changed"


class StdioMcpClientConnection:
    """A short-lived stdio JSON-RPC client for one external MCP server.

    Each public method spawns the configured command, runs the MCP ``initialize``
    handshake, performs the one method, and tears the process down — the same
    stateless-per-call shape as the HTTP client (so the manager treats both identically).
    """

    def __init__(self, config: McpServerConfig, *, deadline: Optional[Deadline] = None):
        self._config = config
        self._next_id = 0
        # ``None`` = the standing per-call timeout (autonomous dispatch). A ``Deadline``
        # bounds the WHOLE operation across calls (the interactive probe).
        self._deadline = deadline
        # Set only inside a ``with`` block — the ONE child shared by every call in the block.
        self._session: Optional["_StdioSession"] = None

    # ── context manager: one child for the block, killed on EVERY exit path ────────

    def __enter__(self) -> "StdioMcpClientConnection":
        """Spawn ONE child for the whole block.

        Two things this buys over the per-call sessions below. It halves the spawns for a
        probe (``initialize`` + ``tools/list`` share a child instead of launching ``npx``
        twice — on a cold cache that is the difference between one download and two). And it
        gives the caller a single process group whose teardown is guaranteed by ``__exit__``
        on every path out: success, a raised ``McpCallError``, a timeout, or an abandoned
        request. A user hitting Back must never leave an orphaned ``npx`` tree on the host.
        """
        self._session = _StdioSession(self._config, deadline=self._deadline).__enter__()
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        session, self._session = self._session, None
        if session is not None:
            session.__exit__(*exc)  # SIGTERM → SIGKILL the whole process group, then reap
        return False  # never swallow an exception

    def is_alive(self) -> bool:
        """Whether this connection's child process is still running.

        The liveness check ``McpClientManager``'s turn-scoped reuse makes BEFORE handing a
        cached connection another call (ADR
        ``2026-08-22-external-mcp-stdio-connections-reused-per-turn`` — mirrors Codex's
        ``reusable_client``'s own "matching config, not closed" check). ``False`` before
        ``__enter__``/after ``__exit__`` (no session at all — nothing to reuse), or once the
        child has exited for any reason (crashed, killed by its own call's timeout watchdog,
        or torn down normally). A cheap, non-blocking ``proc.poll()`` — never assumes life
        from the absence of a *known* death."""
        session = self._session
        return session is not None and session.is_alive()

    @contextmanager
    def _session_scope(self):
        """The session this call runs on: the block-owned one, else a fresh short-lived child.

        Keeps the pre-existing spawn-per-call behaviour for autonomous dispatch (which makes
        exactly one call) while letting a ``with`` block reuse one child across several."""
        if self._session is not None:
            yield self._session
        else:
            with _StdioSession(self._config, deadline=self._deadline) as session:
                yield session

    # ── public API (mirrors McpClientConnection) ───────────────────────────────────

    def initialize(self, *, timeout: Optional[float] = None) -> dict:
        """Run the MCP ``initialize`` handshake; return the server's result block.

        Inside a ``with`` block this is the block's single handshake and its result is
        cached, so a later ``list_tools`` reuses it rather than initializing a second time on
        the same child (which a spec-conformant server rejects).

        ``timeout``, when given, overrides the budget for THIS call only (never mutates the
        connection's own standing deadline) — the app-lifetime pool's connect path uses it to
        grant a first-ever handshake the long ``STDIO_CONNECT_TIMEOUT_SECONDS`` budget while
        every later call on the same pooled connection keeps the short standing one."""
        with self._session_scope() as session:
            return session.ensure_initialized(self._rpc_id, timeout=timeout)

    def list_tools(self) -> list[dict]:
        """``initialize`` (once) → ``tools/list``. Returns tool descriptors.

        Malformed entries (no string ``name``) are dropped (a lying/buggy server can't
        poison the listing), matching the HTTP client."""
        with self._session_scope() as session:
            session.ensure_initialized(self._rpc_id)
            result = session.rpc(self._rpc_id(), "tools/list", {})
        raw = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            return []
        tools: list[dict] = []
        for t in raw:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            if not isinstance(name, str) or not name:
                continue
            entry = {
                "name": name,
                "description": t.get("description") or "",
                "inputSchema": t.get("inputSchema")
                if isinstance(t.get("inputSchema"), dict)
                else {"type": "object", "properties": {}},
            }
            ann = t.get("annotations")
            if isinstance(ann, dict):
                entry["annotations"] = ann
            tools.append(entry)
        return tools

    def call_tool(self, tool: str, arguments: dict) -> Tuple[str, list[dict]]:
        """Spawn → ``initialize`` → ``tools/call`` → close. Returns ``(text, images)`` —
        see ``split_result_content``.

        An ``isError`` result is raised as ``McpCallError`` so the manager fences it. The
        only thing sent to the child is the model's tool-call args (no ONW secret)."""
        with self._session_scope() as session:
            session.ensure_initialized(self._rpc_id)
            result = session.rpc(
                self._rpc_id(), "tools/call", {"name": tool, "arguments": arguments or {}}
            )
        text, images = split_result_content(
            result.get("content") if isinstance(result, dict) else None
        )
        if isinstance(result, dict) and result.get("isError"):
            raise McpCallError(text or "external tool reported an error")
        return text, images

    def take_tools_changed(self) -> bool:
        """Whether the server announced a new toolset since the last ask; clears the flag."""
        session = self._session
        if session is None or not session.tools_changed:
            return False
        session.tools_changed = False
        return True

    def _rpc_id(self) -> int:
        self._next_id += 1
        return self._next_id


class _StdioSession:
    """One spawned child process speaking NDJSON JSON-RPC over its stdin/stdout.

    A context manager: ``__enter__`` spawns (raising ``McpCallError`` on a spawn failure),
    ``__exit__`` terminates the process group. ``rpc`` writes one framed request line and
    reads framed reply lines until the matching id arrives (skipping the child's stderr
    noise on stdout / unrelated notifications)."""

    def __init__(self, config: McpServerConfig, *, deadline: Optional[Deadline] = None):
        self._config = config
        self._proc: Optional[subprocess.Popen] = None
        self._deadline = deadline
        self._initialized: Optional[dict] = None
        self._stderr: Optional[StderrTail] = None
        # PERSISTENT receive buffer (security review H1) — bytes read off stdout that are
        # NOT yet consumed as a complete NDJSON frame, kept across ``_read_capped_line`` calls
        # (within one ``rpc()`` skipping an unmatched notification, and across SEPARATE
        # ``rpc()`` calls on this same long-lived session). A persistent connection genuinely
        # sees a server pipeline an async ``notifications/*`` line and the real reply in one
        # flush — unlike the old per-call design's "no next frame to lose" assumption — so
        # bytes after the first newline in a chunk are consumed from here on the NEXT read,
        # never dropped.
        self._rxbuf = bytearray()
        # Set when the server says its toolset changed. A notice sent while idle waits in the
        # pipe and is read during the NEXT rpc, so it is seen on the next call, not instantly.
        self.tools_changed = False

    def __enter__(self) -> "_StdioSession":
        if get_settings().desktop_mode:
            raise McpCallError(
                "Local stdio MCP servers are unavailable in the desktop app until "
                "child-process containment is supported. Use an HTTP MCP server."
            )
        command = (self._config.command or "").strip()
        if not command:
            raise McpCallError(f"server '{self._config.id}' has no command configured")
        env = _scrubbed_env(self._config.env or {})
        # CreateProcess does not apply PATHEXT, so a bare ``npx`` would miss ``npx.cmd``.
        argv = [shutil.which(command, path=env.get("PATH")) or command, *(self._config.args or [])]
        try:
            self._proc = subprocess.Popen(  # noqa: S603 — user-configured local MCP server (documented arbitrary local exec)
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # CAPTURED, not discarded. This is where ``npx`` writes the download progress
                # that explains a 60-second wait, and where a failing server writes the
                # traceback that explains a dead one — both of which used to go to DEVNULL,
                # leaving "timed out" as the whole story. A pipe MUST be drained or the child
                # deadlocks once the buffer fills, which is what ``StderrTail``'s reader
                # thread is for; see ``stdio_stderr`` for the bounds it enforces.
                stderr=subprocess.PIPE,
                env=env,
                # Own process group so we can kill the whole tree on close (a server may
                # fork children); text=False → we frame exact UTF-8 bytes ourselves.
                **spawn_kwargs(),
            )
        except (OSError, ValueError) as exc:
            raise McpCallError(
                f"cannot start mcp server '{self._config.id}' "
                f"({redact_endpoint('stdio', command=command)}): {redact_error(str(exc))}"
            ) from exc
        # Resolved HERE, in the spawning thread, and handed to the reader by reference: a
        # plain ``threading.Thread`` inherits no ContextVar, so the reader could never look
        # this up itself. ``None`` outside a probe (autonomous dispatch publishes nothing).
        self._stderr = StderrTail(self._proc.stderr, probe_progress.current_channel())
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        proc = self._proc
        stderr = self._stderr
        self._proc = None
        self._stderr = None
        if proc is None:
            return False
        # Kill the whole process group (the server may have forked children), then reap.
        try:
            terminate_tree(proc.pid)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                kill_tree(proc.pid)
            except OSError:
                proc.kill()
        # AFTER the kill, never before: the dead child's write end is what ends the reader's
        # blocking read. Bounded and daemon-backed, so a stderr holder that somehow outlived
        # the process group delays nothing here (``StderrTail.shutdown``). The reader closes
        # the stderr pipe itself — closing it from this thread could close an fd number that
        # has already been reused underneath a read still in flight.
        if stderr is not None:
            stderr.shutdown()
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        return False  # never swallow an exception

    def is_alive(self) -> bool:
        """Non-blocking: whether the spawned child is still running (``proc.poll() is
        None``). ``False`` once ``__exit__`` has run (``self._proc`` cleared) or the child
        has exited on its own."""
        proc = self._proc
        return proc is not None and proc.poll() is None

    def ensure_initialized(self, next_rid, *, timeout: Optional[float] = None) -> dict:
        """Send ``initialize`` at most ONCE per child, returning the cached result after.

        MCP's handshake is once-per-connection; a second one on the same child is a protocol
        error. Callers therefore ask for "make sure this session is initialized" rather than
        "initialize", so sharing a child across ``initialize`` + ``tools/list`` is safe.

        ``timeout`` overrides the budget for this one RPC (see ``rpc``'s ``timeout_override``)
        — ignored once ``_initialized`` is already cached, since no RPC runs in that case."""
        if self._initialized is None:
            self._initialized = self.rpc(
                next_rid(),
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
                timeout_override=timeout,
            )
        return self._initialized

    def rpc(
        self, rid: int, method: str, params: dict, *, timeout_override: Optional[float] = None
    ) -> dict:
        """Send one JSON-RPC request line; read framed replies until the matching id.

        ``timeout_override``, when given, replaces the standing per-call budget for THIS call
        only (never mutates ``self._deadline``) — see ``STDIO_CONNECT_TIMEOUT_SECONDS``'s
        docstring for why the pool's first-spawn handshake needs this rather than a longer
        standing deadline."""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise McpCallError(f"mcp server '{self._config.id}' is not running")
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpCallError(f"mcp server '{self._config.id}' closed its input: {exc}") from exc

        parsed = self._read_until(proc, rid, timeout_override=timeout_override)
        if "error" in parsed and parsed["error"]:
            err = parsed["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            base = f"server '{self._config.id}' error"
            raise McpCallError(f"{base}: {msg}", safe_summary=base + self._rpc_error_hint(msg))
        result = parsed.get("result")
        return result if isinstance(result, dict) else {}

    def _read_until(
        self, proc: subprocess.Popen, rid: int, *, timeout_override: Optional[float] = None
    ) -> dict:
        """Read NDJSON reply lines off stdout until the one with ``id == rid`` arrives.

        Lines that don't parse / aren't our reply (server notifications, a different id)
        are skipped — and, since the read buffer is now PERSISTENT (``self._rxbuf``, security
        review H1), a notification pipelined in the SAME flush as the real reply is skipped
        without another blocking ``read1()``: the bytes after it are already buffered, not
        dropped. EOF before our reply → the server died → ``McpCallError``. Bounded by a
        wall-clock deadline (a watchdog timer that kills the process group on expiry, so a
        never-replying child can't hang the turn — works without a reader thread).

        ``timeout_override`` takes precedence over both the connection-scoped ``Deadline``
        and the standing per-call default, for the ONE RPC this call is serving."""
        deadline_hit: dict[str, bool] = {"v": False}

        def _on_timeout():
            deadline_hit["v"] = True
            try:
                kill_tree(proc.pid)
            except OSError:
                proc.kill()

        import threading

        # Precedence: an explicit per-call override (the pool's first-handshake budget) >
        # a connection-scoped Deadline (the probe's whole-operation budget) > the standing
        # short per-call default for an ordinary steady-state call.
        if timeout_override is not None:
            budget = timeout_override
        elif self._deadline is not None:
            budget = self._deadline.remaining()
        else:
            budget = STDIO_CALL_TIMEOUT_SECONDS
        if budget <= 0:
            raise McpCallError(
                f"mcp server '{self._config.id}' ran out of time before it finished replying"
            )
        watchdog = threading.Timer(budget, _on_timeout)
        watchdog.daemon = True
        watchdog.start()
        try:
            stdout = proc.stdout
            assert stdout is not None
            while True:
                raw, complete = self._read_capped_line(stdout)
                if not complete:
                    if not raw:
                        if deadline_hit["v"]:
                            base = f"mcp server '{self._config.id}' timed out after {budget:.0f}s"
                            raise McpCallError(
                                base + self._explain(), safe_summary=base + self._explain_hint()
                            )
                        base = f"mcp server '{self._config.id}' exited before replying"
                        raise McpCallError(
                            base + self._explain(), safe_summary=base + self._explain_hint()
                        )
                    # A line exceeded MAX_LINE_BYTES with no newline — abort WITHOUT
                    # buffering the whole stream (security-review B1). Kill the child so a
                    # flooding server can't keep streaming.
                    self._kill_proc(proc)
                    raise McpCallError(
                        f"mcp server '{self._config.id}' sent a line over the "
                        f"{MAX_LINE_BYTES}-byte limit"
                    )
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    parsed: Any = json.loads(stripped)
                except (ValueError, TypeError):
                    continue  # noise on stdout — skip
                if not isinstance(parsed, dict):
                    continue
                if parsed.get("method") == _TOOLS_LIST_CHANGED and "id" not in parsed:
                    self.tools_changed = True
                    continue
                if parsed.get("id") != rid:
                    continue  # a notification or a different request's reply — skip
                return parsed
        finally:
            watchdog.cancel()

    def _explain(self) -> str:
        """The clause that turns "it timed out" into "it timed out, and here is what it said".

        THE INCIDENT this exists for: adding a real vendor's stdio server with the vendor's
        own documented ``npx -y`` command failed with a bare "timed out after 60s", which
        reads as "this server is broken". It was not — the package unpacks to ~264 MB and its
        cold npm download alone took 61.2s, so the probe killed ``npx`` before the MCP server
        had started. Warm-cache the identical probe finished in 3.6s. The child was saying so
        on stderr the whole time; we were discarding it. Quoting the tail is what separates
        "still downloading" from "broken", and the no-output case says which one it is too —
        silence is itself a fact, and claiming a cause we do not have would be worse.

        SANITIZED here, at the one place the untrusted bytes enter a message — but
        sanitizing only strips fence-forgery glyphs, not credential-shaped content, and a
        quoted tail is exactly what ``McpCallError.safe_summary`` must NOT carry (security
        review, ``last_error`` leak channel): the raw message this builds is for the probe's
        own ephemeral HTTP response ONLY. ``_explain_hint`` is the companion that goes
        everywhere else.
        """
        if self._stderr is not None:
            self._stderr.settle()  # the child is already dead here — its last line still counts
        tail = self._stderr.error_tail(MAX_ERROR_TAIL_CHARS) if self._stderr else ""
        clean = sanitize_untrusted(tail, max_len=MAX_ERROR_TAIL_CHARS).strip()
        if not clean:
            return " (the command printed nothing)"
        return f" — last output: {clean}"

    def _explain_hint(self) -> str:
        """``_explain``'s safe twin: says WHETHER the child printed something, never WHAT.

        Feeds ``McpCallError.safe_summary`` — the persisted ``last_error`` and the app log,
        neither of which may carry a raw quote of a locally spawned process's stderr (it can
        contain anything the child printed, including a credential the user's own command
        line embedded). A character count still answers the question ``_explain`` exists
        for — "was this still working or already silent" — without repeating a byte of it;
        the full quote remains available through ``str(exc)`` for the one channel built to
        carry it, the synchronous probe's own HTTP response.
        """
        if self._stderr is None:
            return ""
        lines = self._stderr.lines()
        if not lines:
            return " (the command printed nothing)"
        chars = sum(len(line) for line in lines)
        return f" — the command wrote {chars} chars of output (see Test Connect for detail)"

    @staticmethod
    def _rpc_error_hint(msg: Any) -> str:
        """``msg``'s safe twin, for the ONE raise site that quotes a JSON-RPC ``error.message``
        straight off the wire (``rpc``, above): says an error came back and roughly how long
        its message was, never what it said.

        The field arrives over STDOUT during ``initialize``/``tools/list`` — not stderr, so
        ``_explain_hint`` doesn't cover it — but it is exactly as child-controlled as a
        stderr line, and ``connect_enabled()`` exercises this raise site unattended on every
        boot. Feeds ``McpCallError.safe_summary`` for the same two channels ``_explain_hint``
        protects — the persisted ``last_error`` and the app log — while the full quote stays
        on ``str(exc)`` for the synchronous probe's own HTTP response.
        """
        text = msg if isinstance(msg, str) else ("" if msg is None else str(msg))
        if not text:
            return " (no message)"
        return f" ({len(text)} chars)"

    def _read_capped_line(self, stdout) -> tuple[bytes, bool]:
        """Return ONE newline-delimited frame, capped at ``MAX_LINE_BYTES``, preferring bytes
        already buffered in ``self._rxbuf`` from a PREVIOUS over-read before doing another
        ``read1()`` — the persistent-buffer fix for security-review H1.

        The client used to be spawn-per-call, so a single reply line was the whole story and
        anything after the first newline in a chunk was assumed to belong to a "next frame"
        that could never exist. That assumption broke once a connection started persisting for
        the app's lifetime (ADR ``2026-08-22-external-mcp-stdio-connections-persist-for-the-
        app-lifetime``): a real server can pipeline an async ``notifications/*`` line and the
        actual reply in one ``write()``+``flush()``, landing in the SAME ``read1()`` chunk —
        dropping the bytes after the first newline silently threw away the real reply, and the
        loop then blocked on a child with nothing left to say until the watchdog killed it.
        Now those bytes are kept in ``self._rxbuf`` and consumed first, whether that next
        consumer is later in THIS ``_read_until`` loop (skipping the unmatched notification) or
        a LATER ``rpc()`` call on this same long-lived session.

        Returns ``(line_without_newline, complete)``:
          * ``(bytes, True)`` — a full line terminated by ``\\n`` (the normal case);
          * ``(b"", False)`` — clean EOF before any byte (the child exited / no reply);
          * ``(partial, False)`` — the line reached ``MAX_LINE_BYTES`` without a newline (an
            oversized/never-terminated line — the caller aborts and kills the child, so the
            unrecoverable partial buffer is dropped along with it; we NEVER buffer past the
            cap, so a multi-GB line can't OOM us — security-review B1).

        Reads in bounded ``read1`` chunks so we stop the moment the cap is reached rather than
        letting an unbounded read grow the buffer past it."""
        while True:
            nl = self._rxbuf.find(b"\n")
            if nl != -1:
                line = bytes(self._rxbuf[:nl])
                del self._rxbuf[: nl + 1]
                return line, True
            if len(self._rxbuf) >= MAX_LINE_BYTES:
                partial = bytes(self._rxbuf)
                self._rxbuf.clear()
                return partial, False  # hit the cap with no newline — over-limit
            remaining = MAX_LINE_BYTES - len(self._rxbuf)
            chunk = stdout.read1(min(_READ_CHUNK_BYTES, remaining))
            if not chunk:
                # EOF — return whatever (possibly nothing) is buffered as an incomplete line.
                partial = bytes(self._rxbuf)
                self._rxbuf.clear()
                return partial, False
            self._rxbuf += chunk

    def _kill_proc(self, proc: subprocess.Popen) -> None:
        """SIGKILL the child's whole process group (best-effort) — used to stop a flooding
        server the moment it breaches the byte cap, so it can't keep streaming."""
        try:
            kill_tree(proc.pid)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
