"""Provider-independent trading tools. Run identity is supplied by the host."""

from __future__ import annotations

import json

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import schema_of
from backend.core.errors import ValidationError
from backend.core.trading_runtime import trading_context
from backend.models.trading import OrderIntent
from backend.services import trading


def _run_id() -> str:
    context = trading_context()
    if context is None:
        raise ValidationError("Trading tools require a server-owned trading run")
    return context.run_id


def _result(value):
    return json.dumps(value, default=str, ensure_ascii=False), None


_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
_PAGE = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "offset": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}


@tool(
    "get_trading_environment",
    description="Read this run's clock, mandate and trading capabilities.",
    input_schema=_EMPTY,
    mutates=(),
)
async def get_trading_environment(input_data: dict):
    return _result(trading.get_environment(_run_id()))


@tool(
    "get_trading_account",
    description="Read this run's accounts, cash and positions.",
    input_schema=_EMPTY,
    mutates=(),
)
async def get_trading_account(input_data: dict):
    return _result(trading.get_account(_run_id()))


@tool(
    "get_trading_market_data",
    description="Read the latest observations available at this run's current time, with bounded history pagination.",
    input_schema=_PAGE,
    mutates=(),
)
async def get_trading_market_data(input_data: dict):
    return _result(
        trading.get_market_data(
            _run_id(),
            limit=input_data.get("limit", 100),
            offset=input_data.get("offset", 0),
            latest=True,
        )
    )


@tool(
    "submit_trading_order",
    description="Submit an order intent to this run's execution environment; acknowledgement is not a fill.",
    input_schema=schema_of(OrderIntent, tool_name="submit_trading_order"),
    mutates=("trading",),
)
async def submit_trading_order(input_data: dict):
    return _result(await trading.submit_order(_run_id(), input_data))


@tool(
    "cancel_trading_order",
    description="Request cancellation of an order owned by this run.",
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    },
    mutates=("trading",),
)
async def cancel_trading_order(input_data: dict):
    return _result(await trading.cancel_order(_run_id(), input_data["order_id"]))


@tool(
    "list_trading_orders",
    description="Read this run's orders and their known execution states.",
    input_schema=_PAGE,
    mutates=(),
)
async def list_trading_orders(input_data: dict):
    return _result(
        trading.list_orders(
            _run_id(), limit=input_data.get("limit", 100), offset=input_data.get("offset", 0)
        )
    )


@tool(
    "list_trading_fills",
    description="Read actual or simulated fills recorded for this run.",
    input_schema=_PAGE,
    mutates=(),
)
async def list_trading_fills(input_data: dict):
    return _result(
        trading.list_fills(
            _run_id(), limit=input_data.get("limit", 100), offset=input_data.get("offset", 0)
        )
    )


@tool(
    "record_trading_decision",
    description="Record a concise decision summary; model cost is measured by the runtime, not this tool.",
    input_schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
            "wake_after_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 86400,
                "description": "Optional delay until the next decision, measured by this environment's clock.",
            },
        },
        "required": ["summary"],
        "additionalProperties": False,
    },
    mutates=("trading",),
)
async def record_trading_decision(input_data: dict):
    return _result(trading.record_decision(_run_id(), input_data))


@tool(
    "run_trading_strategy",
    description="Execute your Python strategy in an isolated container using the current run snapshot; emit a JSON orders list and summary. Read input JSON from stdin: environment, accounts.items, observations.items. Orders use the submit_trading_order schema.",
    input_schema={
        "type": "object",
        "properties": {"source": {"type": "string", "minLength": 1, "maxLength": 64000}},
        "required": ["source"],
        "additionalProperties": False,
    },
    mutates=("trading",),
    external_effect=True,
)
async def run_trading_strategy(input_data: dict):
    from backend.services.trading_scripts import execute_strategy

    return _result(await execute_strategy(_run_id(), input_data["source"]))
