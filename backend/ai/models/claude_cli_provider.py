"""Claude Code headless-CLI provider.

Runs the locally-installed ``claude`` CLI in headless mode (``claude -p``) as an AI
provider, so a self-hoster with a Claude Pro/Max subscription can spend THAT instead of
buying separate API credits for the same models.

Invocation contract (verified against ``claude`` 2.1.216 — see
``.claude/rfcs/2026-07-22-claude-code-cli-provider.md`` §3)::

    claude -p --input-format stream-json --output-format stream-json --verbose
           --include-partial-messages --model <model> --system-prompt <text>
           --tools "" --strict-mcp-config --setting-sources ""
           --no-session-persistence [--effort <level>] [--max-budget-usd <n>]

with ONE NDJSON user message on stdin.

Four of those flags are load-bearing and must not be dropped:

* ``--verbose`` — required by the CLI whenever ``stream-json`` is used in print mode.
* ``--tools ""`` — the CLI's own Read/Bash/Edit tools are OFF. This is a model backend,
  not a coding agent; the host owns the tool loop.
* ``--setting-sources ""`` — ignore the user's own settings, hooks and ``CLAUDE.md``.
  Without it a ``SessionStart`` hook silently prepends kilobytes of unrelated
  instructions to every turn (observed on the machine this was built on).
* ``--strict-mcp-config`` — ignore whatever MCP servers the user happens to have
  configured, so the model's reachable surface is exactly what we granted it.

Statelessness is deliberate: we never pass ``--resume``, so the CLI holds no conversation
of ours. ``assistant.run`` owns the transcript (folding, checkpoints, rehydration) and
re-sends it every turn — a CLI-side session would silently diverge from it.

Cost: the final ``result`` event carries ``total_cost_usd`` plus the full usage block.
That figure is TRUSTED over any price-table estimate (the same rule already applied to
OpenRouter's ``usage.cost`` and LiteLLM's response-cost header). On a subscription login
it is the *equivalent* API price rather than an incremental charge — reporting it is more
honest than reporting zero, which would hide real consumption of a rate-limited quota.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from itertools import chain
from typing import AsyncGenerator, List, Optional

from backend.ai.image_payloads import decode_image_payloads
from backend.core.child_env import scrubbed_environ
from backend.core.errors import ConfigurationError, ExternalServiceError, ProviderTimeoutError

from . import claude_live_session
from .base import AIProvider
from .cli_call_grace import PostCallWatch, grace_seconds
from .cli_tool_protocol import (
    ToolCallScanner,
    flatten_messages_with_images,
    looks_like_an_unfenced_tool_call,
    render_tool_manifest,
)
from .telemetry import record_structured_output_error

log = logging.getLogger(__name__)

DEFAULT_CLI_PATH = "claude"

# Every turn is a subprocess AND a subscription's rate limit is per-ACCOUNT, so parallel
# spawns buy nothing and can trip it. Bound the fan-out; waiters queue rather than fail.
_DEFAULT_MAX_CONCURRENCY = 2
# Hard per-call ceiling — a hung CLI must surface as a clean error, never as a connector
# turn that hangs forever.
# 300s was a text-completion ceiling; a statement batch reasons over ~30 rows plus their
# match candidates at effort=high and legitimately runs past it (owner, 2026-08-09).
# ONW_CLAUDE_CLI_TIMEOUT still overrides.
_DEFAULT_TIMEOUT_SECONDS = 1200.0
# How many times to silently re-spawn a call whose CLI subprocess exited cleanly but never
# emitted a terminal `result` event (see `_events`'s docstring for why this happens).
# Bounded: a genuinely broken invocation (bad model, bad flags) would fail identically on
# every attempt, so this must not become an unbounded retry loop burning the user's quota.
_DEFAULT_TRUNCATION_RETRIES = 1
# See _structured_output_retries()'s own docstring — a separate, larger budget specifically
# for the CLI's own error_max_structured_output_retries give-up, distinct from the retry
# budget above (which governs a DIFFERENT failure mode: a subprocess that exited without ever
# emitting a terminal result event).
_DEFAULT_STRUCTURED_OUTPUT_RETRIES = 3
# A single NDJSON line (a whole `assistant` envelope) routinely exceeds asyncio's default
# 64 KiB StreamReader limit, which would raise LimitOverrunError mid-turn.
_STDOUT_LIMIT = 8 * 1024 * 1024

# How many times a round that ended as an abandoned, unfenced tool-call narration
# (looks_like_an_unfenced_tool_call) may be resumed with a nudge — the codex_app_server
# transport's `_MAX_CONTINUATIONS` for the identical failure shape (there: Codex's own
# "commentary" phase signal; here: no such signal exists, so the trigger is the narrow
# content-shape check instead). ONE is deliberate for the same reason: a model that
# narrates the call again instead of making it is not going to act on a third ask, and
# every retry is a real request against the user's rate-limited quota.
_MAX_NARRATION_RETRIES = 1
# Deliberately terse and imperative — the calling contract is already in the system prompt
# (render_tool_manifest); repeating it here would just give the model more to narrate about.
_CONTINUE_NUDGE = (
    "Continue. Do it now: if you need a tool, your entire reply must be the single "
    "<tool_call> block. Otherwise give the final answer. Do not restate your plan."
)

_VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# INT19-follow-up (owner-diagnosed 2026-08-25): the CLI's own --json-schema structured-output
# conformance loop giving up within ITS turn budget — empirically TRANSIENT (19/19 isolated
# re-runs of prompts that failed this way inside a larger batch succeeded on a fresh attempt),
# not a per-request or per-content defect. `complete()`'s retry loop treats this ONE subtype
# as retryable, exactly like a truncated (no-result-event) stream; every other is_error subtype
# still raises on the first occurrence — see the retry-loop comment for the full reasoning.
_RETRYABLE_ERROR_SUBTYPE = "error_max_structured_output_retries"


def _is_silent_cli_crash(exc: ExternalServiceError) -> bool:
    """True for a nonzero-exit `ExternalServiceError` with NO stderr content at all — the
    `(no stderr)` suffix is a literal WE construct in `_events()` (see the nonzero-exit
    raise), not fragile matching on provider-authored text. A crash with nothing to explain
    itself is the shape of a transient process death (4 of 76 live calls, 2026-08-25); a
    crash that DOES explain itself in stderr (bad key, real config error) is presumed
    deterministic and must still raise on the first occurrence."""
    return str(exc).endswith("(no stderr)")


def _no_thinking(effort: str) -> bool:
    """True when the caller asked for no reasoning at all (``"none"``/``"off"``).

    Distinct from ``_cli_effort`` because the two knobs are unrelated on this transport:
    ``--effort`` tunes how hard the model works on the ANSWER, while thinking is governed
    only by ``MAX_THINKING_TOKENS``. Asking for the lowest effort does not stop it thinking.
    """
    return (effort or "").strip().lower() in ("none", "off")


def _cli_effort(effort: str) -> str:
    """Our effort value → the ``--effort`` level to pass, or "" to pass no flag.

    ``"none"``/``"off"`` mean "spend tokens on the answer, not on thinking" — every other
    provider honours that (openai_provider sends ``reasoning_effort=minimal`` / disables
    reasoning outright). The CLI has no off rung, so it FLOORS to ``low``.

    Flooring matters because passing no flag is not neutral: it hands the session the CLI's
    OWN default effort, which is higher. So a caller explicitly asking for no reasoning used
    to get MORE of it than one asking for `low`. That is how a chat-suggestions call — which
    requests ``effort="none"`` and ``max_tokens=400`` for three short tappable strings —
    came back with 1952 output tokens (owner-reported, 2026-07-31).

    An unrecognised/blank value still passes nothing: that means "no opinion", which is a
    different thing from "no reasoning", and the CLI's default is the right answer for it.
    """
    level = (effort or "").strip().lower()
    if level in ("none", "off"):
        return "low"
    return level if level in _VALID_EFFORTS else ""


# The child's environment is filtered by core.child_env (shared with the Codex CLI and the terminal):
# an inherited `.env` could otherwise reroute a subscription run to a metered endpoint.

# One semaphore per event loop: the module is imported once but the app, tests and
# background workers may each run their own loop, and an asyncio primitive is bound to
# the loop that created it.
_semaphores: "dict[int, asyncio.Semaphore]" = {}


def _max_concurrency() -> int:
    try:
        return max(1, int(os.getenv("ONW_CLAUDE_CLI_MAX_CONCURRENCY") or _DEFAULT_MAX_CONCURRENCY))
    except ValueError:
        return _DEFAULT_MAX_CONCURRENCY


def _timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("ONW_CLAUDE_CLI_TIMEOUT") or _DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def _stall_seconds() -> float:
    return claude_live_session.LiveSession.stall_seconds()


def _truncation_retries() -> int:
    try:
        return max(
            0,
            int(os.getenv("ONW_CLAUDE_CLI_TRUNCATION_RETRIES") or _DEFAULT_TRUNCATION_RETRIES),
        )
    except ValueError:
        return _DEFAULT_TRUNCATION_RETRIES


def _structured_output_retries() -> int:
    """A SEPARATE, larger budget for the `error_max_structured_output_retries` subtype
    specifically (owner report 2026-08-30) — see that subtype's own handling in `complete`
    for why it's not just reusing `_truncation_retries`. A large combined schema (this
    codebase's analyst `_AnalysisOutput`, with a 3-5-item key_issues array on top of the
    message/actions/etc. fields) was observed failing this way on BOTH of
    `_DEFAULT_TRUNCATION_RETRIES`'s 2 total attempts, back to back, for several different
    instruments in the same sweep — inconsistent with the ORIGINAL 2026-08-25 finding that a
    fresh attempt almost always clears it (that finding predates key_issues being requested
    for anywhere near as many held/watched instruments as it is after the 2026-08-30
    want_key_issues correction, so it was likely never exercised against a schema this large
    in practice). More attempts, not a smaller schema: shrinking what's asked for would
    change the FEATURE's output for every provider, not just work around one provider's
    reliability limit."""
    try:
        return max(
            0,
            int(
                os.getenv("ONW_CLAUDE_CLI_STRUCTURED_OUTPUT_RETRIES")
                or _DEFAULT_STRUCTURED_OUTPUT_RETRIES
            ),
        )
    except ValueError:
        return _DEFAULT_STRUCTURED_OUTPUT_RETRIES


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(id(loop))
    if sem is None:
        sem = asyncio.Semaphore(_max_concurrency())
        _semaphores[id(loop)] = sem
    return sem


def _child_env(api_key: str, no_thinking: bool = False) -> dict:
    """A filtered environment for the child process.

    Strips this app's own secrets (``ONW_*`` and the stores' key vars) so a subprocess we
    spawn can never read them, and passes ``ANTHROPIC_API_KEY`` through ONLY when the user
    explicitly stored one for this provider — otherwise the CLI uses the interactive login
    that is the whole point of this transport.
    """
    env = scrubbed_environ()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    # Non-interactive, machine-readable: no spinner/telemetry surprises in the stream.
    env["CI"] = env.get("CI", "1")
    if no_thinking:
        # The ONLY way to actually stop this CLI from thinking. `--effort low` is the
        # lowest flag it accepts and barely dents it: measured on the real binary with the
        # chat-suggestions prompt, `--effort low` still produced 5,579 output tokens in 53s
        # for a 204-character answer, versus 109 tokens in 2.0s with this set — 51x the
        # tokens and 27x the latency, for identical output.
        env["MAX_THINKING_TOKENS"] = "0"
    return env


def _result_error(event: dict) -> str:
    # Claude can emit subtype="success" with is_error=True for a failed login or API call.
    subtype = event.get("subtype") or ""
    if subtype == "success":
        subtype = ""
    detail = f"{subtype} {event.get('result') or ''}".strip()
    return (
        "Claude Code CLI reported an error: "
        + (detail or "The CLI returned an error without details.")[:500]
    )


class ClaudeCliProvider(AIProvider):
    """AIProvider backed by the local ``claude`` headless CLI."""

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
            f"Claude Code CLI not found (looked for {self.cli_path!r}). Install it "
            "(https://claude.com/claude-code), run `claude` once to log in, and make sure "
            "it is on the PATH of the process running this app — or set the CLI path in "
            "Settings → AI."
        )

    def _argv(
        self,
        model: str,
        system: str,
        effort: str = "",
        max_budget_usd: float | None = None,
        json_schema: dict | None = None,
    ) -> list[str]:
        argv = [
            self._resolve_cli(),
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            # The CLI's own tools are OFF — the host owns the tool loop.
            "--tools",
            "",
            # Ignore the user's MCP servers and their settings/hooks/CLAUDE.md.
            "--strict-mcp-config",
            "--setting-sources",
            "",
            # Don't litter ~/.claude with our turns; we keep no CLI-side session.
            "--no-session-persistence",
            # NO --max-turns, deliberately. The SDK default is "no limit", and the flag
            # bounds TOOL-USE ROUND TRIPS — but this transport passes `--tools ""`, so there
            # are no tool round trips to bound. It was guarding a loop that cannot happen.
            #
            # Setting it to 1 was not tightening a loose default, it was inventing a hard
            # ceiling: the CLI counts a reasoning/continuation step as a turn, so any call
            # where the model thinks before answering reached num_turns=2 and the CLI
            # aborted the WHOLE request with `error_max_turns` — no partial result. That
            # silently broke every schema-constrained call here (statement Lucius pass,
            # briefing generation, chat suggestions) while plain chat kept working, because
            # chat asks for short unreasoned replies. Bisected on a real statement prompt,
            # 2026-08-08: identical prompt/system/schema/effort, --max-turns 1 FAILS with
            # output_tokens=93/num_turns=2, higher values SUCCEED. `max_tokens` was never
            # the variable — this transport ignores it entirely (see `complete`).
            #
            # No --max-budget-usd either, and none is passed by any caller today. A turn
            # cap and a spend cap are the same mistake in different units: both abort the
            # whole request with no partial result, and neither maps to anything the caller
            # can reason about. Cost here is bounded by what the work actually needs — the
            # prompt is one bounded batch of statement rows, tools are off, and the host
            # owns the loop. Per-call spend is already recorded (cost_scope/complete_done),
            # so it is observable without being enforced by a guess.
            # Never stall on an interactive permission prompt in a headless process.
            "--permission-mode",
            "dontAsk",
        ]
        if model:
            argv += ["--model", model]
        if system:
            argv += ["--system-prompt", system]
        cli_effort = _cli_effort(effort)
        if cli_effort:
            argv += ["--effort", cli_effort]
        # Starting with the Opus 4.7 generation, `thinking.display` defaults to
        # "omitted" — thinking still genuinely runs and bills (a real cryptographic
        # `signature` and real `thinking_tokens` on every call) but the CLI withholds
        # the TEXT unless the caller explicitly opts back in. Confirmed live, on our own
        # exact argv (isolation flags + stream-json stdin) and a subscription (OAuth)
        # login: `--thinking adaptive --thinking-display summarized` alone restores real
        # streamed thinking_delta content and a populated final `thinking` block — every
        # other documented workaround (`--max-thinking-tokens`, `MAX_THINKING_TOKENS`,
        # `--forward-subagent-text`) was tried first and changed nothing. Tracked
        # upstream: anthropics/claude-code#56356 (binary-level trace of the same flag
        # wiring into the API request). Passed whenever thinking is not explicitly OFF,
        # mirroring the `--effort` gate above.
        if not _no_thinking(effort):
            argv += ["--thinking", "adaptive", "--thinking-display", "summarized"]
        if max_budget_usd and max_budget_usd > 0:
            argv += ["--max-budget-usd", str(max_budget_usd)]
        if json_schema:
            argv += ["--json-schema", json.dumps(json_schema, ensure_ascii=False)]
        return argv

    async def _spawn(
        self,
        argv: list[str],
        prompt: str,
        no_thinking: bool = False,
        images: list[dict] | None = None,
    ):
        """Start the CLI in an empty temp cwd with the prompt written to stdin."""
        # A fresh empty directory: no repo access and nothing for CLAUDE.md discovery to
        # find, even if a future CLI version ignores --setting-sources.
        decode_image_payloads(images or [])
        workdir = tempfile.mkdtemp(prefix="onw-claude-cli-")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=_child_env(self.api_key, no_thinking=no_thinking),
                limit=_STDOUT_LIMIT,
            )
            content: list[dict] = [{"type": "text", "text": prompt}]
            for img in images or []:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.get("media_type") or "image/jpeg",
                            "data": img.get("data") or "",
                        },
                    }
                )
            payload = json.dumps(
                {"type": "user", "message": {"role": "user", "content": content}},
                ensure_ascii=False,
            )
            assert proc.stdin is not None
            proc.stdin.write((payload + "\n").encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            return proc, workdir
        except BaseException:
            try:
                if proc is not None and proc.returncode is None:
                    await _kill(proc)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
            raise

    async def _events(
        self,
        argv: list[str],
        prompt: str,
        no_thinking: bool = False,
        images: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield the CLI's NDJSON events, bounded by the semaphore and the timeout."""
        async with _semaphore():
            proc, workdir = await self._spawn(argv, prompt, no_thinking=no_thinking, images=images)
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
                    stalled = _stall_seconds() < remaining
                    try:
                        line = await asyncio.wait_for(
                            proc.stdout.readline(),
                            timeout=_stall_seconds() if stalled else remaining,
                        )
                    except asyncio.TimeoutError:
                        if stalled:
                            await _kill(proc)
                            raise ProviderTimeoutError(
                                f"Claude Code CLI produced no output for {_stall_seconds():.0f}s "
                                f"(raise {claude_live_session.STALL_ENV} if this is expected).",
                                stalled=True,
                            ) from None
                        raise
                    if not line:
                        break
                    try:
                        yield json.loads(line.decode("utf-8", "replace"))
                    except (TypeError, ValueError):
                        # A non-JSON line is CLI chatter, not our contract — log at debug
                        # and keep reading rather than failing the whole turn.
                        log.debug("claude_cli: skipping non-JSON stdout line: %.200s", line)
                rc = await proc.wait()
                if rc != 0:
                    detail = b"".join(errs).decode("utf-8", "replace").strip()[:500]
                    raise ExternalServiceError(
                        f"Claude Code CLI exited with status {rc}: {detail or '(no stderr)'}"
                    )
            except asyncio.TimeoutError:
                await _kill(proc)
                raise ProviderTimeoutError(
                    f"Claude Code CLI timed out after {_timeout_seconds():.0f}s "
                    "(raise ONW_CLAUDE_CLI_TIMEOUT if this is expected)."
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
        full_system = (system or "") + render_tool_manifest(tools or [])
        argv = self._argv(model, full_system, effort=effort)
        no_thinking = _no_thinking(effort)
        # MAX_THINKING_TOKENS rides in the ENV, not argv, so two otherwise-identical
        # invocations can differ in it. Discriminate the session key or a thinking-off turn
        # could be served by a session spawned with thinking on.
        key_argv = argv + (["__thinking_off__"] if no_thinking else [])
        use_sessions = claude_live_session.sessions_enabled()

        attempts = _truncation_retries() + 1
        # A stalled attempt (the CLI went silent) earns ONE extra, silent attempt on a fresh
        # process — see ProviderTimeoutError.stalled. Past that it is a plain failure.
        stall_retries_left = 1
        scanner = ToolCallScanner()
        t0 = time.monotonic()
        ttft_ms: Optional[float] = None
        usage_event: Optional[dict] = None
        stop_reason = "end_turn"
        emitted_any_delta = False
        fallback_text = ""
        api_key_source = ""

        # `turn_messages` starts as the caller's own list; a narration retry below EXTENDS
        # it with a synthetic exchange rather than mutating it in place, so the caller's
        # list comes out of this call exactly as it went in.
        turn_messages = messages
        narration_retries = 0
        # The abandoned round's own usage — real tokens/dollars genuinely spent before it
        # narrated instead of calling anything — merged into the retry's once BOTH are known.
        prior_round_usage: Optional[dict] = None

        while True:
            prompt, prompt_images = flatten_messages_with_images(turn_messages)
            attempt = 0
            while True:
                attempt += 1
                scanner = ToolCallScanner()
                t0 = time.monotonic()
                ttft_ms = None
                usage_event = None
                peak_prompt_tokens: Optional[int] = None
                stop_reason = "end_turn"
                emitted_any_delta = False
                emitted_anything = False
                fallback_text = ""
                api_key_source = ""
                # How many of scanner.calls have already been yielded as tool_use_start/
                # tool_use — checked after every scanner.feed(), so a pill fires the moment
                # its block actually closes relative to the surrounding text, not batched to
                # the end of the round regardless of where in the model's own output the
                # call really happened (live incident 2026-08-10: the UI showed pills after a
                # whole fabricated summary and there was no way to tell whether that was the
                # true order or an artifact of this batching — it was the latter).
                emitted_call_count = 0
                # A `result` event carrying `is_error` is recorded rather than raised on the
                # spot, so the `finally` below can retire the session BEFORE we decide whether
                # this is recoverable — see the poisoned-session branch after the loop.
                cli_error: Optional[str] = None
                reused = False
                watch = PostCallWatch(grace_seconds())

                session = None
                if use_sessions:
                    # A fresh CLI can exit right after starting (first run after a sign-in,
                    # a slow start on a small server): retry once before telling the person.
                    # Its own counter: reusing `attempt` here reset the outer loop's count
                    # every pass, so a CLI that kept exiting without a result retried forever.
                    for start_try in (1, 2):
                        (
                            session,
                            pending,
                            pending_images,
                            reused,
                        ) = await claude_live_session.acquire(
                            argv,
                            _child_env(self.api_key, no_thinking=no_thinking),
                            turn_messages,
                            key_argv=key_argv,
                        )
                        try:
                            await session.send(pending, images=pending_images)
                            break
                        except ExternalServiceError as exc:
                            await claude_live_session.release(
                                key_argv, session, turn_messages, ok=False
                            )
                            session = None
                            if start_try == 2:
                                raise
                            log.warning("claude_cli: session exited at start, retrying: %s", exc)
                        except BaseException:
                            await claude_live_session.release(
                                key_argv, session, turn_messages, ok=False
                            )
                            session = None
                            raise
                    source = session.events()
                    log.debug(
                        "claude_cli: turn on a %s session (%d chars sent)",
                        "reused" if reused else "fresh",
                        len(pending),
                    )
                else:
                    source = self._events(
                        argv, prompt, no_thinking=no_thinking, images=prompt_images
                    )

                try:
                    async for ev in _until_call_grace(source, scanner, watch):
                        etype = ev.get("type")
                        if etype == "system" and ev.get("subtype") == "init":
                            api_key_source = ev.get("apiKeySource") or ""
                        elif etype == "stream_event":
                            inner = ev.get("event") or {}
                            if inner.get("type") == "content_block_delta":
                                delta = inner.get("delta") or {}
                                dtype = delta.get("type")
                                if dtype == "thinking_delta" and delta.get("thinking"):
                                    emitted_anything = True
                                    yield {"type": "thinking_delta", "text": delta["thinking"]}
                                elif dtype == "text_delta" and delta.get("text"):
                                    if ttft_ms is None:
                                        ttft_ms = (time.monotonic() - t0) * 1000.0
                                    emitted_any_delta = True
                                    # A call goes out before the text the scanner yields after
                                    # it: one delta can close a call and start the next line,
                                    # and the chat flushes its held line at each call — text
                                    # after the call released there sat ABOVE the row, cut off
                                    # its line (a lone "W", live 2026-10-31).
                                    for visible in chain(scanner.feed(delta["text"]), ("",)):
                                        while emitted_call_count < len(scanner.calls):
                                            call = scanner.calls[emitted_call_count]
                                            call_id = f"cli_{int(t0 * 1000)}_{emitted_call_count}"
                                            emitted_anything = True
                                            yield {
                                                "type": "tool_use_start",
                                                "id": call_id,
                                                "name": call["name"],
                                            }
                                            yield {
                                                "type": "tool_use",
                                                "id": call_id,
                                                "name": call["name"],
                                                "input": call["input"],
                                            }
                                            emitted_call_count += 1
                                            watch.call_parsed()
                                        if visible:
                                            emitted_anything = True
                                            yield {"type": "text_delta", "text": visible}
                            elif inner.get("type") == "message_delta":
                                stop_reason = (inner.get("delta") or {}).get(
                                    "stop_reason"
                                ) or stop_reason
                            elif inner.get("type") == "message_start":
                                peak_prompt_tokens = _max_prompt(
                                    peak_prompt_tokens, _message_start_prompt(inner)
                                )
                        elif etype == "result":
                            fallback_text = ev.get("result") or ""
                            if ev.get("is_error"):
                                cli_error = _result_error(ev)
                                break
                            usage_event = _usage_event(
                                ev, model, ttft_ms, t0, api_key_source, peak_prompt_tokens
                            )

                except ProviderTimeoutError as exc:
                    log.warning("claude_cli: %s — %s", exc, watch.summary())
                    # Nothing reached the user, so a fresh process is invisible to them. Once
                    # something was shown, retrying would duplicate it.
                    if not exc.stalled or emitted_anything or stall_retries_left <= 0:
                        raise
                    stall_retries_left -= 1
                    attempts += 1
                    log.warning("claude_cli: %s — retrying once on a fresh process", exc)
                    continue
                finally:
                    if session is not None:
                        # A turn that produced no result event leaves the session in an
                        # unknown state — retire it rather than reuse a poisoned one.
                        await claude_live_session.release(
                            key_argv, session, turn_messages, ok=usage_event is not None
                        )

                if watch.cut:
                    # No result event will come, so this round's usage is unknown; the CLI
                    # session was retired above (mid-reply, it cannot take the next send).
                    log.warning(
                        "claude_cli: round wrote its tool call(s) but no result event "
                        "followed within %.0fs — running them (%s)",
                        watch.grace,
                        watch.summary(),
                    )
                    usage_event = _usage_event(
                        {}, model, ttft_ms, t0, api_key_source, peak_prompt_tokens
                    )

                if cli_error is not None:
                    # A REUSED session can be poisoned by the model's OWN reply. The CLI holds
                    # the conversation once a session is live, so an empty assistant message —
                    # which this setup demonstrably produces (`turn_incomplete text=''`, live
                    # 2026-08-12) — sits in its history and every later send is rejected with
                    # `400 messages: text content blocks must be non-empty`. Our side cannot
                    # be the source: a fresh session sends the whole transcript, which always
                    # ends with the `Assistant:` cue, and a reused one is guarded by
                    # `acquire`'s own non-empty check.
                    #
                    # The `finally` above has already retired the session (ok=False), so a
                    # retry necessarily spawns a fresh process and re-sends the full
                    # transcript — which cannot carry the poisoned block. Recoverable, so
                    # recover: only when the session really was reused (a fresh one failing
                    # is a genuine error) and nothing has been shown yet (retrying after
                    # content would duplicate it).
                    if reused and not emitted_anything and attempt < attempts:
                        log.warning(
                            "claude_cli: reused session rejected the turn (%s) — retiring it "
                            "and retrying on a fresh process (attempt %d/%d)",
                            cli_error[:120],
                            attempt + 1,
                            attempts,
                        )
                        continue
                    raise ExternalServiceError(cli_error)

                if usage_event is not None:
                    break

                if emitted_anything or attempt >= attempts:
                    # The terminal event IS the contract — a stream that ends without one
                    # has been truncated (an intermittent CLI behavior — the process exits 0
                    # but never writes a result line). A silent retry already ran above
                    # while nothing had reached the user yet; once real content was already
                    # streamed, or the retry budget is spent, raise rather than record a
                    # turn with no cost and no stop reason (retrying post-content would risk
                    # duplicating it).
                    raise ExternalServiceError(
                        "Claude Code CLI output ended without a result event (the turn was "
                        "truncated). Upgrade the CLI (`claude update`) if this repeats."
                    )
                log.warning(
                    "claude_cli: stream_chat ended with no result event before any content "
                    "was shown — retrying (attempt %d/%d)",
                    attempt + 1,
                    attempts,
                )

            # The inner loop above only exits via `break` (usage_event set) or `raise` —
            # reaching here means the round genuinely completed. If it produced no real
            # tool call AND its entire text is the model narrating one instead of actually
            # emitting it, resume with a nudge rather than presenting that as the answer —
            # the codex_app_server transport's identical fix for the identical failure
            # shape, adapted for a transport with no native "was this commentary" signal
            # (see looks_like_an_unfenced_tool_call's docstring). Bounded by
            # _MAX_NARRATION_RETRIES: a model that narrates the call again instead of
            # making it is not going to act on a third ask.
            if (
                not scanner.has_calls
                and narration_retries < _MAX_NARRATION_RETRIES
                and looks_like_an_unfenced_tool_call(fallback_text)
            ):
                narration_retries += 1
                log.info(
                    "claude_cli: turn ended on an unfenced tool-call narration with "
                    "nothing to act on — continuing (%d/%d)",
                    narration_retries,
                    _MAX_NARRATION_RETRIES,
                )
                # This round's usage is real and must not be silently dropped — merged into
                # whatever round finally closes the turn, once that happens below.
                prior_round_usage = (
                    usage_event
                    if prior_round_usage is None
                    else _merge_narration_retry_usage(prior_round_usage, usage_event)
                )
                turn_messages = [
                    *turn_messages,
                    {"role": "assistant", "content": fallback_text},
                    {"role": "user", "content": _CONTINUE_NUDGE},
                ]
                continue
            break

        if prior_round_usage is not None and usage_event is not None:
            usage_event = _merge_narration_retry_usage(prior_round_usage, usage_event)

        # Partial-message deltas can be absent (older CLI / a flag-less build): fall back
        # to the final result text so a turn never silently produces nothing.
        if not emitted_any_delta and fallback_text:
            for visible in scanner.feed(fallback_text):
                if visible:
                    yield {"type": "text_delta", "text": visible}
        tail = scanner.flush()
        if tail:
            yield {"type": "text_delta", "text": tail}

        yield usage_event

        # Whatever the main loop above already emitted inline is skipped here — this only
        # covers a call parsed OUTSIDE that loop (the no-partial-deltas fallback path, or
        # scanner.flush()'s tail — neither of which has a "real time" to preserve anyway).
        for i in range(emitted_call_count, len(scanner.calls)):
            call = scanner.calls[i]
            call_id = f"cli_{int(t0 * 1000)}_{i}"
            yield {"type": "tool_use_start", "id": call_id, "name": call["name"]}
            yield {
                "type": "tool_use",
                "id": call_id,
                "name": call["name"],
                "input": call["input"],
            }

        yield {"type": "done", "stop_reason": "tool_use" if scanner.calls else stop_reason}

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        # The CLI exposes no output-token cap (only --max-budget-usd), so a caller's
        # max_tokens CANNOT be enforced on this transport — it is a request, not a limit.
        # Callers that depend on a hard cap should not be pinned to a CLI provider.
        max_tokens: int = 2048,  # noqa: ARG002 - no CLI flag exists; see comment above
        source: str = "complete",
        images: list[dict] | None = None,
        response_format: dict | None = None,
        effort: str = "",
    ) -> str:
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]

        argv = self._argv(
            model,
            system or "",
            effort=effort,
            json_schema=_json_schema_of(response_format),
        )

        attempts = _truncation_retries() + 1
        # A SEPARATE, larger budget for error_max_structured_output_retries specifically (owner
        # report 2026-08-30) — see _structured_output_retries()'s own docstring. The outer loop
        # bound is the larger of the two so either failure mode can use its own budget; which
        # one applies for a GIVEN attempt is decided below, once that attempt's actual failure
        # reason (retryable_error vs. everything else) is known.
        structured_output_attempts = _structured_output_retries() + 1
        max_attempts = max(attempts, structured_output_attempts)
        text = ""
        usage: dict = {}
        cost = 0.0
        api_key_source = ""
        saw_result = False
        for attempt in range(1, max_attempts + 1):
            text = ""
            usage = {}
            cost = 0.0
            api_key_source = ""
            saw_result = False
            retryable_error = ""
            silent_crash: ExternalServiceError | None = None
            replies: list[str] = []
            try:
                async for ev in self._events(
                    argv, prompt, no_thinking=_no_thinking(effort), images=images
                ):
                    if ev.get("type") == "system" and ev.get("subtype") == "init":
                        api_key_source = ev.get("apiKeySource") or ""
                        continue
                    if ev.get("type") == "assistant":
                        replies.append(_assistant_text(ev))
                        continue
                    if ev.get("type") != "result":
                        continue
                    if ev.get("is_error"):
                        log.error(
                            "claude_cli ERROR EVENT source=%s subtype=%s num_turns=%s usage=%s "
                            "duration_ms=%s",
                            source,
                            ev.get("subtype"),
                            ev.get("num_turns"),
                            ev.get("usage"),
                            ev.get("duration_ms"),
                        )
                        _log_failed_replies(replies)
                        if ev.get("subtype") == _RETRYABLE_ERROR_SUBTYPE:
                            # INT19-follow-up (owner-diagnosed 2026-08-25): "max structured-
                            # output retries" names the CLI's OWN internal --json-schema
                            # conformance loop giving up within ITS turn budget, not a request-
                            # level failure — a fresh spawn gets a fresh turn budget, so this
                            # retries on the SAME budget as truncation below.
                            #
                            # Two distinct real causes, confirmed live 2026-08-30/31: (1) an
                            # untyped `list[dict]` response_model field gives the schema no
                            # item-level shape, and the CLI produced doubly-escaped JSON for it —
                            # fixed at the source (monitoring.analyst._KeyIssue is now a real
                            # nested model; verified via 14/14 clean live reproductions). (2) the
                            # CLI's tool-calling itself sometimes emits a literal broken stub
                            # (`{"$PARAMETER_NAME": ...}`) even against an already-typed schema
                            # (observed on GLDM post-fix) — that's CLI-internal flakiness no
                            # caller-side schema change reaches, so retrying is the right
                            # response to it.
                            retryable_error = ev.get("subtype") or ""
                            break
                        raise ExternalServiceError(_result_error(ev))
                    log.info(
                        "claude_cli OK num_turns=%s output_tokens=%s duration_ms=%s",
                        ev.get("num_turns"),
                        (ev.get("usage") or {}).get("output_tokens"),
                        ev.get("duration_ms"),
                    )
                    saw_result = True
                    # With --json-schema the CLI puts the VALIDATED object in
                    # structured_output; every caller of complete(response_format=…) parses
                    # JSON, so serialise it back rather than trusting the prose `result` to
                    # be well-formed JSON.
                    structured = ev.get("structured_output")
                    if structured is not None:
                        text = json.dumps(structured, ensure_ascii=False)
                    else:
                        text = ev.get("result") or ""
                    usage = ev.get("usage") or {}
                    cost = float(ev.get("total_cost_usd") or 0.0)
            except ExternalServiceError as exc:
                # A second, DIFFERENT transient failure mode found live 2026-08-25 debugging
                # INT20: the CLI process exits nonzero with EMPTY stderr — no explanation at
                # all, the shape of a silent crash rather than a real, self-explaining error
                # (bad API key, real config error — those DO write to stderr and must still
                # raise on the first occurrence, since retrying an explained, deterministic
                # failure only wastes quota). `_events()` raises this as a bare exception
                # (not an `is_error` result event), so it must be caught here to reach the
                # SAME retry budget as the two cases above, rather than propagating straight
                # through this attempt loop untouched as it did before this fix.
                if not _is_silent_cli_crash(exc):
                    raise
                silent_crash = exc

            if saw_result:
                break

            if retryable_error:
                record_structured_output_error(model)

            # Each failure reason is checked against ITS OWN budget — retryable_error (the
            # CLI's own structured-output conformance give-up) gets structured_output_attempts,
            # everything else (silent crash, no result event) gets the original `attempts`.
            budget = structured_output_attempts if retryable_error else attempts
            if attempt >= budget:
                if silent_crash is not None:
                    raise silent_crash
                if retryable_error:
                    raise ExternalServiceError(
                        f"Claude Code CLI reported an error: {retryable_error} "
                        f"(exhausted {budget} attempts)"
                    )
                raise ExternalServiceError(
                    "Claude Code CLI output ended without a result event (the turn was "
                    "truncated). Upgrade the CLI (`claude update`) if this repeats."
                )
            # complete() never streams partial output to a caller — unlike stream_chat,
            # a retry here can never duplicate anything the caller has already seen.
            if silent_crash is not None:
                reason = f"a silent crash ({silent_crash})"
            elif retryable_error:
                reason = f"error {retryable_error}"
            else:
                reason = "no result event"
            log.warning(
                "claude_cli: complete() source=%s attempt %d/%d ended with %s — retrying",
                source,
                attempt,
                budget,
                reason,
            )

        basis = cost_basis_for(api_key_source)
        # Owner incident 2026-08-31 (KODK, openai_provider's own equivalent fix): log the
        # actual final response text on the SUCCESS path too — previously only
        # `_log_failed_replies(replies)` (above, error path) ever logged response content;
        # a call that ran unusually long but still succeeded had nothing to show what it
        # actually produced. Bounded, logged only — see openai_provider.complete's matching
        # comment for why this isn't in cost_log.
        _LOG_TEXT_CAP = 8000
        log.info(
            "complete_done  source=%s  transport=claude_cli  model=%s  basis=%s  input=%d  "
            "cache_write=%d  cache_read=%d  output=%d  cost_usd=%.5f  content_chars=%d\n"
            "--- content (first %d chars) ---\n%s",
            source,
            model,
            basis,
            usage.get("input_tokens", 0) or 0,
            usage.get("cache_creation_input_tokens", 0) or 0,
            usage.get("cache_read_input_tokens", 0) or 0,
            usage.get("output_tokens", 0) or 0,
            cost,
            len(text),
            _LOG_TEXT_CAP,
            text[:_LOG_TEXT_CAP],
        )
        from backend.ai.cost_tracker import record
        from backend.db.model_config_store import provider_label_for

        record(
            source,
            model,
            usage.get("input_tokens", 0) or 0,
            usage.get("output_tokens", 0) or 0,
            usage.get("cache_creation_input_tokens", 0) or 0,
            usage.get("cache_read_input_tokens", 0) or 0,
            cost_usd=cost,
            cost_basis=basis,
            provider=provider_label_for("claude_cli", ""),
        )
        return text

    async def stream_complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,  # noqa: ARG002 - no CLI flag exists, matches complete()
        source: str = "complete",
        effort: str = "",
    ) -> AsyncGenerator[dict, None]:
        """The streaming twin of ``complete()`` — COARSE on this transport: the CLI's
        plain (non-partial-message) event stream only yields a whole "assistant" message
        at a time, not per-token deltas (only ``stream_chat``'s ``--include-partial-
        messages`` path has that granularity, and hijacking its session/tool-scanning
        machinery for a single background completion isn't worth the complexity). So this
        yields ONE ``text_delta`` with the full reply once the CLI's result event
        arrives — a caller computing progress from it sees 0% until completion, then
        100%, never a fabricated partial number this transport cannot actually back up.
        """
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]

        argv = self._argv(model, system or "", effort=effort)

        attempts = _truncation_retries() + 1
        text = ""
        usage: dict = {}
        cost = 0.0
        api_key_source = ""
        saw_result = False
        for attempt in range(1, attempts + 1):
            text = ""
            usage = {}
            cost = 0.0
            api_key_source = ""
            saw_result = False
            async for ev in self._events(argv, prompt, no_thinking=_no_thinking(effort)):
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    api_key_source = ev.get("apiKeySource") or ""
                    continue
                if ev.get("type") != "result":
                    continue
                if ev.get("is_error"):
                    raise ExternalServiceError(_result_error(ev))
                saw_result = True
                text = ev.get("result") or ""
                usage = ev.get("usage") or {}
                cost = float(ev.get("total_cost_usd") or 0.0)

            if saw_result:
                break
            if attempt >= attempts:
                raise ExternalServiceError(
                    "Claude Code CLI output ended without a result event (the turn was "
                    "truncated). Upgrade the CLI (`claude update`) if this repeats."
                )
            log.warning(
                "claude_cli: stream_complete() ended with no result event — retrying "
                "(attempt %d/%d)",
                attempt + 1,
                attempts,
            )

        if text:
            yield {"type": "text_delta", "text": text}

        basis = cost_basis_for(api_key_source)
        log.info(
            "stream_complete_done  source=%s  transport=claude_cli  model=%s  basis=%s  "
            "input=%d  cache_write=%d  cache_read=%d  output=%d  cost_usd=%.5f",
            source,
            model,
            basis,
            usage.get("input_tokens", 0) or 0,
            usage.get("cache_creation_input_tokens", 0) or 0,
            usage.get("cache_read_input_tokens", 0) or 0,
            usage.get("output_tokens", 0) or 0,
            cost,
        )
        from backend.ai.cost_tracker import record
        from backend.db.model_config_store import provider_label_for

        record(
            source,
            model,
            usage.get("input_tokens", 0) or 0,
            usage.get("output_tokens", 0) or 0,
            usage.get("cache_creation_input_tokens", 0) or 0,
            usage.get("cache_read_input_tokens", 0) or 0,
            cost_usd=cost,
            cost_basis=basis,
            provider=provider_label_for("claude_cli", ""),
        )


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


def _assistant_text(ev: dict) -> str:
    """The text of one assistant event, or "" — the model's ACTUAL reply, which `complete`
    otherwise discards.

    Captures BOTH plain prose (`text` blocks) AND a structured-output attempt (`tool_use`
    blocks) — found live, 2026-08-25, debugging an `error_max_structured_output_retries`
    incident: `--json-schema` mode delivers the model's answer as a `tool_use` call (named
    `StructuredOutput` by the CLI internally, an implementation detail we don't hardcode a
    dependency on), NEVER as a `text` block. Before this fix, EVERY `complete(response_
    format=...)` caller's real answer attempt was invisible here — the model's actual
    (possibly schema-invalid) submission on a failed turn, exactly what `_log_failed_replies`
    exists to show, was silently dropped. `thinking` blocks are deliberately excluded — they
    are reasoning, not a reply attempt."""
    content = ((ev.get("message") or {}).get("content")) or []
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            parts.append(str(part.get("text") or ""))
        elif part.get("type") == "tool_use":
            parts.append(
                f"[tool_use {part.get('name') or '?'}] "
                f"{json.dumps(part.get('input'), ensure_ascii=False, default=str)}"
            )
    return "".join(parts)


def _log_failed_replies(replies: list[str]) -> None:
    """What the model actually produced on a failed turn.

    Without this a structured-output failure is unfalsifiable: the error names a subtype and
    a token count, and every diagnosis is a guess about output that nobody ever looked at.
    Logs, per attempt: its length, whether it parses as JSON, and its TAIL — a reply cut off
    by an output ceiling ends mid-token, a schema-invalid one ends with a closing brace."""
    for i, reply in enumerate(replies, start=1):
        if not reply:
            continue
        try:
            json.loads(reply)
            shape = "parses as JSON"
        except ValueError as exc:
            shape = f"NOT valid JSON ({exc})"
        log.error(
            "claude_cli failed reply %d/%d: %d chars, %s | tail=%r",
            i,
            len(replies),
            len(reply),
            shape,
            reply[-400:],
        )


def _destrictify_for_cli(node: object) -> object:
    """Undo `core.llm_seam.strictify_schema`'s OpenAI/Codex-strict-mode shaping before a schema
    becomes the CLI's `--json-schema` (owner report 2026-08-31, root-caused live): every schema
    reaching this transport has ALREADY been strictified upstream (`llm_seam._response_format_
    for` calls it unconditionally, for every transport) — `additionalProperties: false` stamped
    on every object, and every optional field forced into `required` with its type widened to a
    nullable union (`[T, "null"]`), because THAT is what OpenAI/Codex's strict structured-output
    mode actually requires (see `strictify_schema`'s own docstring). The `claude` CLI's own
    `--json-schema` conformance checking never needed either transformation — and empirically,
    it makes reliability WORSE, not better: the real analyst schema (13 top-level fields, all
    but one originally optional, plus a nested `key_issues` array) reproducibly failed
    (`error_max_structured_output_retries`, all 4 attempts exhausted, 384s) when sent
    strictified, on a REAL production prompt/schema pair that the SAME content, sent un-
    strictified, succeeded on 40+/40+ consecutive isolated runs.

    ONLY called by `_json_schema_of` when `_response_format_for`'s `_pydantic_derived` marker is
    set — i.e. ONLY for a schema `ai.tools.schema_from_model.schema_of()` derived from a
    Pydantic model (code-review-caught regression, 2026-08-31: the reversal below is exact for
    THAT case — this repo's own Pydantic models never author `Optional[X]`/`X | None`, so a
    `[T, "null"]` union only ever entered the schema via `strictify_schema` — but is NOT exact
    for a hand-written `services/` schema dict passed to `complete_json` directly, several of
    which deliberately keep a field BOTH nullable AND required as their own fix for a DIFFERENT
    prior bug; blindly reversing those corrupted them). For a property whose type strictify
    widened: drop the `null` arm (restore the original type) and drop the property from
    `required` (restore "may be omitted"). A property whose type was left alone (unchanged,
    non-nullable) was genuinely required to begin with (e.g. `_AnalysisOutput.why`) and is left
    untouched. `additionalProperties: false` is dropped everywhere — a Pydantic-derived schema
    was never authored expecting it. Recurses through `properties`/`items`/`$defs`/`definitions`
    so a nested model (`key_issues`'s own `_KeyIssue` items) gets the same treatment."""
    if isinstance(node, list):
        return [_destrictify_for_cli(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _destrictify_for_cli(v) for k, v in node.items() if k != "additionalProperties"}
    required = out.get("required")
    props = out.get("properties")
    if isinstance(required, list) and isinstance(props, dict):
        still_required = []
        for name in required:
            prop = props.get(name)
            prop_type = prop.get("type") if isinstance(prop, dict) else None
            non_null = (
                [t for t in prop_type if t != "null"] if isinstance(prop_type, list) else None
            )
            if non_null and len(non_null) < len(prop_type):
                prop["type"] = non_null[0] if len(non_null) == 1 else non_null
            else:
                still_required.append(name)
        out["required"] = still_required
    return out


def _json_schema_of(response_format: dict | None) -> dict | None:
    """Translate the OpenAI-shaped ``response_format`` to the CLI's ``--json-schema``.

    A schemaless ``{"type": "json_object"}`` has no CLI equivalent, so it degrades to
    prompt-and-parse (the callers all instruct JSON in the prompt and parse tolerantly).

    `_destrictify_for_cli` only runs when the schema carries `core.llm_seam._response_format_
    for`'s own `_pydantic_derived` marker (code-review-caught regression, 2026-08-31: reversing
    strictify_schema's required+nullable widening is only PROVABLE-safe for a schema `schema_of()`
    derived from a Pydantic model — several hand-written `services/` schemas passed to
    `complete_json` directly deliberately keep a field BOTH nullable AND required as their OWN
    fix for a different prior bug, and blindly reversing that corrupts them). The marker is a
    private, provider-facing signal — never a real JSON Schema keyword — so it's popped off
    before the schema is used for anything, including the CLI-flat "otherwise unchanged" path."""
    if not response_format:
        return None
    if response_format.get("type") == "json_schema":
        json_schema = response_format.get("json_schema") or {}
        schema = json_schema.get("schema")
        if isinstance(schema, dict):
            return _destrictify_for_cli(schema) if json_schema.get("_pydantic_derived") else schema
    return None


def cost_basis_for(api_key_source: str) -> str:
    """Classify what the CLI's reported dollar figure MEANS for this session.

    ``system/init.apiKeySource`` is ``"none"`` when the CLI is running on the user's
    interactive login (a Pro/Max subscription). Anthropic's own docs are explicit that
    the session cost figure "isn't relevant for billing purposes" there — it is a
    locally-computed LIST PRICE for usage that is already included in the plan. That is
    worse than reporting zero, because it looks like a real charge. So the number is
    still recorded (it is the honest "what this turn would have cost" signal, and hiding
    it would understate consumption of a rate-limited quota) but it is STAMPED, so a
    reader can tell an estimate from an invoice.
    """
    return "plan_included" if (api_key_source or "").lower() in ("none", "") else "estimate"


async def _until_call_grace(
    source: AsyncGenerator[dict, None], scanner: ToolCallScanner, watch: PostCallWatch
) -> AsyncGenerator[dict, None]:
    """``source``'s events, ending early (``watch.cut``) once the round's newest parsed tool
    call is older than the grace — see ``cli_call_grace``."""
    try:
        while True:
            wait = watch.remaining(scanner.mid_block)
            if wait is not None and wait <= 0:
                watch.cut = True
                return
            pending = source.__anext__()
            try:
                ev = await (pending if wait is None else asyncio.wait_for(pending, wait))
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                watch.cut = True
                return
            watch.saw(ev)
            yield ev
    finally:
        await source.aclose()


def _message_start_prompt(inner: dict) -> Optional[int]:
    """Prompt size of ONE API request, from its ``message_start`` stream event."""
    usage = (inner.get("message") or {}).get("usage")
    if not isinstance(usage, dict):
        return None
    return (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )


def _max_prompt(a: Optional[int], b: Optional[int]) -> Optional[int]:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _reported_context_window(result: dict) -> Optional[int]:
    """The context window the CLI says served this round (``modelUsage[*].contextWindow``).

    A bare alias like ``sonnet`` is in no price table, so a table lookup falls back to
    200K while the CLI really runs a 1M-window model. With more than one model in the
    envelope (the CLI may call a small helper model), the one that read the most input
    is the conversation's model.
    """
    best: Optional[tuple[int, int]] = None
    for entry in (result.get("modelUsage") or {}).values():
        if not isinstance(entry, dict):
            continue
        window = entry.get("contextWindow")
        if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
            continue
        read = (
            (entry.get("inputTokens") or 0)
            + (entry.get("cacheReadInputTokens") or 0)
            + (entry.get("cacheCreationInputTokens") or 0)
        )
        if best is None or read > best[0]:
            best = (read, window)
    return best[1] if best else None


def _usage_event(
    result: dict,
    model: str,
    ttft_ms: Optional[float],
    t0: float,
    api_key_source: str = "",
    peak_prompt_tokens: Optional[int] = None,
) -> dict:
    """Build the normalised ``usage`` event from the CLI's final ``result`` envelope.

    ``result.usage`` sums every API request the CLI made this round; ``prompt_tokens`` is
    the largest SINGLE request's prompt (from ``message_start``), which is what context
    pressure means.
    """
    usage = result.get("usage") or {}
    cache_creation = usage.get("cache_creation") or {}
    return {
        "prompt_tokens": peak_prompt_tokens,
        "context_window": _reported_context_window(result),
        "cost_basis": cost_basis_for(api_key_source),
        "type": "usage",
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_creation_5m_tokens": cache_creation.get("ephemeral_5m_input_tokens", 0) or 0,
        "cache_creation_1h_tokens": cache_creation.get("ephemeral_1h_input_tokens", 0) or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        # The CLI's own figure — authoritative, never a table estimate.
        "cost_usd": float(result.get("total_cost_usd") or 0.0),
        # So a bare alias ("sonnet") no price table knows still reads as a known price.
        "cost_reported": result.get("total_cost_usd") is not None,
        "model": result.get("model") or model,
        "ttft_ms": ttft_ms if ttft_ms is not None else result.get("ttft_ms"),
        "duration_ms": result.get("duration_ms") or (time.monotonic() - t0) * 1000.0,
    }


def _merge_narration_retry_usage(first: dict, second: dict) -> dict:
    """Combine two rounds' usage into one, for a turn a narration retry resumed.

    The abandoned round genuinely spent real tokens/dollars before it narrated instead of
    calling anything — reporting only the RETRY round's usage would silently under-report
    what the turn actually cost. Token/cost/duration fields sum; ``model``/``cost_basis``
    come from ``second`` (the round that actually finished the turn); ``ttft_ms`` is
    ``first``'s — that IS when the user's screen first showed anything for this turn.
    """
    summed = (
        "input_tokens",
        "cache_creation_tokens",
        "cache_creation_5m_tokens",
        "cache_creation_1h_tokens",
        "cache_read_tokens",
        "output_tokens",
        "cost_usd",
        "duration_ms",
    )
    merged = dict(second)
    for key in summed:
        merged[key] = (first.get(key) or 0) + (second.get(key) or 0)
    merged["ttft_ms"] = (
        first.get("ttft_ms") if first.get("ttft_ms") is not None else second.get("ttft_ms")
    )
    merged["cost_reported"] = bool(first.get("cost_reported") and second.get("cost_reported"))
    merged["prompt_tokens"] = _max_prompt(first.get("prompt_tokens"), second.get("prompt_tokens"))
    merged["context_window"] = second.get("context_window") or first.get("context_window")
    return merged
