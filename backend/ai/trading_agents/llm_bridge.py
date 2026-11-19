"""The LLM bridge: a per-run, loopback, OpenAI-compatible server in front of ONW's own providers.

TradingAgents never holds a model key and never calls a vendor: its ``openai_compatible`` client
is pointed here, and every call goes through the owner's own ``AIProvider`` exactly as Lucius's
does — one cost path, one provider config, one place to turn a provider off. The worker sees only
two aliases (``onw-deep`` / ``onw-quick``); anything else is refused, so it cannot pick a model
the owner did not approve.

One request is one provider call and one fresh CLI turn (``one_shot_turn``): the bridge hands the
model's tool calls straight back to TradingAgents, which runs its own data tools and re-sends its
transcript — never the Lucius "turn stays open" path. It runs NO tool.

Message bodies carry portfolio amounts, so nothing here logs one: a call logs its id, alias,
tokens, latency and status only. See docs/specs/trading-agents.md §4.2.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from backend.ai.models.base import AIProvider
from backend.ai.trading_agents import openai_shape as shape
from backend.ai.trading_agents.openai_errors import classify_error, error_body
from backend.core.cost_context import cost_attribution
from backend.core.errors import (
    BudgetExceededError,
    ConfigurationError,
    ExternalServiceError,
    ModelUnavailableError,
    OpenNetWorthError,
    ValidationError,
)
from backend.core.one_shot_turn import one_shot_turn
from backend.services.trading_agents.settings import ResolvedModels

log = logging.getLogger(__name__)

SOURCE = "trading_agents"
ALIAS_DEEP = "onw-deep"
ALIAS_QUICK = "onw-quick"
ALIASES = (ALIAS_DEEP, ALIAS_QUICK)
PLAN_INCLUDED = "plan_included"
# TradingAgents does not send an effort; both tiers run at the owner's usual middle setting.
_EFFORT = "medium"
_MAX_BODY_BYTES = 16 * 1024 * 1024
_START_TIMEOUT_SECONDS = 5.0
_STOP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class BridgeModel:
    """One alias's resolved model: the provider object and how its calls are labelled."""

    alias: str
    ref: str
    model: str
    provider_label: str
    transport: str
    provider: AIProvider


@dataclass(frozen=True)
class RunLimits:
    """Hard stops the bridge enforces on its own, so a cap cannot be overshot by the calls that
    were already in flight when the runner noticed it. ``None`` = no limit."""

    max_calls: Optional[int] = None
    max_metered_usd: Optional[float] = None


@dataclass(frozen=True)
class CallRecord:
    """What one finished call cost, handed to the runner in-process (never via the worker)."""

    call_id: str
    alias: str
    model_ref: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: Optional[float]
    cost_basis: Optional[str]
    latency_ms: float
    tool_calls: int

    @property
    def metered_usd(self) -> float:
        """Spend that is real money: a plan-included call is quota use, not an invoice."""
        return 0.0 if self.cost_basis == PLAN_INCLUDED else float(self.cost_usd or 0.0)


def resolve_bridge_models(models: ResolvedModels) -> dict[str, BridgeModel]:
    """The two aliases' providers, built once for the run (a config change mid-run is not
    picked up). A provider the owner turned off raises ``ModelUnavailableError`` — never a
    reroute to the instance default."""
    from backend.ai.assistant import resolve_model_context
    from backend.db.model_config_store import provider_label_for

    out: dict[str, BridgeModel] = {}
    for alias, ref in ((ALIAS_DEEP, models.deep), (ALIAS_QUICK, models.quick)):
        try:
            context = resolve_model_context(ref)
        except ConfigurationError as exc:
            raise ModelUnavailableError(exc.message or str(exc), model_ref=ref) from exc
        if context.provider_key != ref.partition(":")[0]:
            raise ModelUnavailableError(f"{ref!r} is not a usable AI model.", model_ref=ref)
        label = provider_label_for(context.transport, context.base_url, context.provider_key)
        out[alias] = BridgeModel(
            alias, ref, context.model, label, context.transport, context.provider
        )
    return out


class _QuietServer(uvicorn.Server):
    """A uvicorn server that leaves the process's signal handlers to the main app."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


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
        self._refusal: Optional[str] = None
        self._server: Optional[_QuietServer] = None
        self._task: Optional[asyncio.Task] = None
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

    def refuse(self, reason: str) -> None:
        """Turn every later call away (a cap the runner tracks tripped)."""
        self._refusal = reason

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
        except OSError as exc:
            sock.close()
            raise ExternalServiceError("The TradingAgents model bridge could not start.") from exc
        self._port = sock.getsockname()[1]
        config = uvicorn.Config(
            self.app,
            log_config=None,
            access_log=False,
            lifespan="off",
            proxy_headers=False,
            timeout_graceful_shutdown=2,
        )
        self._server = _QuietServer(config)
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while not self._server.started:
            if self._task.done() or time.monotonic() > deadline:
                await self.stop()
                raise ExternalServiceError("The TradingAgents model bridge could not start.")
            await asyncio.sleep(0.01)
        log.info("bridge_started run=%s port=%d", self.run_id, self._port)

    async def stop(self) -> None:
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

    async def __aenter__(self) -> "LlmBridge":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    # ── request gates ────────────────────────────────────────────────────────────────────

    def _guard(self, request: Request) -> Optional[Response]:
        """403 for a peer that is not on this machine, 401 for a wrong or missing token."""
        if not _is_loopback(request.client.host if request.client else ""):
            return _error(403, "Forbidden.", "permission_error", "forbidden")
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        known = self.token.encode()
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), known):
            return _error(401, "Invalid API key.", "authentication_error", "invalid_api_key")
        return None

    def _admit(self) -> None:
        """Count a call in, or raise ``BudgetExceededError`` when the run is out of budget."""
        limits = self._limits
        reason = self._refusal
        if reason is None and limits.max_calls is not None and self._admitted >= limits.max_calls:
            reason = f"The run reached its limit of {limits.max_calls} model calls."
        if (
            reason is None
            and limits.max_metered_usd is not None
            and self._metered_usd >= limits.max_metered_usd
        ):
            reason = f"The run reached its spend cap of ${limits.max_metered_usd:.2f}."
        if reason is not None:
            self._refused += 1
            raise BudgetExceededError(reason)
        self._admitted += 1

    # ── routes ───────────────────────────────────────────────────────────────────────────

    async def _list_models(self, request: Request) -> Response:
        denied = self._guard(request)
        return denied or JSONResponse(shape.models_list(list(self._models)))

    async def _chat(self, request: Request) -> Response:
        denied = self._guard(request)
        if denied is not None:
            return denied
        try:
            body = await _read_json(request)
            model = self._models.get(str(body.get("model")))
            if model is None:
                return _error(
                    400,
                    f"The model must be one of {', '.join(ALIASES)}.",
                    "invalid_request_error",
                    "model_not_found",
                )
            onw_request = shape.to_onw_request(body)
            self._admit()
        except OpenNetWorthError as exc:
            status, payload = classify_error(exc)
            return JSONResponse(payload, status_code=status)
        return await self._serve_call(model, onw_request)

    async def _serve_call(self, model: BridgeModel, onw_request: shape.OnwRequest) -> Response:
        call_id = shape.new_call_id()
        started = time.monotonic()
        try:
            async with self._slots:
                reply = await _run_provider(self.run_id, model, onw_request)
            record = self._finish(model, call_id, reply, started)
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

    def _finish(
        self, model: BridgeModel, call_id: str, reply: shape.Reply, started: float
    ) -> CallRecord:
        """Record the call's cost (once) and report it to the runner."""
        usage = reply.usage
        if not getattr(model.provider, "RECORDS_COST_IN_STREAM_CHAT", False):
            _record_cost(self.run_id, model, usage)
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
        )
        self._metered_usd += record.metered_usd
        if self._on_call is not None:
            try:
                self._on_call(record)
            except Exception:  # noqa: BLE001 — the runner's bookkeeping never fails a call
                log.exception("bridge_on_call_failed run=%s call=%s", self.run_id, call_id)
        return record


# ── helpers ──────────────────────────────────────────────────────────────────────────────


def _is_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _error(status: int, message: str, kind: str, code: str) -> JSONResponse:
    return JSONResponse(error_body(message, kind=kind, code=code), status_code=status)


async def _read_json(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise ValidationError("The request is too large.")
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ValidationError("The request body must be a JSON object.")
    return body


async def _run_provider(
    run_id: str, model: BridgeModel, onw_request: shape.OnwRequest
) -> shape.Reply:
    """One fresh provider turn, read to its end and closed. The model's tool calls are only
    collected: TradingAgents runs its own tools, so none is ever executed here.

    The stream is read to ``done`` rather than cut at the first ``tool_use`` because the
    Claude Code and Antigravity transports report the round's usage AFTER its calls."""
    builder = shape.ReplyBuilder()
    with one_shot_turn(), cost_attribution(SOURCE, SOURCE, run_id):
        stream = model.provider.stream_chat(
            messages=onw_request.messages,
            tools=onw_request.tools,
            system=onw_request.system,
            model=model.model,
            effort=_EFFORT,
        )
        try:
            async for event in stream:
                builder.add(event)
                if builder.reply.done:
                    break
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
    return builder.reply


def _record_cost(run_id: str, model: BridgeModel, usage: shape.Usage) -> None:
    """The ONE cost row of a call — the same fields ``assistant._record_cost`` writes, labelled
    for the run. A CLI subscription call is stamped by the transport's own ``cost_basis``."""
    from backend.ai.cost_tracker import record

    record(
        SOURCE,
        model.model,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_creation_tokens,
        usage.cache_read_tokens,
        run_id,
        cost_usd=usage.cost_usd,
        surface=SOURCE,
        cost_basis=usage.cost_basis,
        provider=model.provider_label,
    )
