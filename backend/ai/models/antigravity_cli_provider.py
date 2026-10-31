"""Google Antigravity headless-CLI provider.

Runs the locally-installed ``agy`` CLI in headless streaming mode (``agy --input-format
stream-json``) as an AI provider, allowing a self-hoster with a Google Antigravity account to spend THAT
instead of separate API credits.

Every run speaks ``--input-format stream-json --output-format stream-json``: the prompt goes
over stdin (never argv, which a long transcript would push past the OS ARG_MAX) and the reply
streams back as NDJSON. A chat turn keeps ONE live process per conversation and sends only the
new messages each round (``antigravity_live_session``, ADR
``2026-10-05-antigravity-cli-live-sessions``); ``ONW_ANTIGRAVITY_CLI_SESSIONS=0`` spawns a
process per round instead.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, List, Optional

from backend.core.child_env import scrubbed_environ
from backend.core.errors import ConfigurationError, ExternalServiceError

from . import antigravity_live_session
from .base import AIProvider
from .cli_tool_protocol import ToolCallScanner, render_tool_manifest
from .pricing import cost_usd_openai

log = logging.getLogger(__name__)

DEFAULT_CLI_PATH = "agy"

_DEFAULT_MAX_CONCURRENCY = 2

_NO_RESULT = "Antigravity CLI output ended without a result event (the turn was truncated)."


def cost_basis_for(api_key: str) -> str:
    """Attributes cost basis: "api_billed" when an API key is present, else "plan_included"."""
    return "api_billed" if (api_key or "").strip() else "plan_included"


@dataclass(frozen=True)
class _Turn:
    """Normalized turn result from an `agy` headless execution."""

    text: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    duration_ms: float


def _text_delta(event: dict) -> str:
    if event.get("event") != "step_update":
        return ""
    delta = (event.get("step_update") or {}).get("text_delta")
    return delta if isinstance(delta, str) else ""


def _result_failure(result: Optional[dict]) -> Optional[str]:
    """Why a round's ``result`` is not a success, or None when it is one."""
    if result is None:
        return _NO_RESULT
    status = str(result.get("status") or "SUCCESS").upper()
    if status == "SUCCESS":
        return None
    detail = result.get("error") or result.get("response") or "no detail"
    if status == "ERROR":
        return f"Antigravity CLI returned error: {detail}"
    return f"Antigravity CLI turn ended with status {status}: {detail}"


def _turn_from_result(
    result: Optional[dict], streamed: str, duration_ms: float, usage: dict[str, int]
) -> _Turn:
    """``usage`` is the turn's own delta — ``result.usage`` itself is session-cumulative."""
    failure = _result_failure(result)
    if failure is not None:
        raise ExternalServiceError(failure)
    return _Turn(
        text=str((result or {}).get("response") or streamed),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cached_tokens=usage.get("cache_read_tokens", 0),
        duration_ms=duration_ms,
    )


@dataclass
class _Round:
    """What one model round has shown and received so far."""

    reused: bool = False
    scanner: ToolCallScanner = field(default_factory=ToolCallScanner)
    t0: float = field(default_factory=time.monotonic)
    ttft_ms: Optional[float] = None
    emitted: bool = False
    streamed: str = ""
    result: Optional[dict] = None
    usage: dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and _result_failure(self.result) is None

    def _show(self, deltas: list[str]) -> list[str]:
        visible = [d for d in deltas if d]
        if visible:
            if self.ttft_ms is None:
                self.ttft_ms = (time.monotonic() - self.t0) * 1000.0
            self.emitted = True
        return visible

    def consume(self, event: dict) -> list[str]:
        """Fold one stream event in; return the user-visible text it produced."""
        delta = _text_delta(event)
        if delta:
            self.streamed += delta
            return self._show(self.scanner.feed(delta))
        if event.get("event") == "result":
            self.result = event.get("result") or {}
        return []

    def finish(self) -> list[str]:
        """End of round: the response stands in for a stream that carried no deltas."""
        out: list[str] = []
        if self.ok and not self.streamed and self.result is not None:
            self.streamed = str(self.result.get("response") or "")
            out.extend(self.scanner.feed(self.streamed))
        out.extend(self.scanner.flush())
        return self._show(out)


def _usage_event(
    turn: _Turn, model: str, basis: str, cost: float, ttft_ms: Optional[float], duration_ms: float
) -> dict:
    return {
        "cost_basis": basis,
        "type": "usage",
        "input_tokens": turn.input_tokens,
        "cache_creation_tokens": 0,
        "cache_creation_5m_tokens": 0,
        "cache_creation_1h_tokens": 0,
        "cache_read_tokens": turn.cached_tokens,
        "output_tokens": turn.output_tokens,
        "cost_usd": cost,
        "model": model,
        "ttft_ms": ttft_ms,
        "duration_ms": duration_ms,
    }


# The Auto-approval judge (``ai/auto_judge.py``, ``permissions_v2``) reads untrusted arguments and
# must run with NO tools. This CLI is started with ``--dangerously-skip-permissions`` and offers no
# switch that turns its own tools off, so a judge here could be steered into running one. It is
# therefore unavailable on this transport: the judge treats any error as "ask", and the call
# becomes an ordinary confirm card.
_JUDGE_SOURCES = frozenset({"auto_approval"})
_NO_JUDGE = (
    "The Auto-approval judge cannot run on the Antigravity CLI: it cannot be limited to no tools. "
    "Pick another model for the judge in Settings -> AI; until then a call that needs a decision asks you."
)


class AntigravityCliProvider(AIProvider):
    """AIProvider implementation backed by the local `agy` CLI binary."""

    def __init__(self, cli_path: str = "", api_key: str = "") -> None:
        self.cli_path = (cli_path or "").strip()
        self.api_key = (api_key or "").strip()
        self._semaphore = asyncio.Semaphore(_DEFAULT_MAX_CONCURRENCY)

    def _resolve_binary(self) -> str:
        from backend.db.model_config_store import cli_binary_path

        resolved = cli_binary_path("antigravity_cli", self.cli_path)
        if resolved:
            return resolved
        lookup = shutil.which(self.cli_path or DEFAULT_CLI_PATH)
        if lookup:
            return lookup
        raise ConfigurationError(
            f"Antigravity CLI ({self.cli_path or DEFAULT_CLI_PATH}) not found on PATH. "
            "Install it from https://antigravity.google or configure its path in Settings → AI Models."
        )

    def _argv(self, model: str, effort: str) -> list[str]:
        # The prompt is NEVER an argv element: stream-json input reads it from stdin.
        cmd = [
            self._resolve_binary(),
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
        ]
        if model.strip():
            cmd.extend(["--model", model.strip()])
        if effort.strip().lower() in ("low", "medium", "high"):
            cmd.extend(["--effort", effort.strip().lower()])
        return cmd

    async def _run_subprocess(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        effort: str = "",
    ) -> _Turn:
        argv = self._argv(model, effort)
        t0 = time.monotonic()
        streamed = ""
        result: Optional[dict] = None
        usage: dict[str, int] = {}
        async with self._semaphore:
            session = await antigravity_live_session.spawn(argv, scrubbed_environ())
            try:
                await session.send(antigravity_live_session.with_system(system, prompt))
                async for ev in session.events():
                    streamed += _text_delta(ev)
                    if session.is_final(ev):
                        result = ev.get("result") or {}
                        usage = session.usage_delta(result.get("usage") or {})
            finally:
                await session.close()
        return _turn_from_result(result, streamed, (time.monotonic() - t0) * 1000.0, usage)

    def _record(self, source: str, model: str, turn: _Turn) -> "tuple[float, str]":
        cost = cost_usd_openai(model, turn.input_tokens, turn.output_tokens, turn.cached_tokens)
        basis = cost_basis_for(self.api_key)
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
            provider=provider_label_for("antigravity_cli", ""),
        )
        return cost, basis

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,  # noqa: ARG002
        source: str = "complete",
        images: list[dict] | None = None,
        response_format: dict | None = None,  # noqa: ARG002
        effort: str = "",
    ) -> str:
        if source in _JUDGE_SOURCES:
            raise ConfigurationError(_NO_JUDGE)
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]

        turn = await self._run_subprocess(prompt, system=system, model=model, effort=effort)
        cost, basis = self._record(source, model, turn)
        log.info(
            "complete_done  source=%s  transport=antigravity_cli  model=%s  basis=%s  input=%d  "
            "cached=%d  output=%d  cost_usd=%.5f",
            source,
            model,
            basis,
            turn.input_tokens,
            turn.cached_tokens,
            turn.output_tokens,
            cost,
        )
        return turn.text

    async def stream_complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,  # noqa: ARG002
        source: str = "complete",
        effort: str = "",
    ) -> AsyncGenerator[dict, None]:
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]

        t0 = time.monotonic()
        turn = await self._run_subprocess(prompt, system=system, model=model, effort=effort)
        cost, basis = self._record(source, model, turn)
        log.info(
            "stream_complete_done  source=%s  transport=antigravity_cli  model=%s  basis=%s  "
            "input=%d  cached=%d  output=%d  cost_usd=%.5f",
            source,
            model,
            basis,
            turn.input_tokens,
            turn.cached_tokens,
            turn.output_tokens,
            cost,
        )

        yield {"type": "chunk", "text": turn.text}
        yield _usage_event(turn, model, basis, cost, None, (time.monotonic() - t0) * 1000.0)
        yield {"type": "done"}

    async def _stream_round(
        self,
        rnd: _Round,
        argv: list[str],
        key_argv: list[str],
        messages: List[dict],
        system: str,
        pooled: bool,
        fresh: bool,
    ) -> AsyncGenerator[str, None]:
        """One model round on a live (pooled) or one-shot process; yields visible text."""
        env = dict(os.environ)
        async with self._semaphore:
            if pooled:
                session, text, _images, rnd.reused = await antigravity_live_session.acquire(
                    argv, env, messages, key_argv, system, fresh=fresh
                )
            else:
                session = await antigravity_live_session.spawn(argv, env)
                text, _images = antigravity_live_session.first_round(system)(messages)
            try:
                await session.send(text)
                async for ev in session.events():
                    for visible in rnd.consume(ev):
                        yield visible
                if rnd.result is not None:
                    rnd.usage = session.usage_delta(rnd.result.get("usage") or {})
            except ExternalServiceError as err:
                if session.timed_out:
                    raise
                rnd.error = str(err)
            finally:
                if pooled:
                    # No result, or a non-SUCCESS one, retires the session — its state is
                    # unknown, and reusing it could answer from a poisoned history.
                    await antigravity_live_session.release(key_argv, session, messages, rnd.ok)
                else:
                    await session.close()

    async def stream_chat(
        self,
        messages: List[dict],
        tools: List[dict],
        system: str,
        model: str,
        effort: str = "medium",
    ) -> AsyncGenerator[dict, None]:
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]

        effective_system = (system or "") + render_tool_manifest(tools or [])
        argv = self._argv(model, effort)
        # `agy` has no system-prompt flag — the instructions ride in a session's first
        # message — so the session key must carry them, or a changed prompt or tool
        # manifest would be answered by a session that was given the old one.
        key_argv = argv + [antigravity_live_session.system_key(effective_system)]
        pooled = antigravity_live_session.sessions_enabled()

        for attempt in (1, 2):
            rnd = _Round()
            async for visible in self._stream_round(
                rnd, argv, key_argv, messages, effective_system, pooled, attempt > 1
            ):
                yield {"type": "text_delta", "text": visible}

            error = rnd.error or _result_failure(rnd.result)
            if error is None:
                # A failed round is not flushed: a half-held `<tool_call>` fragment must not
                # reach the user, nor count as "something was shown" and block the retry.
                for visible in rnd.finish():
                    yield {"type": "text_delta", "text": visible}
                break
            # A REUSED session can die between rounds (agy's own --print-timeout, a crash);
            # it is already retired, so retry once on a NEW process (never another idle
            # pooled session) with the full transcript — but only while nothing has been shown, or the retry would
            # duplicate it.
            if rnd.reused and not rnd.emitted and attempt == 1:
                log.warning("antigravity_cli: reused session failed (%.120s) — retrying", error)
                continue
            raise ExternalServiceError(error)

        turn = _turn_from_result(rnd.result, rnd.streamed, 0.0, rnd.usage)
        cost, basis = self._record("chat", model, turn)

        for i, call in enumerate(rnd.scanner.calls):
            yield {
                "type": "tool_use",
                "id": call.get("id") or f"cli_{int(rnd.t0 * 1000)}_{i}",
                "name": call["name"],
                "input": call["input"],
            }

        duration_ms = (time.monotonic() - rnd.t0) * 1000.0
        yield _usage_event(turn, model, basis, cost, rnd.ttft_ms, duration_ms)
        yield {"type": "done", "stop_reason": "tool_use" if rnd.scanner.calls else "end_turn"}
