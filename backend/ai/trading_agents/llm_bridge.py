"""The LLM bridge: a per-run, loopback, OpenAI-compatible server in front of ONW's own providers.

TradingAgents never holds a model key and never calls a vendor: its ``openai_compatible`` client
is pointed here, and every call goes through the owner's own ``AIProvider`` exactly as Lucius's
does — one cost path, one provider config, one place to turn a provider off. The worker sees only
two aliases (``onw-deep`` / ``onw-quick``); anything else is refused, so it cannot pick a model
the owner did not approve.

One request is one provider call and one fresh CLI turn (``one_shot_turn``): the bridge hands the
model's tool calls straight back to TradingAgents, which runs its own tools and re-sends its
transcript — never the Lucius "turn stays open" path. It runs NO tool.

Caps are enforced HERE, before a call is made: a metered call reserves its worst-case cost first
(``BridgeModel.worst_case_usd``) and waits while the reservations in flight would pass the limit,
so concurrent calls cannot overshoot it. The reservation is the provider's real output ceiling,
never the length the worker asks for, and a call whose worst case alone is more than the run has
left is refused even when nothing else is outstanding. A call runs as its own task, shielded from the request: it is
finished and recorded (one cost row, one ``CallRecord``) even when the worker was killed under it,
and ``stop()`` waits for the calls still going.

Message bodies carry portfolio amounts, so nothing here logs one: a call logs its id, alias,
tokens, latency and status only. See docs/specs/trading-agents.md §4.2.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import socket
import time
from typing import Callable, Mapping, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from backend.ai.trading_agents import openai_shape as shape
from backend.ai.trading_agents.bridge_http import (
    QuietServer,
    error_response,
    is_loopback,
    read_json,
    record_cost,
    run_provider,
    server_config,
)
from backend.ai.trading_agents.bridge_models import (
    ALIAS_DEEP,
    ALIAS_QUICK,
    ALIASES,
    KIND_BUDGET,
    KIND_CALLS,
    PLAN_INCLUDED,
    BridgeModel,
    CallRecord,
    RunLimits,
    resolve_bridge_models,
)
from backend.ai.trading_agents.openai_errors import classify_error
from backend.core.errors import BudgetExceededError, ExternalServiceError, OpenNetWorthError

log = logging.getLogger(__name__)

__all__ = [
    "ALIASES",
    "ALIAS_DEEP",
    "ALIAS_QUICK",
    "PLAN_INCLUDED",
    "BridgeModel",
    "CallRecord",
    "LlmBridge",
    "RunLimits",
    "resolve_bridge_models",
]

_START_TIMEOUT_SECONDS = 5.0
_STOP_TIMEOUT_SECONDS = 5.0
_INFLIGHT_WAIT_SECONDS = 60.0
# After the grace a cancelled call gets this long to unwind, then it is abandoned: a provider or
# CLI child that ignores a cancel must not hold the run open.
_CANCEL_WAIT_SECONDS = 3.0
_CALL_TIMEOUT_SECONDS = 10 * 60.0
STOP_BOUND_SECONDS = _INFLIGHT_WAIT_SECONDS + _CANCEL_WAIT_SECONDS + _STOP_TIMEOUT_SECONDS


class LlmBridge:
    """The run's bridge. ``async with LlmBridge(...) as bridge`` → ``bridge.base_url`` /
    ``bridge.token`` for the worker; ``on_call`` gets one ``CallRecord`` per finished call."""

    def __init__(
        self,
        *,
        run_id: str,
        models: Mapping[str, BridgeModel],
        concurrency: int,
        on_call: Optional[Callable[[CallRecord], None]] = None,
        limits: RunLimits = RunLimits(),
    ) -> None:
        self.run_id = run_id
        self.token = secrets.token_urlsafe(32)
        self._models = dict(models)
        self._on_call = on_call
        self._limits = limits
        self._slots = asyncio.Semaphore(max(1, concurrency))
        self._admitted = 0
        self._refused = 0
        self._metered_usd = 0.0
        self._reserved_usd = 0.0
        self._released = asyncio.Event()
        self._inflight: set[asyncio.Task] = set()
        self._refusal: Optional[tuple[str, str]] = None
        self.refusal_kind: Optional[str] = None
        self.refusal_text: Optional[str] = None
        self._server: Optional[QuietServer] = None
        self._task: Optional[asyncio.Task] = None
        self._sock: Optional[socket.socket] = None
        self._port = 0
        self.app = Starlette(
            routes=[
                Route("/v1/chat/completions", self._chat, methods=["POST"]),
                Route("/v1/models", self._list_models, methods=["GET"]),
            ]
        )

    # ── lifecycle ────────────────────────────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1"

    @property
    def metered_usd(self) -> float:
        return self._metered_usd

    @property
    def calls_admitted(self) -> int:
        return self._admitted

    @property
    def refused_calls(self) -> int:
        """Calls turned away for a cap — the runner's proof that a failed worker hit one."""
        return self._refused

    def refuse(self, reason: str, kind: str = KIND_BUDGET) -> None:
        """Turn every later call away (a cap the runner tracks tripped)."""
        self._refusal = (kind, reason)
        self._released.set()

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
        except OSError as exc:
            sock.close()
            raise ExternalServiceError("The TradingAgents model bridge could not start.") from exc
        self._sock = sock
        self._port = sock.getsockname()[1]
        self._server = QuietServer(server_config(self.app))
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while not self._server.started:
            if self._task.done() or time.monotonic() > deadline:
                await self.stop()
                raise ExternalServiceError("The TradingAgents model bridge could not start.")
            await asyncio.sleep(0.01)
        log.info("bridge_started run=%s port=%d", self.run_id, self._port)

    async def stop(self) -> None:
        """Wait (bounded) for the calls still going so each is recorded, then shut the server
        and release the socket."""
        pending = set(self._inflight)
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=_INFLIGHT_WAIT_SECONDS)
            for task in unfinished:
                task.cancel()
            if unfinished:
                _, abandoned = await asyncio.wait(unfinished, timeout=_CANCEL_WAIT_SECONDS)
                if abandoned:
                    log.warning(
                        "bridge_calls_abandoned run=%s count=%d", self.run_id, len(abandoned)
                    )
                    self._inflight.clear()
        if self._server is not None:
            self._server.should_exit = True
        task, self._task = self._task, None
        if task is not None:
            try:
                await asyncio.wait_for(task, _STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                log.warning("bridge_stop_timeout run=%s", self.run_id)
            except Exception:  # noqa: BLE001 — a server that died on its own is already stopped
                log.exception("bridge_server_failed run=%s", self.run_id)
        self._server = None
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()

    async def __aenter__(self) -> "LlmBridge":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    # ── request gates ────────────────────────────────────────────────────────────────────

    def _guard(self, request: Request) -> Optional[Response]:
        """403 for a peer that is not on this machine, 401 for a wrong or missing token."""
        if not is_loopback(request.client.host if request.client else ""):
            return error_response(403, "Forbidden.", "permission_error", "forbidden")
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        known = self.token.encode()
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), known):
            return error_response(
                401, "Invalid API key.", "authentication_error", "invalid_api_key"
            )
        return None

    def _stop_reason(self, metered: bool, reserve: float = 0.0) -> Optional[tuple[str, str]]:
        """``(kind, sentence)`` when no more calls may be admitted, else ``None``. With nothing
        else outstanding, a call whose own worst case is more than the dollars left is turned away
        too: it could not be held to the limit."""
        if self._refusal is not None:
            return self._refusal
        limits = self._limits
        if limits.max_calls is not None and self._admitted >= limits.max_calls:
            return KIND_CALLS, f"The run reached its limit of {limits.max_calls} model calls."
        limit = limits.usd_limit() if metered else None
        if limit is not None:
            left = limit[0] - self._metered_usd
            if left <= 0 or (self._reserved_usd == 0 and reserve > left):
                return KIND_BUDGET, limit[1]
        return None

    def _fits(self, reserve: float, metered: bool) -> bool:
        limit = self._limits.usd_limit() if metered else None
        if limit is None or reserve <= 0 or self._reserved_usd == 0:
            return True
        return reserve <= limit[0] - self._metered_usd - self._reserved_usd

    async def _admit(self, reserve: float, metered: bool) -> None:
        """Count a call in and reserve its worst-case cost, waiting while the reservations
        already in flight leave no room; ``BudgetExceededError`` when the run is out of budget."""
        while True:
            stop = self._stop_reason(metered, reserve)
            if stop is not None:
                self._refused += 1
                if self.refusal_kind is None:
                    self.refusal_kind, self.refusal_text = stop
                raise BudgetExceededError(stop[1])
            if self._fits(reserve, metered):
                break
            self._released.clear()
            await self._released.wait()
        self._admitted += 1
        self._reserved_usd += reserve

    def _release(self, reserve: float) -> None:
        self._reserved_usd = max(0.0, self._reserved_usd - reserve)
        self._released.set()

    # ── routes ───────────────────────────────────────────────────────────────────────────

    async def _list_models(self, request: Request) -> Response:
        denied = self._guard(request)
        return denied or JSONResponse(shape.models_list(list(self._models)))

    async def _chat(self, request: Request) -> Response:
        denied = self._guard(request)
        if denied is not None:
            return denied
        try:
            body, size = await read_json(request)
            model = self._models.get(str(body.get("model")))
            if model is None:
                return error_response(
                    400,
                    f"The model must be one of {', '.join(ALIASES)}.",
                    "invalid_request_error",
                    "model_not_found",
                )
            onw_request = shape.to_onw_request(body)
            reserve = model.worst_case_usd(size)
            await self._admit(reserve, model.metered)
        except OpenNetWorthError as exc:
            status, payload = classify_error(exc)
            return JSONResponse(payload, status_code=status)
        return await self._serve_call(model, onw_request, reserve)

    async def _serve_call(
        self, model: BridgeModel, onw_request: shape.OnwRequest, reserve: float
    ) -> Response:
        call_id = shape.new_call_id()
        started = time.monotonic()
        task = asyncio.create_task(self._call(model, call_id, onw_request, reserve, started))
        self._inflight.add(task)
        task.add_done_callback(self._call_done)
        try:
            reply, record = await asyncio.shield(task)
            if not (reply.text or reply.tool_uses):
                raise ExternalServiceError("The model returned an empty reply.")
        except Exception as exc:  # noqa: BLE001 — every provider failure maps to an HTTP error
            status, payload = classify_error(exc)
            log.warning(
                "bridge_call_failed run=%s call=%s alias=%s status=%d error=%s latency_ms=%d",
                self.run_id,
                call_id,
                model.alias,
                status,
                type(exc).__name__,
                (time.monotonic() - started) * 1000.0,
            )
            return JSONResponse(payload, status_code=status)
        log.info(
            "bridge_call run=%s call=%s alias=%s in=%d out=%d tool_calls=%d latency_ms=%d status=200",
            self.run_id,
            call_id,
            model.alias,
            record.input_tokens,
            record.output_tokens,
            record.tool_calls,
            record.latency_ms,
        )
        completion = shape.chat_completion(
            reply, onw_request, alias=model.alias, call_id=call_id, created=int(time.time())
        )
        return JSONResponse(completion)

    def _call_done(self, task: asyncio.Task) -> None:
        self._inflight.discard(task)
        if not task.cancelled():
            task.exception()  # read it, so a call nobody awaits any more is not logged as lost

    async def _call(
        self,
        model: BridgeModel,
        call_id: str,
        onw_request: shape.OnwRequest,
        reserve: float,
        started: float,
    ) -> tuple[shape.Reply, CallRecord]:
        """One admitted call, to its end and recorded — whether or not anyone still waits for it.
        A call that fails is a call: recorded with no tokens, so ``llm_calls`` and the call cap
        count the same things."""
        try:
            async with self._slots:
                reply = await _bounded(run_provider(self.run_id, model, onw_request))
            return reply, self._finish(model, call_id, reply, started)
        except BaseException:
            self._notify(self._failed_record(model, call_id, started))
            raise
        finally:
            self._release(reserve)

    def _failed_record(self, model: BridgeModel, call_id: str, started: float) -> CallRecord:
        return CallRecord(
            call_id=call_id,
            alias=model.alias,
            model_ref=model.ref,
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost_usd=None,
            cost_basis=None,
            latency_ms=(time.monotonic() - started) * 1000.0,
            tool_calls=0,
            failed=True,
        )

    def _finish(
        self, model: BridgeModel, call_id: str, reply: shape.Reply, started: float
    ) -> CallRecord:
        """Record the call's cost (once) and report it to the runner."""
        usage = reply.usage
        if not getattr(model.provider, "RECORDS_COST_IN_STREAM_CHAT", False):
            record_cost(self.run_id, model, usage)
        record = CallRecord(
            call_id=call_id,
            alias=model.alias,
            model_ref=model.ref,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cost_usd=usage.cost_usd,
            cost_basis=usage.cost_basis,
            latency_ms=(time.monotonic() - started) * 1000.0,
            tool_calls=len(reply.tool_uses),
            unpriced=model.is_unpriced(usage),
        )
        self._notify(record)
        return record

    def _notify(self, record: CallRecord) -> None:
        self._metered_usd += record.metered_usd
        if self._on_call is not None:
            try:
                self._on_call(record)
            except Exception:  # noqa: BLE001 — the runner's bookkeeping never fails a call
                log.exception("bridge_on_call_failed run=%s call=%s", self.run_id, record.call_id)


def _consume(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()  # read it, so an abandoned call is not logged as lost


async def _bounded(call) -> shape.Reply:
    """``call``'s result within ``_CALL_TIMEOUT_SECONDS``. A call past it, or one whose caller was
    cancelled, is cancelled and ABANDONED rather than awaited, so a provider that ignores a cancel
    cannot keep this coroutine (and the run) waiting."""
    inner = asyncio.ensure_future(call)
    try:
        done, _ = await asyncio.wait({inner}, timeout=_CALL_TIMEOUT_SECONDS)
    except BaseException:
        inner.cancel()
        inner.add_done_callback(_consume)
        raise
    if not done:
        inner.cancel()
        inner.add_done_callback(_consume)
        raise ExternalServiceError("The model did not answer in time.")
    return inner.result()
