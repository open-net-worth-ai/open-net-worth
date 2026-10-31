"""Codex headless-CLI provider.

Runs the locally-installed ``codex`` CLI as an AI provider, so a self-hoster with a ChatGPT
Plus/Pro/Business subscription can spend THAT instead of buying separate OpenAI API credits
for the same models. It is the exact twin of :mod:`backend.ai.models.claude_cli_provider` —
same shape, same protocol, same isolation contract — for the other vendor's coding CLI.

**Two transports, one provider.** A turn is served by the persistent ``codex app-server``
(:mod:`backend.ai.models.codex_app_server`) whenever it can start, because ``codex exec
--json`` on 0.146.0 emits no deltas and no reasoning — the user sees nothing until the
subprocess exits. A failing app-server is RETRIED and then RAISED — it is never swapped for
``exec`` behind the user's back (owner, 2026-08-09; see ``_APP_SERVER_ATTEMPTS``). ``exec``
now serves an explicit ``ONW_CODEX_TRANSPORT=exec`` pin and image-bearing ``complete()``
calls; streaming app-server turns carry images as native ``localImage`` inputs. Everything
below describes the ``exec`` contract. ADR ``2026-08-01-codex-app-server-transport``.

Isolation applies to BOTH: ``CODEX_HOME`` is not cleared but REPLACED with a tree we own
(:mod:`backend.ai.models.codex_home`), because clearing it merely makes the CLI fall back to
the operator's real ``~/.codex`` — plugins and skills included. Measured: 53,983 input tokens
for ``"What is 17*23?"``, with the model shelling out to read skill files before answering.

Invocation contract (see ``.claude/rfcs/2026-07-22-codex-cli-provider.md`` §3)::

    codex exec --json --color never --skip-git-repo-check --sandbox read-only
               [--model <model>]
               -c approval_policy="never" -c mcp_servers={} -c tools.web_search=false
               -c project_doc_max_bytes=0 -c history.persistence="none"
               [-c model_reasoning_effort="<effort>"]
               -

with the whole prompt (system instructions + flattened transcript) on **stdin**, which is
what the trailing ``-`` selects.

Six of those are load-bearing and must not be dropped:

* ``--json`` — the machine-readable event stream. Without it stdout is human prose.
* ``--sandbox read-only`` + ``-c approval_policy="never"`` — this is a MODEL BACKEND, not
  a coding agent. It must never edit a file and must never stall on an approval prompt in
  a headless process.
* ``-c mcp_servers={}`` — ignore whatever MCP servers the user happens to have configured,
  so the model's reachable surface is exactly what we granted it.
* ``-c tools.web_search=false`` — the CLI's own web tool is OFF; the host owns the tool
  loop (this app has its own ``web_search`` tool with its own cost accounting).
* ``-c project_doc_max_bytes=0`` — never read an ``AGENTS.md``. Belt-and-braces with the
  empty temp cwd below: without it a repo doc would silently prepend kilobytes of
  unrelated instructions to every turn (the ``--setting-sources ""`` lesson from the
  Claude CLI transport, which was observed live there).
* ``-c history.persistence="none"`` — don't litter the user's ``~/.codex`` with our turns.

Statelessness is deliberate: we never pass ``resume``, so the CLI holds no conversation of
ours. ``assistant.run`` owns the transcript (folding, checkpoints, rehydration) and
re-sends it every turn — a CLI-side session would silently diverge from it.

Two contracts differ from the Claude CLI transport, both because the tool differs:

* **No system-prompt flag.** ``codex exec`` has no ``--system-prompt``, so the system text
  is rendered into the single stdin prompt as a fenced ``# System instructions`` section.
* **No dollar figure.** Codex reports token counts, never a cost. So cost is derived from
  the usual OpenAI price table (``pricing.cost_usd_openai``) exactly like the
  ``openai_compatible`` transport does, and stamped ``plan_included`` when running on the
  user's own ChatGPT login — where that figure is a list-price *equivalent* for usage the
  subscription already covers, not a charge.

Event schema: two are accepted, deliberately. Current Codex builds emit a typed
``thread.started`` / ``item.*`` / ``turn.completed`` stream; older ones emit the
``{"id": …, "msg": {"type": …}}`` envelope (``agent_message_delta`` / ``task_complete`` /
``token_count``). Both are normalised here rather than pinning one, because the schema is
still moving and a self-hoster's installed version is not ours to choose.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
from typing import AsyncGenerator, List, Optional

from backend.ai.tool_text import UNPARSED_CALL_NOTICE
from backend.core.child_env import scrubbed_environ
from backend.core.errors import ConfigurationError, ExternalServiceError, ProviderTimeoutError

from . import codex_app_server, codex_live_turn, codex_turn_claims
from .base import AIProvider, log_text_cap
from .cli_tool_protocol import (
    NATIVE_TOOLS_NOTE,
    ToolCallScanner,
    flatten_messages_with_images,
    render_tool_manifest,
    write_image_files,
)
from .codex_home import isolated_home
from .pricing import cost_usd_openai
from .telemetry import record_structured_output_error

log = logging.getLogger(__name__)

DEFAULT_CLI_PATH = "codex"

# Which transport serves a turn. The app-server is preferred — it streams deltas and
# reasoning, and reuses one warm process. `ONW_CODEX_TRANSPORT=exec|app_server` pins it.
# See ADR 2026-08-01-codex-app-server-transport.
_TRANSPORTS = ("app_server", "exec")

# A failing app-server RETRIES, then RAISES — it never degrades to `codex exec` (owner,
# 2026-08-09: "app server must work"). The old auto-fallback was a sticky process-wide switch
# flipped by one transient error, so a single hiccup quietly moved every later turn onto a
# transport that streams nothing, and the operator's only clue was one WARNING line in the
# log. Measured fallout: an `app-server reported an error: error` at 00:01:37 rerouted a
# statement import to exec, and the degraded read was surfaced as an ordinary result.
#
# The error message deliberately does NOT offer exec as a workaround (owner: "i don't want
# users to switch to exec"). Telling someone to pin the degraded transport is the same
# silent-degrade wearing a politer face — the app-server failing is a bug to fix, not a
# setting to route around. Fix the CLI, don't reroute the user.
_APP_SERVER_ATTEMPTS = 3
_APP_SERVER_RETRY_DELAY = 0.5


def _preferred_transport() -> str:
    pinned = (os.getenv("ONW_CODEX_TRANSPORT") or "").strip().lower()
    return pinned if pinned in _TRANSPORTS else "app_server"


# A 400-class rejection is the SAME on every attempt — a bad schema, an unknown model, a
# malformed request. Retrying it burns the user's wait for nothing and buries the real cause
# under two duplicate WARNINGs (measured 2026-08-09: `invalid_json_schema` retried 3× at ~3s
# each before surfacing). Only genuinely transient failures earn a retry.
_NON_RETRYABLE_MARKERS = (
    "invalid_request_error",
    "invalid_json_schema",
    "unsupported",
    "not_found",
)


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return not any(marker in text for marker in _NON_RETRYABLE_MARKERS)


def _app_server_failed(exc: Exception, attempt: int) -> ExternalServiceError:
    """The terminal error — phrased for the operator who sees it.

    ``attempt`` is where it STOPPED, which is 1 for a non-retryable rejection: saying
    "after 3 attempts" there would be a lie, and would send someone hunting a flaky process
    when the request itself was malformed."""
    log.error(
        "codex_cli: Codex app-server failed after %d attempt(s) — surfacing to the user: %s",
        attempt,
        exc,
    )
    tried = "1 attempt" if attempt == 1 else f"{attempt} attempts"
    return ExternalServiceError(
        f"Codex app-server failed after {tried}: {exc}. "
        "Check that the `codex` CLI is installed and current (`codex --version`) and that "
        "`codex app-server` starts."
    )


# Every turn is a subprocess AND a subscription's rate limit is per-ACCOUNT, so parallel
# spawns buy nothing and can trip it. Bound the fan-out; waiters queue rather than fail.
_DEFAULT_MAX_CONCURRENCY = 2
# Hard per-call ceiling — a hung CLI must surface as a clean error, never as a connector
# turn that hangs forever.
_DEFAULT_TIMEOUT_SECONDS = 300.0
# A single JSONL line (a whole completed item) routinely exceeds asyncio's default 64 KiB
# StreamReader limit, which would raise LimitOverrunError mid-turn.
_STDOUT_LIMIT = 8 * 1024 * 1024
# How many times to silently re-spawn a call whose CLI subprocess exited cleanly but never
# emitted a terminal completion event (mirrors the Claude CLI transport's identical guard).
# Bounded: a genuinely broken invocation would fail identically on every attempt, so this
# must not become an unbounded retry loop burning the user's usage quota.
_DEFAULT_TRUNCATION_RETRIES = 1

# What Codex accepts for `model_reasoning_effort`. Our ladder's xhigh/max have no Codex
# rung, so they FOLD to high rather than being passed through and rejected.
_EFFORT_MAP = {
    # "none"/"off" mean "spend tokens on the answer, not on thinking". Codex has no off rung
    # either, so they FLOOR to low rather than mapping to nothing — passing no config at all
    # is not neutral, it hands the run Codex's OWN default effort, which is higher. See the
    # matching note in claude_cli_provider._cli_effort for the incident behind this.
    "none": "low",
    "off": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

# The child's environment is filtered by core.child_env (shared with the Claude CLI and the terminal):
# OPENAI_API_KEY in particular makes Codex bill the API instead of the ChatGPT plan.

# One semaphore per event loop: the module is imported once but the app, tests and
# background workers may each run their own loop, and an asyncio primitive is bound to
# the loop that created it.
_semaphores: "dict[int, asyncio.Semaphore]" = {}


def _max_concurrency() -> int:
    try:
        return max(1, int(os.getenv("ONW_CODEX_CLI_MAX_CONCURRENCY") or _DEFAULT_MAX_CONCURRENCY))
    except ValueError:
        return _DEFAULT_MAX_CONCURRENCY


def _timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("ONW_CODEX_CLI_TIMEOUT") or _DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def _truncation_retries() -> int:
    try:
        return max(
            0,
            int(os.getenv("ONW_CODEX_CLI_TRUNCATION_RETRIES") or _DEFAULT_TRUNCATION_RETRIES),
        )
    except ValueError:
        return _DEFAULT_TRUNCATION_RETRIES


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(id(loop))
    if sem is None:
        sem = asyncio.Semaphore(_max_concurrency())
        _semaphores[id(loop)] = sem
    return sem


def _child_env(api_key: str) -> dict:
    """A filtered environment for the child process.

    Strips this app's own secrets (``ONW_*`` and the stores' key vars) so a subprocess we
    spawn can never read them, and passes ``OPENAI_API_KEY`` through ONLY when the user
    explicitly stored one for this provider — otherwise the CLI uses the ``codex login``
    session that is the whole point of this transport.
    """
    env = scrubbed_environ()
    if api_key:
        env["OPENAI_API_KEY"] = api_key
    # Clearing CODEX_HOME is not isolation — the CLI then falls back to the operator's real
    # ~/.codex, plugins and skills included (measured: 53,983 input tokens for "17*23", and
    # two shell commands spent reading skill files first). Point it at a tree we own.
    env["CODEX_HOME"] = isolated_home()
    # Non-interactive, machine-readable: no spinner/telemetry surprises in the stream.
    env["CI"] = env.get("CI", "1")
    return env


def cost_basis_for(api_key: str) -> str:
    """Classify what this transport's dollar figure MEANS for this session.

    With no stored key the CLI runs on the user's ``codex login`` (a ChatGPT plan), so the
    price-table figure is a locally-computed LIST PRICE for usage the subscription already
    covers — not a charge. It is still recorded (hiding it would understate consumption of
    a rate-limited quota) but STAMPED, so a reader can tell an estimate from an invoice.
    """
    return "estimate" if (api_key or "").strip() else "plan_included"


def build_prompt(system: str, transcript: str) -> str:
    """One stdin payload: the system instructions, then the conversation.

    ``codex exec`` takes no ``--system-prompt``, so the system text has to ride in the
    prompt. It is fenced under an explicit heading rather than merely concatenated, so the
    model can tell standing instructions from the turn it is answering.
    """
    if not (system or "").strip():
        return transcript
    return f"# System instructions\n\n{system.strip()}\n\n# Conversation\n\n{transcript}"


class CodexCliProvider(AIProvider):
    """AIProvider backed by the local ``codex`` CLI in non-interactive mode."""

    def __init__(self, cli_path: str = "", api_key: str = ""):
        self.cli_path = (cli_path or "").strip() or DEFAULT_CLI_PATH
        self.api_key = (api_key or "").strip()

    # ── process plumbing ──────────────────────────────────────────────────────

    def _resolve_cli(self) -> str:
        """Absolute path to the CLI, or a clean ConfigurationError."""
        resolved = shutil.which(self.cli_path)
        if resolved:
            return resolved
        raise ConfigurationError(
            f"Codex CLI not found (looked for {self.cli_path!r}). Install it "
            "(https://developers.openai.com/codex/cli), run `codex login` once, and make "
            "sure it is on the PATH of the process running this app — or set the CLI path "
            "in Settings → AI."
        )

    def _argv(self, model: str, effort: str = "") -> list[str]:
        argv = [
            self._resolve_cli(),
            "exec",
            "--json",
            # No ANSI escapes in a stream we parse.
            "--color",
            "never",
            # The scratch cwd below is deliberately not a repo.
            "--skip-git-repo-check",
            # NOT `--ignore-user-config`, though it would also work: this path exists as the
            # fallback for a CLI too old to have `app-server`, and an unrecognised flag
            # fails the whole turn. The isolated CODEX_HOME already covers the operator's
            # config, plugins and skills, and it works on every version.
            # A model backend never edits anything…
            "--sandbox",
            "read-only",
            # …and never stalls on an approval prompt in a headless process.
            "-c",
            'approval_policy="never"',
            # Ignore the user's MCP servers and the CLI's own web tool — the host owns
            # the tool loop.
            "-c",
            "mcp_servers={}",
            "-c",
            "tools.web_search=false",
            # Never read an AGENTS.md, and keep no CLI-side history of our turns.
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            'history.persistence="none"',
        ]
        if model:
            argv += ["--model", model]
        mapped = _EFFORT_MAP.get((effort or "").lower())
        if mapped:
            argv += ["-c", f'model_reasoning_effort="{mapped}"']
        # Trailing "-": read the prompt from stdin, so it never lands in the process list.
        argv.append("-")
        return argv

    async def _spawn(self, argv: list[str], prompt: str, images: list[dict] | None = None):
        """Start the CLI in an empty temp cwd with the prompt written to stdin.

        ``-i/--image <FILE>...`` takes a greedy file list, so any image goes in as a path
        written into this same throwaway cwd — never inline on argv (no prompt-length
        limit, and it never lands in the host's process list) — with a trailing ``--``
        splicing it off from the ``-`` (stdin) prompt argument that always ends ``argv``.
        """
        # A fresh empty directory: no repo access and nothing for AGENTS.md discovery to
        # find, even if a future CLI version ignores project_doc_max_bytes. Doubles as the
        # scratch dir for image files below — both are removed together on the way out.
        workdir = tempfile.mkdtemp(prefix="onw-codex-cli-")
        proc = None
        try:
            final_argv = argv
            if images:
                paths = write_image_files(workdir, images)
                final_argv = argv[:-1] + ["-i", *paths, "--"] + argv[-1:]
            proc = await asyncio.create_subprocess_exec(
                *final_argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=_child_env(self.api_key),
                limit=_STDOUT_LIMIT,
            )
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            return proc, workdir
        except BaseException:
            if proc is not None and proc.returncode is None:
                await _kill(proc)
            shutil.rmtree(workdir, ignore_errors=True)
            raise

    async def _events(
        self, argv: list[str], prompt: str, images: list[dict] | None = None
    ) -> AsyncGenerator[dict, None]:
        """Yield the CLI's JSONL events, bounded by the semaphore and the timeout."""
        async with _semaphore():
            proc, workdir = await self._spawn(argv, prompt, images=images)
            # Drain stderr concurrently: a full pipe buffer would otherwise block the
            # child mid-turn, and we want its text for the failure message.
            errs: list[bytes] = []
            err_task = asyncio.ensure_future(_drain(proc.stderr, errs))
            deadline = time.monotonic() + _timeout_seconds()
            try:
                assert proc.stdout is not None
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                    if not line:
                        break
                    try:
                        yield json.loads(line.decode("utf-8", "replace"))
                    except (TypeError, ValueError):
                        # A non-JSON line is CLI chatter, not our contract — log at debug
                        # and keep reading rather than failing the whole turn.
                        log.debug("codex_cli: skipping non-JSON stdout line: %.200s", line)
                rc = await proc.wait()
                if rc != 0:
                    detail = b"".join(errs).decode("utf-8", "replace").strip()[:500]
                    raise ExternalServiceError(
                        f"Codex CLI exited with status {rc}: {detail or '(no stderr)'}"
                    )
            except asyncio.TimeoutError:
                await _kill(proc)
                raise ProviderTimeoutError(
                    f"Codex CLI timed out after {_timeout_seconds():.0f}s "
                    "(raise ONW_CODEX_CLI_TIMEOUT if this is expected)."
                ) from None
            finally:
                if proc.returncode is None:
                    await _kill(proc)
                err_task.cancel()
                shutil.rmtree(workdir, ignore_errors=True)

    # ── AIProvider ────────────────────────────────────────────────────────────

    async def stream_chat(
        self,
        messages: List[dict],
        tools: List[dict],
        system: str,
        model: str,
        effort: str = "medium",
    ) -> AsyncGenerator[dict, None]:
        if _preferred_transport() == "app_server":
            for attempt in range(1, _APP_SERVER_ATTEMPTS + 1):
                started = False
                try:
                    async for event in self._stream_app_server(
                        messages, tools or [], system, model, effort
                    ):
                        started = True
                        yield event
                    return
                except ConfigurationError:
                    # No CLI at all — retrying cannot help and exec would fail identically.
                    raise
                except ExternalServiceError as exc:
                    if started:
                        # Content already reached the user; re-running the turn would
                        # duplicate it. Never retried, never swapped for another transport.
                        raise
                    if attempt >= _APP_SERVER_ATTEMPTS or not _is_retryable(exc):
                        raise _app_server_failed(exc, attempt) from exc
                    log.warning(
                        "codex_cli: Codex app-server attempt %d/%d failed, retrying — %s",
                        attempt,
                        _APP_SERVER_ATTEMPTS,
                        exc,
                    )
                    await asyncio.sleep(_APP_SERVER_RETRY_DELAY * attempt)

        instructions = (system or "") + render_tool_manifest(tools or [])
        transcript, images = flatten_messages_with_images(messages)
        async for event in self._stream_exec(instructions, transcript, model, effort, images):
            yield event

    async def _native_tool_specs(self, tools: list[dict]) -> list[dict] | None:
        """The turn's tools as native registrations, or None to use the text protocol.

        None when there are no tools, native tools are pinned off, the installed CLI cannot
        deliver them (version / code-mode host probe at handshake), or a tool name is not a
        legal function name. Decided per round: the offered set can GROW between rounds
        (``find_tools``), and a thread's registration cannot change, so each round's thread
        is started with the set as it is NOW."""
        if not tools or not codex_app_server.native_tools_enabled():
            return None
        client = codex_app_server.client_for(self.cli_path, self.api_key, native_tools=True)
        await client.ensure_started()
        return codex_app_server.dynamic_tool_specs(tools) if client.native_ready else None

    async def _stream_app_server(
        self,
        messages: List[dict],
        tools: list[dict],
        system: str,
        model: str,
        effort: str,
    ) -> AsyncGenerator[dict, None]:
        """One model round over the app-server: native tools when the CLI supports them,
        else the text protocol. A refused registration (nothing streamed yet) re-runs the
        round on the text protocol rather than failing it."""
        specs = await self._native_tool_specs(tools)
        try:
            async for event in self._app_server_round(
                messages, tools, system, model, effort, specs
            ):
                yield event
        except codex_app_server.NativeToolsUnavailable as exc:
            log.warning("codex_cli: app-server refused native tools, using text protocol — %s", exc)
            async for event in self._app_server_round(messages, tools, system, model, effort, None):
                yield event

    async def _app_server_round(
        self,
        messages: List[dict],
        tools: list[dict],
        system: str,
        model: str,
        effort: str,
        specs: list[dict] | None,
    ) -> AsyncGenerator[dict, None]:
        """Stream one host round over the persistent app-server (deltas + reasoning).

        With native tools the Codex turn outlives the round: it PARKS at the model's tool
        calls, and the next round — when it carries exactly those calls' results — continues
        the same thread instead of pasting the conversation into a new one
        (:mod:`backend.ai.models.codex_live_turn`)."""
        native = specs is not None
        instructions = (system or "") + (
            NATIVE_TOOLS_NOTE if native and tools else render_tool_manifest(tools)
        )
        key: dict = {}
        if native and codex_live_turn.live_turns_enabled():
            key = codex_live_turn.turn_key(instructions, specs or [], model, effort)
        claimed = codex_turn_claims.claim(messages, key) if key else None
        client = codex_app_server.client_for(self.cli_path, self.api_key, native_tools=native)
        native_calls: list[dict] = []
        malformed_calls = 0
        scanner = ToolCallScanner()
        t0 = time.monotonic()
        ttft_ms: Optional[float] = None
        turn = _Turn()
        emitted_any_delta = False

        def _abandoned_after_a_preamble(phase: str) -> bool:
            """Did the turn end on narration with nothing to act on?

            An agentic model announces before it acts ("I'll search the ledger…"), which
            Codex marks ``commentary``. After the preamble it may have nothing to invoke and
            the turn just ends — and we would render the preamble as the answer. If our text
            protocol DID yield a call, the turn is fine: the host loop is about to run it.

            `phase` is empty on builds that do not report it; treat that as "phase unknown"
            and do nothing, per the schema's own warning.
            """
            return phase == "commentary" and not scanner.has_calls

        emitted_thinking = False
        async with codex_app_server.semaphore():
            if claimed is not None:
                live, answers, steer = claimed
                live.should_continue = _abandoned_after_a_preamble
                log.info(
                    "codex_cli: continuing the open Codex turn (%d result(s)%s)",
                    len(answers),
                    f", {len(steer)} steer" if steer else "",
                )
                try:
                    await live.resume(answers, steer)
                except BaseException:
                    # A retry of this round must not find the half-resumed turn: it starts a
                    # fresh thread from the transcript instead.
                    live.close()
                    raise
            else:
                nonce = secrets.token_hex(6) if native else ""
                transcript, images = flatten_messages_with_images(messages, result_nonce=nonce)
                live = await codex_live_turn.LiveTurn.start(
                    client,
                    instructions=instructions,
                    transcript=transcript,
                    model=model,
                    effort=effort,
                    images=images,
                    should_continue=_abandoned_after_a_preamble,
                    dynamic_tools=specs,
                    park=bool(key),
                    key=key,
                )
            events = live.events()
            try:
                async for parsed in events:
                    kind = parsed["kind"]
                    if kind == "thinking" and parsed["text"]:
                        yield {
                            "type": "thinking_delta",
                            "text": _thinking_text(parsed, emitted_thinking),
                        }
                        emitted_thinking = True
                    elif kind == "text" and parsed["text"]:
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - t0) * 1000.0
                        emitted_any_delta = True
                        for visible in scanner.feed(parsed["text"]):
                            if visible:
                                yield {"type": "text_delta", "text": visible}
                    elif kind == "tool_calls":
                        native_calls = parsed["calls"]
                        malformed_calls = parsed["malformed"]
                    elif kind == "message":
                        turn.with_message(parsed["text"])
                    elif kind == "usage":
                        turn.with_usage(codex_app_server.usage_fields(parsed["usage"]))
                    elif kind == "error":
                        raise ExternalServiceError(
                            f"Codex app-server reported an error: {parsed['text']}"[:500]
                        )
                    elif kind == "done":
                        turn.finish(parsed["text"])
            finally:
                await events.aclose()

        if not turn.completed:
            live.close()
            raise ExternalServiceError(
                "Codex app-server ended without a turn/completed event (the turn was truncated)."
            )
        async for event in self._finish(
            turn,
            scanner,
            emitted_any_delta,
            model,
            ttft_ms,
            t0,
            native_calls,
            malformed_calls,
            bind_native=live.bind,
        ):
            yield event

    async def _stream_exec(
        self,
        instructions: str,
        transcript: str,
        model: str,
        effort: str,
        images: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        prompt = build_prompt(instructions, transcript)
        argv = self._argv(model, effort=effort)

        attempts = _truncation_retries() + 1
        scanner = ToolCallScanner()
        t0 = time.monotonic()
        ttft_ms: Optional[float] = None
        turn: Optional[_Turn] = None
        emitted_any_delta = False

        for attempt in range(1, attempts + 1):
            scanner = ToolCallScanner()
            t0 = time.monotonic()
            ttft_ms = None
            turn = None
            emitted_any_delta = False
            emitted_anything = False
            emitted_thinking = False

            async for ev in self._events(argv, prompt, images=images):
                parsed = _classify(ev)
                kind = parsed["kind"]
                if kind == "thinking" and parsed["text"]:
                    emitted_anything = True
                    yield {
                        "type": "thinking_delta",
                        "text": _thinking_text(parsed, emitted_thinking),
                    }
                    emitted_thinking = True
                elif kind == "text" and parsed["text"]:
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - t0) * 1000.0
                    emitted_any_delta = True
                    for visible in scanner.feed(parsed["text"]):
                        if visible:
                            emitted_anything = True
                            yield {"type": "text_delta", "text": visible}
                elif kind == "message":
                    # A whole (non-streamed) assistant message. Only useful when the build
                    # emitted no deltas at all — otherwise it would duplicate the text.
                    turn = (turn or _Turn()).with_message(parsed["text"])
                elif kind == "usage":
                    turn = (turn or _Turn()).with_usage(parsed["usage"])
                elif kind == "error":
                    raise ExternalServiceError(
                        f"Codex CLI reported an error: {parsed['text']}"[:500]
                    )
                elif kind == "done":
                    turn = turn or _Turn()
                    if parsed["usage"]:
                        turn.with_usage(parsed["usage"])
                    turn.finish(parsed["text"])

            if turn is not None and turn.completed:
                break

            if emitted_anything or attempt >= attempts:
                # The terminal event IS the contract — a stream that ends without one has
                # been truncated (an intermittent CLI behavior — the process exits 0 but
                # never writes a completion event). A silent retry already ran above while
                # nothing had reached the user yet; once real content was already streamed,
                # or the retry budget is spent, raise rather than record a turn with no
                # usage (retrying post-content would risk duplicating it).
                raise ExternalServiceError(
                    "Codex CLI output ended without a completion event (the turn was "
                    "truncated). Upgrade the CLI (`codex --upgrade`) if this repeats."
                )
            log.warning(
                "codex_cli: stream_chat ended with no completion event before any content "
                "was shown — retrying (attempt %d/%d)",
                attempt + 1,
                attempts,
            )

        async for event in self._finish(turn, scanner, emitted_any_delta, model, ttft_ms, t0):
            yield event

    async def _finish(
        self,
        turn: "_Turn",
        scanner: ToolCallScanner,
        emitted_any_delta: bool,
        model: str,
        ttft_ms: Optional[float],
        t0: float,
        native_calls: list[dict] | None = None,
        malformed_calls: int = 0,
        bind_native=None,
    ) -> AsyncGenerator[dict, None]:
        """The tail every transport shares: fallback text, usage, tool calls, done.

        ``native_calls`` are calls the model made through the app-server's registered tools;
        they leave here as the same ``tool_use`` events a parsed ``<tool_call>`` does, so the
        host loop cannot tell the two apart. ``bind_native`` receives the ids minted for them
        (in order), so an open Codex turn can be matched to their results next round."""
        # Deltas can be absent (an older build, or a model that emits only whole items):
        # fall back to the final message text so a turn never silently produces nothing.
        if not emitted_any_delta and turn.text:
            for visible in scanner.feed(turn.text):
                if visible:
                    yield {"type": "text_delta", "text": visible}
        tail = scanner.flush()
        if tail:
            yield {"type": "text_delta", "text": tail}
        if malformed_calls:
            yield {"type": "text_delta", "text": f"\n{UNPARSED_CALL_NOTICE}\n"}

        yield self._usage_event(turn, model, ttft_ms, t0)

        calls = scanner.calls + (native_calls or [])
        call_ids = [f"codex_{int(t0 * 1000)}_{i}" for i in range(len(calls))]
        if bind_native is not None and native_calls:
            bind_native(call_ids[len(scanner.calls) :])
        for call_id, call in zip(call_ids, calls):
            yield {"type": "tool_use_start", "id": call_id, "name": call["name"]}
            yield {
                "type": "tool_use",
                "id": call_id,
                "name": call["name"],
                "input": call["input"],
            }

        yield {"type": "done", "stop_reason": "tool_use" if calls else "end_turn"}

    def _usage_event(self, turn: "_Turn", model: str, ttft_ms: Optional[float], t0: float) -> dict:
        """Build the normalised ``usage`` event. Codex reports no cost — derive it."""
        return {
            "cost_basis": cost_basis_for(self.api_key),
            "type": "usage",
            "input_tokens": turn.input_tokens,
            "cache_creation_tokens": 0,
            "cache_creation_5m_tokens": 0,
            "cache_creation_1h_tokens": 0,
            "cache_read_tokens": turn.cached_tokens,
            "output_tokens": turn.output_tokens,
            "cost_usd": cost_usd_openai(
                model, turn.input_tokens, turn.output_tokens, turn.cached_tokens
            ),
            "model": model,
            "ttft_ms": ttft_ms,
            "duration_ms": (time.monotonic() - t0) * 1000.0,
        }

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,  # noqa: ARG002 - the CLI has no output-token cap flag
        source: str = "complete",
        images: list[dict] | None = None,
        response_format: dict | None = None,
        effort: str = "",
    ) -> str:
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]

        turn = await self._complete_turn(prompt, system, model, effort, response_format, images)

        cost = cost_usd_openai(model, turn.input_tokens, turn.output_tokens, turn.cached_tokens)
        basis = cost_basis_for(self.api_key)
        # Owner incident 2026-08-31 (KODK, openai_provider's own equivalent fix): log the
        # actual response text, not just token counts. Bounded, logged only — see
        # openai_provider.complete's matching comment for why this isn't in cost_log.
        _LOG_TEXT_CAP = log_text_cap(source)
        log.info(
            "complete_done  source=%s  transport=codex_cli  model=%s  basis=%s  input=%d  "
            "cached=%d  output=%d  cost_usd=%.5f  content_chars=%d\n"
            "--- content (first %d chars) ---\n%s",
            source,
            model,
            basis,
            turn.input_tokens,
            turn.cached_tokens,
            turn.output_tokens,
            cost,
            len(turn.text or ""),
            _LOG_TEXT_CAP,
            (turn.text or "")[:_LOG_TEXT_CAP],
        )
        from backend.ai.cost_tracker import record
        from backend.db.model_config_store import provider_label_for

        record(
            source,
            model,
            turn.input_tokens,
            turn.output_tokens,
            0,
            turn.cached_tokens,
            cost_usd=cost,
            cost_basis=basis,
            provider=provider_label_for("codex_cli", ""),
        )
        return turn.text

    async def stream_complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,  # noqa: ARG002 - the CLI has no output-token cap flag
        source: str = "complete",
        effort: str = "",
    ) -> AsyncGenerator[dict, None]:
        """The streaming twin of ``complete()`` — COARSE on this transport, matching
        ``ClaudeCliProvider.stream_complete``'s same tradeoff: reuses ``_complete_turn``
        (full app-server/exec routing + retry behavior, unchanged) rather than forking
        its complexity to expose the app-server path's real incremental "text" events,
        then yields the whole reply as ONE ``text_delta`` once it's back. A caller
        computing progress from it sees 0% until completion, then 100% — never a
        fabricated partial number this path isn't built to back up.
        """
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]

        turn = await self._complete_turn(prompt, system, model, effort, None, None)

        if turn.text:
            yield {"type": "text_delta", "text": turn.text}

        cost = cost_usd_openai(model, turn.input_tokens, turn.output_tokens, turn.cached_tokens)
        basis = cost_basis_for(self.api_key)
        log.info(
            "stream_complete_done  source=%s  transport=codex_cli  model=%s  basis=%s  "
            "input=%d  cached=%d  output=%d  cost_usd=%.5f",
            source,
            model,
            basis,
            turn.input_tokens,
            turn.cached_tokens,
            turn.output_tokens,
            cost,
        )
        from backend.ai.cost_tracker import record
        from backend.db.model_config_store import provider_label_for

        record(
            source,
            model,
            turn.input_tokens,
            turn.output_tokens,
            0,
            turn.cached_tokens,
            cost_usd=cost,
            cost_basis=basis,
            provider=provider_label_for("codex_cli", ""),
        )

    async def _complete_turn(
        self,
        prompt: str,
        system: str,
        model: str,
        effort: str,
        response_format: dict | None,
        images: list[dict] | None = None,
    ) -> "_Turn":
        """One non-streamed turn on the app-server, retried then RAISED — never silently
        rerouted to ``codex exec`` (owner, 2026-08-09; see ``_APP_SERVER_ATTEMPTS``).

        An image is the ONE remaining exec route, and it is a routing decision made BEFORE
        any attempt rather than a failure fallback: app-server's ``thread/start`` protocol
        has no image parameter wired up here, but ``exec -i`` genuinely reads one (verified
        against the real CLI), so a scanned statement gets a correct answer over a slower
        transport rather than a fabricated one over a faster one.
        """
        if not images and _preferred_transport() == "app_server":
            for attempt in range(1, _APP_SERVER_ATTEMPTS + 1):
                try:
                    return await self._complete_app_server(
                        prompt, system, model, effort, response_format
                    )
                except ConfigurationError:
                    raise
                except ExternalServiceError as exc:
                    if attempt >= _APP_SERVER_ATTEMPTS or not _is_retryable(exc):
                        raise _app_server_failed(exc, attempt) from exc
                    log.warning(
                        "codex_cli: Codex app-server attempt %d/%d failed, retrying — %s",
                        attempt,
                        _APP_SERVER_ATTEMPTS,
                        exc,
                    )
                    await asyncio.sleep(_APP_SERVER_RETRY_DELAY * attempt)
        return await self._complete_exec(prompt, system, model, effort, response_format, images)

    async def _complete_app_server(
        self,
        prompt: str,
        system: str,
        model: str,
        effort: str,
        response_format: dict | None,
    ) -> "_Turn":
        client = codex_app_server.client_for(self.cli_path, self.api_key)
        schema = (response_format or {}).get("json_schema", {})
        schema = schema.get("schema") if isinstance(schema, dict) else None
        turn = _Turn()
        async with codex_app_server.semaphore():
            async for parsed in client.run_turn(
                instructions=system or "",
                # `outputSchema` constrains the final message natively, but the prompt-side
                # instruction stays for the `json_object` case and for older servers.
                transcript=prompt + _json_instruction(response_format),
                model=model,
                effort=effort,
                output_schema=schema if isinstance(schema, dict) else None,
            ):
                kind = parsed["kind"]
                if kind == "error":
                    raise ExternalServiceError(
                        f"Codex app-server reported an error: {parsed['text']}"[:500]
                    )
                if kind == "text":
                    turn.with_message(parsed["text"], append=True)
                elif kind == "message":
                    turn.with_message(parsed["text"])
                elif kind == "usage":
                    turn.with_usage(codex_app_server.usage_fields(parsed["usage"]))
                elif kind == "done":
                    turn.finish(parsed["text"])
        if not turn.completed:
            if _expects_json_response(response_format):
                record_structured_output_error(model)
            raise ExternalServiceError(
                "Codex app-server ended without a turn/completed event (the turn was truncated)."
            )
        return turn

    async def _complete_exec(
        self,
        prompt: str,
        system: str,
        model: str,
        effort: str,
        response_format: dict | None,
        images: list[dict] | None = None,
    ) -> "_Turn":
        argv = self._argv(model, effort=effort)
        body = build_prompt(system or "", prompt + _json_instruction(response_format))

        attempts = _truncation_retries() + 1
        turn = _Turn()
        for attempt in range(1, attempts + 1):
            turn = _Turn()
            async for ev in self._events(argv, body, images=images):
                parsed = _classify(ev)
                kind = parsed["kind"]
                if kind == "error":
                    raise ExternalServiceError(
                        f"Codex CLI reported an error: {parsed['text']}"[:500]
                    )
                if kind in ("text", "message"):
                    turn = turn.with_message(parsed["text"], append=(kind == "text"))
                elif kind == "usage":
                    turn.with_usage(parsed["usage"])
                elif kind == "done":
                    if parsed["usage"]:
                        turn.with_usage(parsed["usage"])
                    turn.finish(parsed["text"])

            if turn.completed:
                break

            if _expects_json_response(response_format):
                record_structured_output_error(model)

            if attempt >= attempts:
                raise ExternalServiceError(
                    "Codex CLI output ended without a completion event (the turn was "
                    "truncated). Upgrade the CLI (`codex --upgrade`) if this repeats."
                )
            # complete() never streams partial output to a caller — unlike stream_chat,
            # a retry here can never duplicate anything the caller has already seen.
            log.warning(
                "codex_cli: complete() ended with no completion event — retrying (attempt %d/%d)",
                attempt + 1,
                attempts,
            )

        return turn


class _Turn:
    """The accumulating state of one CLI invocation (text + usage + terminal flag)."""

    __slots__ = ("text", "completed", "input_tokens", "cached_tokens", "output_tokens")

    def __init__(self) -> None:
        self.text = ""
        self.completed = False
        self.input_tokens = 0
        self.cached_tokens = 0
        self.output_tokens = 0

    def with_message(self, text: str, append: bool = False) -> "_Turn":
        if text:
            self.text = (self.text + text) if append else text
        return self

    def with_usage(self, usage: dict) -> "_Turn":
        # Codex re-reports cumulative usage on several events; the LAST one wins rather
        # than summing, or a mid-turn token_count would be double-counted.
        self.input_tokens = int(usage.get("input_tokens") or 0)
        self.cached_tokens = int(usage.get("cached_input_tokens") or 0)
        self.output_tokens = int(usage.get("output_tokens") or 0)
        return self

    def finish(self, text: str) -> "_Turn":
        self.completed = True
        if text and not self.text:
            self.text = text
        return self


def _thinking_text(parsed: dict, already_emitted: bool) -> str:
    """A reasoning chunk, with a blank line before it when it is a WHOLE item following
    another.

    Codex delivers each summary as a complete item wrapped in `**…**`. Concatenated with no
    separator, two of them meet as `**Planning BTC info retrieval****Planning credible
    finance source use**` — which markdown cannot parse as two bold runs, so the reader sees
    the asterisks (owner report, 2026-08-23, gpt-5.5). Character-level deltas are joined
    untouched.
    """
    text = parsed["text"]
    return f"\n\n{text}" if parsed.get("whole") and already_emitted else text


def _classify(ev: dict) -> dict:
    """Normalise ONE CLI event into ``{kind, text, usage}``.

    ``kind`` is one of ``text`` (a streamed delta), ``thinking``, ``message`` (a whole
    assistant message), ``usage``, ``done``, ``error`` or ``ignore``. Both the typed
    (``turn.completed`` / ``item.*``) and the legacy (``msg.type``) schemas are accepted —
    see the module docstring for why neither is pinned.
    """
    etype = ev.get("type") or ""

    # ── typed schema ──────────────────────────────────────────────────────────
    if etype == "turn.completed":
        # The typed schema's terminal event carries the usage block too — one event, both
        # facts, which is why every ``done`` may also carry a usage payload.
        return {"kind": "done", "text": "", "usage": ev.get("usage") or {}}
    if etype in ("item.completed", "item.started", "item.updated"):
        item = ev.get("item") or {}
        itype = item.get("item_type") or item.get("type") or ""
        if itype in ("agent_message", "assistant_message"):
            kind = "message" if etype == "item.completed" else "ignore"
            return {"kind": kind, "text": item.get("text") or "", "usage": {}}
        if itype == "reasoning" and etype == "item.completed":
            # WHOLE: this schema delivers each reasoning summary as one finished item
            # ("**Planning BTC info retrieval**"), not as a character stream. Consecutive
            # items therefore need a separator between them — see `_thinking_text`. The
            # legacy `agent_reasoning_delta` path below is character-level and must NOT be
            # separated, or blank lines land mid-sentence.
            return {"kind": "thinking", "text": item.get("text") or "", "usage": {}, "whole": True}
        return {"kind": "ignore", "text": "", "usage": {}}
    if etype == "turn.failed":
        err = ev.get("error") or {}
        return {"kind": "error", "text": err.get("message") or "turn failed", "usage": {}}
    if etype == "error":
        return {"kind": "error", "text": ev.get("message") or "error", "usage": {}}

    # ── legacy schema ─────────────────────────────────────────────────────────
    msg = ev.get("msg")
    if not isinstance(msg, dict):
        return {"kind": "ignore", "text": "", "usage": {}}
    mtype = msg.get("type") or ""
    if mtype == "agent_message_delta":
        return {"kind": "text", "text": msg.get("delta") or "", "usage": {}}
    if mtype in ("agent_reasoning_delta", "agent_reasoning_raw_content_delta"):
        return {"kind": "thinking", "text": msg.get("delta") or "", "usage": {}}
    if mtype == "agent_message":
        return {"kind": "message", "text": msg.get("message") or "", "usage": {}}
    if mtype == "token_count":
        info = msg.get("info") or {}
        usage = info.get("last_token_usage") or info.get("total_token_usage") or msg
        return {"kind": "usage", "text": "", "usage": usage if isinstance(usage, dict) else {}}
    if mtype == "task_complete":
        return {"kind": "done", "text": msg.get("last_agent_message") or "", "usage": {}}
    if mtype in ("error", "stream_error"):
        return {"kind": "error", "text": msg.get("message") or "error", "usage": {}}
    return {"kind": "ignore", "text": "", "usage": {}}


def _expects_json_response(response_format: dict | None) -> bool:
    if not isinstance(response_format, dict):
        return False
    schema = (response_format.get("json_schema") or {}).get("schema")
    return isinstance(schema, dict) or response_format.get("type") in ("json_object", "json_schema")


def _json_instruction(response_format: dict | None) -> str:
    """Prompt-side JSON coaxing for ``complete(response_format=…)``.

    ``codex exec`` does have an ``--output-schema`` flag on recent builds, but it is not
    present on every version a self-hoster may have installed, and passing an unsupported
    flag fails the whole turn. So structured output degrades to prompt-and-parse — the
    callers all instruct JSON in the prompt and parse tolerantly already.
    """
    if not response_format:
        return ""
    schema = (response_format.get("json_schema") or {}).get("schema")
    if isinstance(schema, dict):
        return (
            "\n\nRespond with a single JSON object and nothing else — no prose, no code "
            "fence. It must validate against this JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False)
        )
    if response_format.get("type") in ("json_object", "json_schema"):
        return "\n\nRespond with a single JSON object and nothing else — no prose, no code fence."
    return ""


async def _drain(stream, sink: list[bytes]) -> None:
    """Read a pipe to EOF into ``sink`` — best-effort, never raises into the turn."""
    if stream is None:
        return
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            if sum(len(c) for c in sink) < 64_000:
                sink.append(chunk)
    except (asyncio.CancelledError, OSError, ValueError):
        return


async def _kill(proc) -> None:
    """Best-effort terminate AND reap — a dead process must never keep the turn alive.

    The reap matters: without awaiting ``wait()`` the transport is torn down by the GC
    later, after the loop may already be closed.
    """
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (asyncio.TimeoutError, ProcessLookupError, OSError):
        pass
