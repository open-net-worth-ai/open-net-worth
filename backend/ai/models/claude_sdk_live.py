"""One Claude Code query, kept OPEN across the host's tool rounds (flag ``claude_cli_native_tools``).

The host owns the tool loop: ``assistant.run`` dispatches every call through the choke points,
``tool_permissions``, the confirm gate and the audit trail. So a provider round ends at the model's
tool calls, exactly as every other transport's does. What this module adds is that the CLI is NOT
told the round ended: its calls are PARKED. Each parked call is a ``PostToolUse`` hook awaiting a
future keyed by the call's id; the next round — when its messages carry that call's result —
resolves the future with the HOST's real result, and the same query goes on from there, with its
reasoning and prompt cache intact. The model never sees anything but what the host answered.

The SDK client lives in ONE dedicated task (``_run``): it holds an anyio task group that must be
entered and left by the same task, and rounds, the park timer and a stopped turn all arrive on
other ones. Closing is therefore always "cancel that task"; its ``finally`` releases every parked
future and makes sure the CLI process is gone.

``core.process_tree`` is deliberately not used for the kill: the SDK spawns the CLI in OUR process
group (no new session), so ``killpg`` would signal this server too. The SDK's own close escalates
EOF -> SIGTERM -> SIGKILL on the one child, and a last ``kill()`` covers a child that survived it.

ADR ``2026-11-23-the-claude-cli-gets-tools-as-native-calls-and-the-host-still-runs-them``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from contextlib import suppress
from typing import Any, AsyncGenerator, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
)
from claude_agent_sdk.types import StreamEvent

from backend.ai.models import claude_sdk_tools as sdk_tools
from backend.ai.models.claude_sdk_usage import Round, result_failure, round_usage
from backend.core.errors import ExternalServiceError, ProviderTimeoutError

log = logging.getLogger(__name__)

_DEFAULT_PARK_SECONDS = 900.0
# A parked turn is an idle CLI process: bound how many may wait at once (oldest closed first).
_MAX_PARKED = 16
# How long a round waits for sibling calls whose blocks have started but not yet arrived.
_SIBLING_GRACE = 2.0
_END = object()


def park_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("ONW_CLAUDE_SDK_PARK_SECONDS") or _DEFAULT_PARK_SECONDS))
    except ValueError:
        return _DEFAULT_PARK_SECONDS


class LiveQuery:
    """A started Claude Code query that one or more host rounds read from."""

    def __init__(
        self, *, key: dict, offered: dict[str, str], model: str, loop: asyncio.AbstractEventLoop
    ):
        self.key = key
        self.offered = offered
        self.model = model
        self.loop = loop
        self.messages: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.parked_at = 0.0
        self.bound: set[str] = set()
        self.api_key_source = ""
        self.resolved_model = ""
        self.cost_estimated = 0.0
        self._results: dict[str, asyncio.Future] = {}
        self._task: Optional[asyncio.Task] = None
        self._timer: Optional[asyncio.TimerHandle] = None
        self._workdir = ""
        self._process: Any = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    @classmethod
    async def start(
        cls,
        *,
        system: str,
        tools: list[dict],
        prompt: list[dict],
        model: str,
        effort: str,
        no_thinking: bool,
        api_key: str,
        cli_path: str,
        key: dict,
        client_factory=None,
    ) -> "LiveQuery":
        live = cls(
            key=key,
            offered=sdk_tools.host_names(tools),
            model=model,
            loop=asyncio.get_running_loop(),
        )
        live._workdir = tempfile.mkdtemp(prefix="onw-claude-sdk-")
        prompt_file = os.path.join(live._workdir, "system-prompt.txt")
        with open(prompt_file, "wb") as fh:
            fh.write((system or "").encode("utf-8"))
        options = ClaudeAgentOptions(
            # Lockdown: no built-in tool, nothing from the owner's settings, only our server.
            tools=[],
            allowed_tools=[sdk_tools.PREFIX + "*"],
            mcp_servers={sdk_tools.SERVER: sdk_tools.build_server(tools)},
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="dontAsk",
            system_prompt={"type": "file", "path": prompt_file},
            model=model or None,
            cli_path=cli_path,
            cwd=live._workdir,
            env=sdk_tools.child_env(api_key, no_thinking),
            include_partial_messages=True,
            max_buffer_size=64 * 1024 * 1024,
            stderr=lambda line: log.debug("claude_sdk: stderr: %.300s", line),
            hooks={
                "PreToolUse": [sdk_tools.refuse_unoffered(set(live.offered))],
                "PostToolUse": [
                    HookMatcher(
                        matcher=sdk_tools.PREFIX + ".*",
                        hooks=[live._host_result],
                        timeout=park_seconds() + 120,
                    )
                ],
            },
        )
        if effort:
            options.effort = effort
        if not no_thinking:
            options.thinking = {"type": "adaptive", "display": "summarized"}
        started: asyncio.Future = live.loop.create_future()
        live._task = asyncio.create_task(
            live._run(options, prompt, client_factory or ClaudeSDKClient, started)
        )
        try:
            await started
        except BaseException:
            live.close()
            raise
        return live

    async def _run(self, options, prompt, client_factory, started: asyncio.Future) -> None:
        proc = None
        try:
            async with client_factory(options) as client:
                proc = getattr(getattr(client, "_transport", None), "_process", None)
                self._process = proc
                await client.query(_stream_of(prompt))
                started.set_result(None)
                async for message in client.receive_messages():
                    self.messages.put_nowait(message)
                    if isinstance(message, ResultMessage):
                        break
        except asyncio.CancelledError:
            if not started.done():
                started.cancel()
            raise
        except BaseException as exc:  # noqa: BLE001 — handed to the round that is reading
            if not started.done():
                started.set_exception(exc)
            else:
                self.messages.put_nowait(exc)
        finally:
            self.closed = True
            self._release()
            self.messages.put_nowait(_END)
            await _make_sure_gone(proc)
            shutil.rmtree(self._workdir, ignore_errors=True)

    def close(self) -> None:
        """End the query for good: release every parked call and stop the CLI."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self.closed = True
        self._release()

    def _release(self) -> None:
        _forget(self)
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for future in self._results.values():
            if not future.done():
                future.cancel()
        self._results.clear()
        self.bound = set()

    # ── the parked calls ──────────────────────────────────────────────────────

    def _future(self, call_id: str) -> asyncio.Future:
        future = self._results.get(call_id)
        if future is None:
            future = self._results[call_id] = self.loop.create_future()
        return future

    async def _host_result(self, input_data, tool_use_id, _context) -> dict:
        """The ``PostToolUse`` hook: hold the CLI here until the host has run the call."""
        content, is_error = await self._future(tool_use_id or "")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedMCPToolOutput": sdk_tools.tool_content(content, is_error),
            }
        }

    def resume(self, answers: list[tuple[str, Any, bool]]) -> None:
        """Hand the host's real results to the parked calls; the query goes on by itself."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        _forget(self)
        for call_id, content, is_error in answers:
            future = self._future(call_id)
            if not future.done():
                future.set_result((content, is_error))
        self.bound = set()

    def _park(self, call_ids: list[str]) -> None:
        self.bound = set(call_ids)
        self.parked_at = time.monotonic()
        self._timer = self.loop.call_later(park_seconds(), self._expire)
        _remember(self)

    def _expire(self) -> None:
        log.info("claude_sdk: parked query expired unclaimed — closing it")
        self.close()

    # ── one host round ────────────────────────────────────────────────────────

    async def events(self, *, timeout: float, stall: float) -> AsyncGenerator[dict, None]:
        """Stream ONE host round: until the model's tool call(s) or the end of the query.

        A round that ends on tool calls PARKS the query (the calls' ids are bound, the park timer
        starts) and leaves it open; every other exit closes it."""
        rnd = Round()
        deadline = time.monotonic() + timeout
        settled = False
        try:
            while True:
                message = await self._next(rnd, deadline, timeout, stall)
                for event in self._translate(message, rnd) if message is not None else ():
                    if event.get("type") != "_result":
                        yield event
                        continue
                    settled = True
                    result = event["result"]
                    yield self._usage(rnd, result)
                    yield {"type": "done", "stop_reason": result.stop_reason or "end_turn"}
                    return
                complete = rnd.ended_on_tools and len(rnd.calls) >= rnd.started_calls
                if rnd.calls and (message is None or complete):
                    self._park([c["id"] for c in rnd.calls])
                    settled = True
                    yield self._usage(rnd, None)
                    yield {"type": "done", "stop_reason": "tool_use"}
                    return
        finally:
            if not settled:
                self.close()

    async def _next(self, rnd: Round, deadline: float, timeout: float, stall: float):
        """The next SDK message of this round; ``None`` when the model's call blocks are all in
        (or the wait for a sibling's ran out). Raises on a stall, the ceiling or a dead CLI."""
        remaining = deadline - time.monotonic()
        wait = stall if stall and stall < remaining else remaining
        siblings_pending = rnd.ended_on_tools and rnd.started_calls > len(rnd.calls)
        if siblings_pending:
            wait = min(wait, _SIBLING_GRACE)
        try:
            message = await asyncio.wait_for(self.messages.get(), timeout=max(wait, 0.01))
        except asyncio.TimeoutError:
            if rnd.calls and rnd.ended_on_tools:
                return None
            if stall and stall < remaining:
                raise ProviderTimeoutError(
                    f"Claude Code CLI produced no output for {stall:.0f}s "
                    "(raise ONW_CLAUDE_CLI_STALL if this is expected).",
                    stalled=True,
                ) from None
            raise ProviderTimeoutError(
                f"Claude Code CLI timed out after {timeout:.0f}s "
                "(raise ONW_CLAUDE_CLI_TIMEOUT if this is expected)."
            ) from None
        if message is _END:
            raise ExternalServiceError(
                "Claude Code CLI output ended without a result event (the turn was truncated). "
                "Upgrade the CLI (`claude update`) if this repeats."
            )
        if isinstance(message, BaseException):
            raise message
        return message

    def _usage(self, rnd: Round, result: Optional[ResultMessage]) -> dict:
        event, self.cost_estimated = round_usage(
            rnd,
            model=self.model,
            resolved_model=self.resolved_model,
            api_key_source=self.api_key_source,
            result=result,
            estimated_so_far=self.cost_estimated,
        )
        return event

    def _translate(self, message: Any, rnd: Round):
        """One SDK message as zero or more normalized provider events."""
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                self.api_key_source = (message.data or {}).get("apiKeySource") or ""
        elif isinstance(message, StreamEvent):
            if message.parent_tool_use_id is None:
                yield from self._stream_event(message.event or {}, rnd)
        elif isinstance(message, AssistantMessage):
            if message.parent_tool_use_id is None and not message.error:
                yield from self._assistant(message, rnd)
        elif isinstance(message, ResultMessage):
            if message.is_error:
                raise result_failure(message)
            yield {"type": "_result", "result": message}

    def _stream_event(self, ev: dict, rnd: Round):
        kind = ev.get("type")
        if kind == "message_start":
            usage = (ev.get("message") or {}).get("usage") or {}
            self.resolved_model = (ev.get("message") or {}).get("model") or self.resolved_model
            rnd.requests.append(dict(usage))
            rnd.delta_seen = False
        elif kind == "content_block_start":
            if (ev.get("content_block") or {}).get("type") == "tool_use":
                rnd.started_calls += 1
        elif kind == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "thinking_delta" and delta.get("thinking"):
                yield {"type": "thinking_delta", "text": delta["thinking"]}
            elif delta.get("type") == "text_delta" and delta.get("text"):
                if rnd.ttft_ms is None:
                    rnd.ttft_ms = (time.monotonic() - rnd.t0) * 1000.0
                rnd.delta_seen = True
                yield {"type": "text_delta", "text": delta["text"]}
        elif kind == "message_delta":
            if rnd.requests:
                rnd.requests[-1]["output_tokens"] = (ev.get("usage") or {}).get(
                    "output_tokens", rnd.requests[-1].get("output_tokens", 0)
                )
            if (ev.get("delta") or {}).get("stop_reason") == "tool_use":
                rnd.ended_on_tools = True

    def _assistant(self, message: AssistantMessage, rnd: Round):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                name = block.name.removeprefix(sdk_tools.PREFIX)
                host = self.offered.get(name)
                if host is None or any(c["id"] == block.id for c in rnd.calls):
                    continue
                rnd.calls.append({"id": block.id, "name": host, "input": block.input or {}})
                if rnd.started_calls < len(rnd.calls):
                    rnd.started_calls = len(rnd.calls)
                yield {"type": "tool_use_start", "id": block.id, "name": host}
                yield {"type": "tool_use", "id": block.id, "name": host, "input": block.input or {}}
            elif getattr(block, "text", None) and not rnd.delta_seen:
                yield {"type": "text_delta", "text": block.text}


async def _stream_of(prompt: list[dict]):
    for message in prompt:
        yield message


async def _make_sure_gone(proc: Any) -> None:
    """The SDK's close already waits, terminates and kills; this is the last word for a child
    that survived it. One child only — see the module docstring on why not a process group."""
    if proc is None or getattr(proc, "returncode", 0) is not None:
        return
    with suppress(Exception):
        proc.kill()
    with suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5)


# ── the parked-query registry ─────────────────────────────────────────────────

_parked: "dict[str, LiveQuery]" = {}


def _remember(live: LiveQuery) -> None:
    for call_id in live.bound:
        _parked[call_id] = live
    turns = sorted({id(t): t for t in _parked.values()}.values(), key=lambda t: t.parked_at)
    for stale in turns[: max(0, len(turns) - _MAX_PARKED)]:
        log.info("claude_sdk: too many parked queries — closing the oldest")
        stale.close()


def _forget(live: LiveQuery) -> None:
    for call_id in [k for k, v in _parked.items() if v is live]:
        del _parked[call_id]


def parked(call_id: str) -> Optional[LiveQuery]:
    return _parked.get(call_id)


async def close_all() -> None:
    for live in list({id(t): t for t in _parked.values()}.values()):
        live.close()
