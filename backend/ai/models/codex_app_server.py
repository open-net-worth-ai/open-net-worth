"""Codex **app-server** transport — a persistent JSON-RPC child instead of a spawn per turn.

This is the streaming half of the Codex provider. ``codex exec --json`` on 0.146.0 emits
whole ``item.completed`` envelopes and no reasoning at all, so a turn shows the user nothing
until the subprocess exits. ``codex app-server`` speaks JSON-RPC 2.0 over stdio and emits
``item/agentMessage/delta`` (157 deltas on one measured answer) plus
``item/reasoning/summaryTextDelta`` — real token streaming and real thinking.

Both external harnesses that wrap Codex moved to this transport for the same reason
(OpenClaw dropped its bundled ``codex-cli`` backend entirely; Hermes routes ``openai/*`` turns
through the app-server). Where we deliberately DIVERGE from both: they hand Codex the tool
loop and the thread state. We keep ours.

    persistent process  ✔  (no cold start, no re-init per turn)
    streaming + reasoning  ✔
    Codex-owned thread/compaction  ✘  — a fresh ephemeral thread per USER turn; within one
                                        turn the thread stays open across tool rounds

``assistant.run`` owns the transcript (folding, checkpoints, rehydration) and re-sends it at the
start of every user turn; Codex never resumes a thread of ours across turns, so a CLI-side
conversation cannot silently diverge from the one we persist. Tools are registered on the
thread as REAL (dynamic) tools — but Codex never executes one: the host captures the call and
runs it itself, because every write in this app must pass a service choke point with our audit
and cost accounting. The turn PARKS at the call and the host answers it in-thread next round
(:mod:`backend.ai.models.codex_live_turn`, ADR ``2026-10-05-codex-turns-stay-open-across-tool-rounds``). Where the build cannot do that, tools
ride in the prompt manifest and come back through :class:`ToolCallScanner`. RFC/ADR
``2026-08-01-codex-app-server-transport``, ``2026-10-03-codex-tools-are-native-calls-the-host-runs``.

Isolation is unchanged in intent from the ``exec`` contract, but enforced differently: the
app-server has no ``--ignore-user-config`` flag, so the operator's plugins/skills/marketplaces
are kept out via an isolated ``CODEX_HOME`` (see :mod:`backend.ai.models.codex_home`) plus a
``config`` block on ``thread/start`` carrying the same overrides ``exec`` passed with ``-c``.
``baseInstructions`` REPLACES Codex's built-in agent preamble, which is what takes a measured
turn from 14k input tokens to ~10k.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import shutil
import signal
import time
from typing import AsyncGenerator, Optional

from backend.core.child_env import scrubbed_environ
from backend.core.errors import ConfigurationError, ExternalServiceError
from backend.core.report_redact import redact_text

from .cli_tool_protocol import write_image_files  # noqa: F401 — LiveTurn calls it via this module
from .codex_home import has_login, isolated_home

log = logging.getLogger(__name__)

# A single JSONL frame (a whole completed item, or a turn/completed carrying every item)
# routinely exceeds asyncio's default 64 KiB StreamReader limit.
_STDOUT_LIMIT = 8 * 1024 * 1024
# Hard per-turn ceiling — a hung server must surface as a clean error, never a turn that
# hangs forever. Mirrors the exec transport's contract.
_DEFAULT_TIMEOUT_SECONDS = 300.0
# The handshake is local and cheap; if it does not complete the binary is wrong or broken.
_INITIALIZE_TIMEOUT_SECONDS = 30.0
_STDERR_TAIL_LINES = 5
_STDERR_LINE_CHARS = 300
_STDERR_QUOTE_CHARS = 500
_STDERR_SETTLE_SECONDS = 1.0
# Bound the fan-out: a subscription's rate limit is per-ACCOUNT, so parallel turns buy
# nothing and can trip it. Unlike the exec transport this is NOT about process count — the
# process is shared — purely about concurrent turns against the same account.
_DEFAULT_MAX_CONCURRENCY = 2
# Orphan-queue backstop: a notification for a thread nobody is reading must not accumulate
# forever if a turn dies between registering and draining.
_MAX_ORPHAN_QUEUES = 32
# How many times a turn that ended on `commentary` with nothing to act on may be resumed.
# ONE is deliberate: a model that narrates twice in a row is not going to act on the third
# ask, and every continuation is a real request against the user's rate-limited quota.
_MAX_CONTINUATIONS = 1

# Sent to resume a turn the model abandoned after its preamble. Deliberately terse and
# imperative — the calling contract is already in `baseInstructions`; repeating it here
# would just give the model more to narrate about.
_CONTINUE_NUDGE = (
    "Continue. Do it now: if you need a tool, your entire reply must be the single "
    "<tool_call> block. Otherwise give the final answer. Do not restate your plan."
)

# ── native tools ──────────────────────────────────────────────────────────────
# The first release verified end to end (dynamic tools are delivered through code mode there, so
# the host binary must be present too). Older builds keep the text protocol.
_MIN_NATIVE_TOOLS_VERSION = (0, 160, 0)
_CODE_MODE_HOST = "codex-code-mode-host"
# Sibling calls of one model response arrive within milliseconds of each other; after the
# first, wait this long for the rest before ending the turn.
_PARALLEL_CALL_GRACE_SECONDS = 0.4
# How long to wait for the interrupted turn's own `turn/completed` (it carries the usage).
_INTERRUPT_WAIT_SECONDS = 8.0
_MAX_NATIVE_CALLS = 16
# Thread ids whose turn already ended: late frames for them are dropped, not queued.
_RELEASED_MEMORY = 256
# What the function-name field of a Responses-API tool accepts.
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NOT_RUN_HERE = (
    "Not executed in this session: the host runs the call and returns its result in the "
    "next message."
)

_NATIVE_CONTINUE_NUDGE = (
    "Continue. Do it now: if you need a tool, call it. Otherwise give the final answer. "
    "Do not restate your plan."
)

_CLIENT_NAME = "open-net-worth"
_CLIENT_VERSION = "1.0.0"

# Codex's own advertised ladder (`model/list` → supportedReasoningEfforts) for gpt-5.6-sol is
# low/medium/high/xhigh/max/ultra — our ladder maps 1:1. The exec transport FOLDS xhigh/max to
# high because `-c model_reasoning_effort` rejects them; that fold is lossy and must not be
# carried over here. "none"/"off" still FLOOR to low: Codex has no off rung, and passing
# nothing is not neutral — it hands the turn Codex's own (higher) default.
_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


def _effort(effort: str) -> str:
    level = (effort or "").strip().lower()
    if level in ("none", "off"):
        return "low"
    return level if level in _EFFORTS else ""


def _timeout_seconds() -> float:
    try:
        return max(
            5.0, float(os.getenv("ONW_CODEX_APP_SERVER_TIMEOUT") or _DEFAULT_TIMEOUT_SECONDS)
        )
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def _max_concurrency() -> int:
    try:
        return max(1, int(os.getenv("ONW_CODEX_CLI_MAX_CONCURRENCY") or _DEFAULT_MAX_CONCURRENCY))
    except ValueError:
        return _DEFAULT_MAX_CONCURRENCY


# The thread config block — the app-server equivalent of the exec transport's `-c` flags.
# Load-bearing for the same reasons documented there: this is a MODEL BACKEND, not a coding
# agent, and the host owns the tool loop.
_THREAD_CONFIG = {
    "mcp_servers": {},
    "tools": {"web_search": False},
    "project_doc_max_bytes": 0,
    "history": {"persistence": "none"},
}


def native_tools_enabled() -> bool:
    """``ONW_CODEX_NATIVE_TOOLS=0`` pins the text protocol (an operator's escape hatch)."""
    return (os.getenv("ONW_CODEX_NATIVE_TOOLS") or "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "text",
    )


def dynamic_tool_specs(tools: list[dict]) -> Optional[list[dict]]:
    """The turn's offered tools as ``thread/start`` ``dynamicTools``, or None when they
    cannot all be registered (an unusable name) — the caller then uses the text protocol
    for that turn rather than offering the model a partial surface."""
    specs = []
    for t in tools:
        name = t.get("name") or ""
        if not _TOOL_NAME_RE.match(name):
            return None
        specs.append(
            {
                "type": "function",
                "name": name,
                "description": (t.get("description") or "").strip(),
                "inputSchema": t.get("input_schema") or {"type": "object", "properties": {}},
            }
        )
    return specs


def _parse_version(user_agent: str) -> tuple[int, ...]:
    """``x/0.160.0 (Mac OS …)`` -> ``(0, 160, 0)``; empty when the build reports none."""
    match = re.match(r"[^/\s]+/(\d+)\.(\d+)\.(\d+)", user_agent or "")
    return tuple(int(g) for g in match.groups()) if match else ()


def _code_mode_host_present(cli: str) -> bool:
    """Dynamic tools ride code mode, which needs its host binary beside ``codex`` or on PATH."""
    search = os.pathsep.join([os.path.dirname(os.path.realpath(cli)), os.environ.get("PATH", "")])
    return shutil.which(_CODE_MODE_HOST, path=search) is not None


# "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header" is what
# Codex says with no login at all; the other is its expired or rotated login.
_AUTH_REJECTION = re.compile(r"\b401\b.*unauthorized|sign in again", re.IGNORECASE | re.DOTALL)


def is_auth_rejection(text: str) -> bool:
    """True when Codex refused a turn for the login it holds, not for the request."""
    return bool(_AUTH_REJECTION.search(text or ""))


class NativeToolsUnavailable(ExternalServiceError):
    """The app-server refused the turn's tool registration — run it on the text protocol."""


def child_env(api_key: str, base_env: Optional[dict] = None, native_tools: bool = False) -> dict:
    """The child environment: isolated Codex config tree, operator's ``HOME`` preserved.

    The filter is ``core.child_env``'s, shared with the exec transport so the two stay in
    lockstep. ``CODEX_HOME`` is then set (not merely cleared) to a directory we own; ``HOME``
    is deliberately left alone.
    """
    env = scrubbed_environ(base_env)
    if api_key:
        env["OPENAI_API_KEY"] = api_key
    env["CODEX_HOME"] = isolated_home(native_tools)
    env["CI"] = env.get("CI", "1")
    return env


class AppServerClient:
    """One persistent ``codex app-server`` child, multiplexed across turns.

    A single reader task owns stdout and fans frames out two ways: JSON-RPC responses go to
    the future waiting on that id, notifications go to the queue registered for their
    ``threadId``. Queues are created lazily by ``threadId`` rather than handed out at
    subscribe time, because ``thread/started`` can race the ``thread/start`` response.
    """

    def __init__(self, cli_path: str, api_key: str = "", native_tools: bool = False):
        self.cli_path = cli_path
        self.api_key = api_key
        # A native-tools client is a SEPARATE child with its own CODEX_HOME (code mode on).
        self.native_tools = native_tools
        self.native_ready = False
        self._released: "collections.deque[str]" = collections.deque(maxlen=_RELEASED_MEMORY)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.Task] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._start_error: Optional[BaseException] = None
        # True when this child cannot be holding a working login: it started with none, or a
        # turn was refused. A child reads its login once, so signing in needs a new child.
        self._login_stale = False
        self._stderr_tail: "collections.deque[str]" = collections.deque(maxlen=_STDERR_TAIL_LINES)
        self._stderr_drain: Optional[asyncio.Future] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def ensure_started(self) -> None:
        """Spawn + handshake if we have no live child. Safe to call on every turn."""
        async with self._lock:
            if self.alive:
                if not (self._login_stale and self._has_login()):
                    return
                log.info("codex_app_server: a login appeared — restarting the child to read it")
            await self._reap()
            resolved = shutil.which(self.cli_path)
            if not resolved:
                raise ConfigurationError(
                    f"Codex CLI not found (looked for {self.cli_path!r}). Install it "
                    "(https://developers.openai.com/codex/cli), run `codex login` once, and "
                    "make sure it is on the PATH of the process running this app — or set "
                    "the CLI path in Settings → AI."
                )
            self._proc = await asyncio.create_subprocess_exec(
                resolved,
                "app-server",
                "--listen",
                "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env(self.api_key, native_tools=self.native_tools),
                limit=_STDOUT_LIMIT,
            )
            self._login_stale = not self._has_login()
            self._reader = asyncio.ensure_future(self._read_loop())
            self._stderr_tail.clear()
            self._stderr_drain = asyncio.ensure_future(_drain(self._proc.stderr, self._stderr_tail))
            handshake: dict = {"clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION}}
            if self.native_tools:
                # `thread/start.dynamicTools` is gated behind the experimental API.
                handshake["capabilities"] = {"experimentalApi": True}
            try:
                init = await asyncio.wait_for(
                    self._request("initialize", handshake),
                    timeout=_INITIALIZE_TIMEOUT_SECONDS,
                )
            except (asyncio.TimeoutError, ExternalServiceError) as exc:
                exit_note = await _exit_summary(self._proc)
                await self._reap()
                raise ExternalServiceError(
                    f"Codex app-server did not complete its handshake: {exc}"
                    + exit_note
                    + await self._stderr_summary()
                ) from exc
            self._notify("initialized")
            self.native_ready = (
                self.native_tools
                and native_tools_enabled()
                and _parse_version((init or {}).get("userAgent") or "") >= _MIN_NATIVE_TOOLS_VERSION
                and _code_mode_host_present(resolved)
            )
            log.info(
                "codex_app_server: started  pid=%s native_tools=%s",
                self._proc.pid,
                self.native_ready,
            )

    def _has_login(self) -> bool:
        return bool(self.api_key) or has_login(self.native_tools)

    def note_auth_rejected(self) -> None:
        """A turn came back 401: whatever this child read at spawn is no good. The next turn
        re-reads the login if there is one, which is how signing in again takes effect."""
        self._login_stale = True

    async def _stderr_summary(self) -> str:
        """What the dead child printed — otherwise a failed start gives no reason at all."""
        if self._stderr_drain is not None:
            await asyncio.wait({self._stderr_drain}, timeout=_STDERR_SETTLE_SECONDS)
        text = " | ".join(self._stderr_tail)[-_STDERR_QUOTE_CHARS:]
        if not text:
            return ""
        log.warning("codex_app_server: child stderr before the failed start: %s", text)
        return f". Codex printed: {redact_text(text)}"

    async def aclose(self) -> None:
        async with self._lock:
            await self._reap()

    async def _reap(self) -> None:
        """Tear the child and its reader down, failing anything still waiting on it."""
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (ProcessLookupError, OSError, asyncio.TimeoutError):
                pass
        self._fail_pending(ExternalServiceError("Codex app-server stopped"))
        self._queues.clear()

    def _fail_pending(self, exc: BaseException) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    # ── framing ───────────────────────────────────────────────────────────────

    def _write(self, msg: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise ExternalServiceError("Codex app-server is not running")
        proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    async def _request(self, method: str, params: Optional[dict] = None):
        self._next_id += 1
        req_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._write(msg)
        except ExternalServiceError:
            self._pending.pop(req_id, None)
            raise
        return await fut

    async def _read_loop(self) -> None:
        """Own stdout: responses → futures, notifications → per-thread queues."""
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except (TypeError, ValueError):
                    log.debug("codex_app_server: skipping non-JSON frame: %.200s", line)
                    continue
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    self._resolve(msg)
                elif msg.get("method") and "id" in msg:
                    self._on_server_request(msg)
                elif msg.get("method"):
                    self._dispatch(msg)
        except (asyncio.CancelledError, OSError, ValueError):
            pass
        finally:
            # The child died (or we cancelled). Anything waiting must learn now rather than
            # hang until the per-turn deadline.
            self._fail_pending(ExternalServiceError("Codex app-server exited"))
            for q in list(self._queues.values()):
                q.put_nowait(None)

    def _resolve(self, msg: dict) -> None:
        fut = self._pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        if "error" in msg:
            err = msg.get("error") or {}
            fut.set_exception(
                ExternalServiceError(f"Codex app-server error: {err.get('message') or err}")
            )
        else:
            fut.set_result(msg.get("result"))

    def _on_server_request(self, msg: dict) -> None:
        """A server->client request. Only ``item/tool/call`` is ours to answer, and the turn
        that registered the tool answers it; anything else is refused so the server never
        waits on a client that does not implement it."""
        if msg.get("method") == "item/tool/call":
            thread_id = (msg.get("params") or {}).get("threadId")
            if thread_id in self._queues:
                self._dispatch(msg)
                return
            # A call for a turn nobody is reading (finished, unknown, or another client's).
            log.warning("codex_app_server: declining a tool call for an unowned thread")
            self._respond(msg["id"], _declined_result())
            return
        log.warning("codex_app_server: refusing unsupported server request %r", msg.get("method"))
        self._respond(msg["id"], error={"code": -32601, "message": "not supported by this client"})

    def _respond(self, request_id, result: Optional[dict] = None, error: Optional[dict] = None):
        frame: dict = {"jsonrpc": "2.0", "id": request_id}
        frame.update({"error": error} if error else {"result": result})
        try:
            self._write(frame)
        except ExternalServiceError:
            pass  # the child is gone; nothing is waiting on this answer any more

    def _dispatch(self, msg: dict) -> None:
        params = msg.get("params") or {}
        thread_id = params.get("threadId") if isinstance(params, dict) else None
        if not thread_id:
            return
        if thread_id in self._released:
            return
        q = self._queues.get(thread_id)
        if q is None:
            if len(self._queues) >= _MAX_ORPHAN_QUEUES:
                log.warning("codex_app_server: dropping notification for unknown thread")
                return
            q = asyncio.Queue()
            self._queues[thread_id] = q
        q.put_nowait(msg)

    def _queue_for(self, thread_id: str) -> asyncio.Queue:
        # A turn is about to read this thread, so it is live whatever its history.
        if thread_id in self._released:
            self._released.remove(thread_id)
        q = self._queues.get(thread_id)
        if q is None:
            q = asyncio.Queue()
            self._queues[thread_id] = q
        return q

    def _release(self, thread_id: str) -> None:
        self._queues.pop(thread_id, None)
        self._released.append(thread_id)

    def _interrupt_nowait(self, thread_id: str, turn_id: str) -> None:
        """Best-effort ``turn/interrupt`` that cannot block or raise — for an exit path
        (timeout, error, cancelled consumer) that must not leave the model running on the
        operator's account. The reply is ignored."""
        if not turn_id:
            return
        self._next_id += 1
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id,
                    "method": "turn/interrupt",
                    "params": {"threadId": thread_id, "turnId": turn_id},
                }
            )
        except ExternalServiceError:
            pass

    async def _interrupt(self, thread_id: str, captured: "_CapturedCalls") -> None:
        """End the turn the model is blocked in: its calls are already ours to run."""
        captured.interrupted = True
        captured.interrupted_at = time.monotonic()
        try:
            await asyncio.wait_for(
                self._request(
                    "turn/interrupt", {"threadId": thread_id, "turnId": captured.turn_id}
                ),
                timeout=_INTERRUPT_WAIT_SECONDS,
            )
        except (ExternalServiceError, asyncio.TimeoutError):
            log.warning("codex_app_server: turn/interrupt did not complete cleanly")

    def _decline_captured(self, captured: "_CapturedCalls") -> None:
        """Answer the captured requests so no server-side call is left awaiting a client
        that has moved on. The REAL result reaches the model next round, as transcript."""
        for request_id in captured.request_ids:
            self._respond(request_id, _declined_result())
        captured.request_ids.clear()

    # ── one model turn ────────────────────────────────────────────────────────

    async def run_turn(
        self,
        *,
        instructions: str,
        transcript: str,
        model: str,
        effort: str,
        output_schema: Optional[dict] = None,
        images: Optional[list[dict]] = None,
        should_continue=None,
        dynamic_tools: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        """Start an ephemeral thread, run ONE turn to its end, yield normalised events.

        Yields ``{"kind": …}`` dicts — ``text`` / ``thinking`` / ``message`` / ``usage`` /
        ``done`` — deliberately the same vocabulary ``codex_cli_provider._classify``
        produces, so the provider maps one shape onto the wire format regardless of which
        transport served the turn.

        ``should_continue(phase)`` is consulted when a turn completes, and decides whether
        that completion was REAL: an agentic model narrates before it acts, Codex classifies
        such a message ``commentary``, and a turn that ends on it with nothing to act on is
        resumed once (``_MAX_CONTINUATIONS``).

        This is the one-shot form: with ``dynamic_tools`` the first native call (plus any
        siblings) is captured, the turn is interrupted and the calls are yielded as
        ``{"kind": "tool_calls", …}``. A tool loop that should keep its thread across rounds
        uses :class:`~backend.ai.models.codex_live_turn.LiveTurn` with ``park=True`` instead.
        """
        from .codex_live_turn import LiveTurn

        live = await LiveTurn.start(
            self,
            instructions=instructions,
            transcript=transcript,
            model=model,
            effort=effort,
            output_schema=output_schema,
            images=images,
            should_continue=should_continue,
            dynamic_tools=dynamic_tools,
        )
        events = live.events()
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()
            live.close()


_SIGNAL_HINTS = {
    "SIGKILL": "usually the system ran out of memory, or a container limit stopped it",
    "SIGSEGV": "Codex crashed; a different Codex version may not",
    "SIGILL": "this Codex build may not support this processor",
}


def describe_exit(returncode: Optional[int]) -> str:
    """How a child ended, in words an operator can act on. A signal is the usual silent death:
    nothing reaches stderr, so without this a crash, a memory kill and a missing file all read
    as the same "not running" (2026-11-11, a Docker install no fresh container reproduced).
    A shell-style code above 128 is read as that signal too: the npm launcher passes it on."""
    if returncode is None:
        return "was still running but never answered"
    signum = -returncode if returncode < 0 else returncode - 128 if returncode > 128 else 0
    if not signum:
        return f"exited with code {returncode}"
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = f"signal {signum}"
    detail = [f"exit code {returncode}"] if returncode > 0 else []
    if name in _SIGNAL_HINTS:
        detail.append(_SIGNAL_HINTS[name])
    return f"was stopped by {name}" + (f" ({'; '.join(detail)})" if detail else "")


async def _exit_summary(proc: Optional[asyncio.subprocess.Process]) -> str:
    """``. Codex <how it ended>`` for a child that failed its start, or "" with no child."""
    if proc is None:
        return ""
    try:
        await asyncio.wait_for(proc.wait(), timeout=_STDERR_SETTLE_SECONDS)
    except asyncio.TimeoutError:
        pass
    note = describe_exit(proc.returncode)
    log.warning("codex_app_server: failed start — Codex %s", note)
    return f". Codex {note}"


def _turn_id(response) -> str:
    turn = (response or {}).get("turn") if isinstance(response, dict) else None
    return str((turn or {}).get("id") or "")


def _declined_result() -> dict:
    return {"success": False, "contentItems": [{"type": "inputText", "text": _NOT_RUN_HERE}]}


class _CapturedCalls:
    """The native tool calls one turn made, in arrival order.

    Arguments are validated here, at the boundary: a call whose ``arguments`` is not a JSON
    object (or a string holding one) is dropped and recorded in ``malformed`` — never run
    with defaults, and never guessed at.
    """

    def __init__(self, offered: frozenset = frozenset()) -> None:
        # The names registered on THIS thread. A call for anything else did not come from our
        # registration (a Codex-side tool, a collision, a hallucination) and is never mapped
        # onto a tool of ours by bare name.
        self.offered = offered
        self.calls: list[dict] = []
        # The request ids answering each entry of ``calls`` (a replayed callId adds one).
        self.call_requests: list[list] = []
        # Requests for unusable calls, as (request id, tool name), still to be answered.
        self.rejected: list[tuple] = []
        self.request_ids: list = []
        self.malformed = 0
        self.turn_id = ""
        self.first_at = 0.0
        self.interrupted = False
        self.interrupted_at = 0.0
        self._seen: dict[str, Optional[int]] = {}

    def add(self, msg: dict) -> None:
        params = msg.get("params") or {}
        request_id = msg.get("id")
        self.request_ids.append(request_id)
        if not self.first_at:
            self.first_at = time.monotonic()
        call_id = str(params.get("callId") or "")
        tool = params.get("tool")
        if call_id in self._seen:
            index = self._seen[call_id]
            if index is None:
                self.rejected.append((request_id, tool))
            else:
                self.call_requests[index].append(request_id)
            return
        self._seen[call_id] = None
        if len(self.calls) + self.malformed >= _MAX_NATIVE_CALLS:
            self.malformed += 1  # over the cap: told to the model, never silently dropped
            self.rejected.append((request_id, tool))
            return
        self.turn_id = self.turn_id or str(params.get("turnId") or "")
        args = params.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        if args is None and params.get("arguments") is None:
            args = {}
        name = params.get("tool")
        if (
            not isinstance(args, dict)
            or not isinstance(name, str)
            or params.get("namespace") is not None
            or name not in self.offered
        ):
            self.malformed += 1
            self.rejected.append((request_id, name))
            return
        self._seen[call_id] = len(self.calls)
        self.calls.append({"name": name, "input": args})
        self.call_requests.append([request_id])

    def wait_budget(self) -> float:
        """How long the next frame may take before the turn should move on: unbounded until
        a call is captured, then the sibling grace, then the interrupt's own deadline."""
        if self.interrupted:
            return max(0.0, self.interrupted_at + _INTERRUPT_WAIT_SECONDS - time.monotonic())
        if self.first_at:
            return max(0.0, self.first_at + _PARALLEL_CALL_GRACE_SECONDS - time.monotonic())
        return float("inf")


def classify(msg: dict) -> dict:
    """Normalise ONE app-server notification into ``{kind, text, usage}``.

    Same vocabulary as the exec transport's ``_classify`` so the provider has one mapping:
    ``text`` (a visible delta), ``thinking``, ``message`` (a whole assistant message),
    ``usage``, ``done``, ``error``, ``ignore`` — plus ``retrying``, which only this transport has:
    Codex lost the stream and is trying again by itself (see ``LiveTurn.events``).
    """
    method = msg.get("method") or ""
    p = msg.get("params") or {}

    if method == "item/agentMessage/delta":
        return {"kind": "text", "text": p.get("delta") or "", "usage": {}}
    if method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
        # Reasoning arrives as indexed PARTS, each a self-contained block with its own
        # bold headline. `part` lets the caller tell a boundary from a continuation —
        # without it, part 0's trailing "**" abuts part 1's leading "**" and renders as
        # "…discrepancy****Confirming…" (owner-reported, 2026-08-01). Summary parts and
        # raw-content parts are numbered separately, so the kind is part of the identity.
        # So is the reasoning ITEM: every item numbers its parts from 0, so a turn that holds
        # several (or a continuation turn) would repeat ("…summaryTextDelta", 0) and fuse
        # "…function inspectionFiltering tool names…" (owner-reported, 2026-10-03).
        index = p.get("summaryIndex")
        if index is None:
            index = p.get("contentIndex")
        return {
            "kind": "thinking",
            "text": p.get("delta") or "",
            "part": (method, p.get("itemId"), index),
            "usage": {},
        }
    if method == "item/started":
        # The phase arrives HERE, before the first delta — the delta notification carries
        # no phase of its own, so this is the only place to learn it in time.
        item = p.get("item") or {}
        if item.get("type") == "agentMessage":
            return {"kind": "phase", "text": item.get("phase") or "", "usage": {}}
        return {"kind": "ignore", "text": "", "usage": {}}
    if method == "item/completed":
        item = p.get("item") or {}
        if item.get("type") == "agentMessage":
            return {
                "kind": "message",
                "text": item.get("text") or "",
                "phase": item.get("phase") or "",
                "usage": {},
            }
        return {"kind": "ignore", "text": "", "usage": {}}
    if method == "thread/tokenUsage/updated":
        usage = p.get("tokenUsage") or {}
        # `last` is this turn; `total` is the thread. We run one turn per thread so they
        # agree, but `last` is the honest field to read.
        breakdown = usage.get("last") or usage.get("total") or {}
        # `total` rides along for a turn that spans several host rounds (codex_live_turn):
        # each round's share is the difference in the thread total, not the latest `last`.
        return {"kind": "usage", "text": "", "usage": breakdown, "total": usage.get("total") or {}}
    if method == "turn/completed":
        turn = p.get("turn") or {}
        if turn.get("status") == "failed" or turn.get("error"):
            return {"kind": "error", "text": _error_text(turn), "usage": {}}
        return {"kind": "done", "text": _final_text(turn), "usage": {}}
    if method == "turn/failed":
        return {"kind": "error", "text": _error_text(p), "usage": {}}
    if method == "error":
        # "Reconnecting... 2/5" says nothing; the reason it is reconnecting is in the details.
        if p.get("willRetry") is True:
            err = p.get("error") if isinstance(p.get("error"), dict) else {}
            detail = err.get("additionalDetails") or err.get("message") or ""
            return {"kind": "retrying", "text": str(detail), "usage": {}}
        return {"kind": "error", "text": _error_text(p), "usage": {}}
    return {"kind": "ignore", "text": "", "usage": {}}


def strictify_schema(schema: dict) -> dict:
    """A JSON Schema the Responses API's STRICT structured-output mode will accept.

    Codex sends `outputSchema` on as `response_format: codex_output_schema`, which is strict,
    and strict mode has two requirements ordinary JSON Schema does not:

      * every object sets ``additionalProperties: false``
      * every object's ``required`` lists ALL of its properties (optionality is expressed by
        a nullable TYPE, never by omission from ``required``)

    Our schemas are written for the Anthropic tool-input dialect, which demands neither, so
    EVERY schema-bearing codex turn was rejected with ``invalid_json_schema`` — 100% of them,
    from the first one. It looked like flakiness because the failure was swallowed twice
    over: the parse discarded the payload down to the word "error" (see `_describe_error`),
    and the provider then silently rerouted the turn to `codex exec`, which takes no schema
    and so always "worked". Owner repro 2026-08-09, statement import on gpt-5.6-sol.

    Normalising here rather than at each call site means a schema authored for any other
    provider stays portable — no caller has to know which dialect codex speaks.
    """
    if not isinstance(schema, dict):
        return schema
    out = {k: strictify_schema(v) if isinstance(v, dict) else v for k, v in schema.items()}
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if isinstance(schema.get(key), list):
            out[key] = [strictify_schema(s) if isinstance(s, dict) else s for s in schema[key]]
    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties")
        if isinstance(props, dict):
            out["properties"] = {k: strictify_schema(v) for k, v in props.items()}
            out["additionalProperties"] = False
            # Strict mode rejects a property that is absent from `required`, so the whole
            # property set goes in. A field the caller meant to be optional stays optional in
            # practice — the model may still answer "" / null — it just has to be present.
            out["required"] = list(out["properties"].keys())
    return out


def _error_text(payload: dict) -> str:
    """The readable reason in an error-bearing frame, which is either at the top (older builds)
    or under ``error`` with its cause beside it. Anything else is serialised for diagnosis."""
    if payload.get("message"):
        return str(payload["message"])
    err = payload.get("error")
    if not isinstance(err, dict) or not err.get("message"):
        return str(err) if err and not isinstance(err, dict) else _describe_error(payload)
    message, details = str(err["message"]), err.get("additionalDetails")
    return f"{message}: {details}" if details and str(details) not in message else message


def dropped_mid_reply(detail: str) -> str:
    """The error for a stream lost after text was shown, when Codex would re-send the whole reply
    and no event can take back what the chat already has."""
    cause = f" ({detail})" if detail else ""
    return f"The connection to the model dropped partway through the reply{cause}. Send your message again."


def _describe_error(payload: dict) -> str:
    """A diagnosable string for an error frame carrying no ``message``.

    The scar (owner, 2026-08-09): a ``method: "error"`` notification whose params had no
    ``message`` key was collapsed to the literal word "error", so the operator's only record
    of a failed statement read was ``app-server reported an error: error`` — the payload that
    would have said WHY was discarded at the parse. Serialise whatever the frame actually
    carried instead; truncated because this lands in a log line and an exception message.
    """
    if not payload:
        return "app-server sent an error frame with no detail"
    for key in ("error", "reason", "detail", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:400]
        if isinstance(value, dict) and value:
            return json.dumps(value, default=str)[:400]
    return json.dumps(payload, default=str)[:400]


def _final_text(turn: dict) -> str:
    """The turn's last assistant message, used only when no deltas were streamed."""
    for item in reversed(turn.get("items") or []):
        if isinstance(item, dict) and item.get("type") == "agentMessage":
            return item.get("text") or ""
    return ""


def usage_fields(breakdown: dict) -> dict:
    """app-server camelCase usage → the field names the provider's ``_Turn`` expects."""
    return {
        "input_tokens": int(breakdown.get("inputTokens") or 0),
        "cached_input_tokens": int(breakdown.get("cachedInputTokens") or 0),
        "output_tokens": int(breakdown.get("outputTokens") or 0),
    }


async def _drain(stream, tail: Optional["collections.deque[str]"] = None) -> None:
    """Read stderr to EOF so a full pipe can never block the child mid-turn, keeping the last
    lines in ``tail`` for the one error that needs them."""
    if stream is None:
        return
    try:
        while True:
            chunk = await stream.readline()
            if not chunk:
                return
            text = chunk.decode("utf-8", "replace").strip()
            if text:
                log.debug("codex_app_server stderr: %.300s", text)
                if tail is not None:
                    tail.append(text[:_STDERR_LINE_CHARS])
    except (asyncio.CancelledError, OSError, ValueError):
        return


# One client per (event loop, cli path, key): the module is imported once, but the app,
# tests and background workers may each run their own loop, and both the subprocess
# transport and the futures are bound to the loop that created them.
_clients: "dict[tuple, AppServerClient]" = {}
_semaphores: "dict[int, asyncio.Semaphore]" = {}


def client_for(cli_path: str, api_key: str = "", native_tools: bool = False) -> AppServerClient:
    loop_id = id(asyncio.get_running_loop())
    key = (loop_id, cli_path, api_key, native_tools)
    client = _clients.get(key)
    if client is None:
        client = AppServerClient(cli_path, api_key, native_tools)
        _clients[key] = client
    return client


def semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    sem = _semaphores.get(loop_id)
    if sem is None:
        sem = asyncio.Semaphore(_max_concurrency())
        _semaphores[loop_id] = sem
    return sem
