"""The LLM bridge's plumbing: the loopback server, request reading and the one provider turn."""

from __future__ import annotations

import contextlib
import ipaddress
import json
from typing import Iterator

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.ai.trading_agents import openai_shape as shape
from backend.ai.trading_agents.bridge_models import SOURCE, BridgeModel
from backend.ai.trading_agents.openai_errors import error_body
from backend.core.config import SERVER_EVENT_LOOP
from backend.core.cost_context import cost_attribution
from backend.core.errors import ValidationError
from backend.core.one_shot_turn import one_shot_turn

# TradingAgents does not send an effort; both tiers run at the owner's usual middle setting.
EFFORT = "medium"
MAX_BODY_BYTES = 2 * 1024 * 1024


class QuietServer(uvicorn.Server):
    """A uvicorn server that leaves the process's signal handlers to the main app."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def server_config(app) -> uvicorn.Config:
    """Loopback server config: no log of its own, no lifespan, and no trust in ``X-Forwarded-For``
    (the peer address is what the loopback guard judges)."""
    return uvicorn.Config(
        app,
        log_config=None,
        access_log=False,
        lifespan="off",
        loop=SERVER_EVENT_LOOP,
        proxy_headers=False,
        timeout_graceful_shutdown=2,
    )


def is_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def error_response(status: int, message: str, kind: str, code: str) -> JSONResponse:
    return JSONResponse(error_body(message, kind=kind, code=code), status_code=status)


async def read_json(request: Request) -> tuple[dict, int]:
    """The JSON object a request carries and its size in bytes. A body over ``MAX_BODY_BYTES`` is
    refused as soon as it passes the limit, never read to its end."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ValidationError("The request is too large.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ValidationError("The request is too large.")
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ValidationError("The request body must be a JSON object.")
    return body, size


async def run_provider(
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
            effort=EFFORT,
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


def record_cost(run_id: str, model: BridgeModel, usage: shape.Usage) -> None:
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
