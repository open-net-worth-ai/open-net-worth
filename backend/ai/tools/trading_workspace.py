"""Scoped research, files and emitted work for arbitrary trading executors."""

import json

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import schema_of
from backend.core.errors import ConflictError, ValidationError
from backend.core.trading_runtime import trading_context
from backend.models.trading import TradingMarketRequest
from backend.services import trading
from backend.services.trading.work_records import record_work, workspace_files, write_workspace_file


def _result(value):
    return json.dumps(value, ensure_ascii=False, default=str), None


def _run_id():
    context = trading_context()
    if context is None:
        raise ValidationError("Workspace tools require a server-owned trading run")
    return context.run_id


@tool(
    "write_trading_file",
    description="Save an immutable version of a relative workspace research/code/test file. No host filesystem access.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 160},
            "content": {"type": "string", "maxLength": 64000},
        },
        "required": ["name", "content"],
        "additionalProperties": False,
    },
    mutates=("trading",),
)
async def write_trading_file(data):
    row = write_workspace_file(_run_id(), data["name"], data["content"])
    return _result({"event_id": row["id"], **row["payload"]})


@tool(
    "read_trading_files",
    description="List workspace files, or read an exact saved version by name/version.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 160},
            "version": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    },
    mutates=(),
)
async def read_trading_files(data):
    return _result(workspace_files(_run_id(), **data))


@tool(
    "record_trading_work",
    description="Publish actual executor output, analysis, actor activity, tool evidence or wait state. Never invent unreported reasoning.",
    input_schema={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": [
                    "input",
                    "output",
                    "analysis",
                    "reasoning",
                    "tool_call",
                    "tool_result",
                    "error",
                    "wait",
                    "actor",
                    "native_event",
                ],
            },
            "payload": {"type": "object"},
        },
        "required": ["kind", "payload"],
        "additionalProperties": False,
    },
    mutates=("trading",),
)
async def record_trading_work(data):
    return _result(record_work(_run_id(), data["kind"], data["payload"]))


@tool(
    "search_trading_research",
    description="Search public current research for this Paper/Live mandate. Historical environments refuse current web research.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 1000}},
        "required": ["query"],
        "additionalProperties": False,
    },
    mutates=(),
    external_effect=True,
)
async def search_trading_research(data):
    run = trading.get_run(_run_id())
    if run["mode"] == "backtest":
        raise ConflictError("Current web research is unavailable in a historical environment")
    from backend.ai.tools.misc import web_search

    return await web_search(data)


@tool(
    "fetch_trading_research",
    description="Read an explicitly supplied public research URL in Paper/Live. Uses the app's existing pinned public-URL fetcher; no credentials or personal book.",
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string", "minLength": 1, "maxLength": 2000}},
        "required": ["url"],
        "additionalProperties": False,
    },
    mutates=(),
    external_effect=True,
)
async def fetch_trading_research(data):
    if trading.get_run(_run_id())["mode"] == "backtest":
        raise ConflictError("Current URL research is unavailable in a historical environment")
    from backend.ai.tools.misc import fetch_page

    return await fetch_page(data)


@tool(
    "request_trading_market_data",
    description="Choose current instruments for a Paper/Live workspace's reviewed Yahoo feed. Subscribes and fetches quotes/bars within allowed markets and allocated venue/currency; cannot change mandate, funds or clock. Backtest refuses current data.",
    input_schema=schema_of(TradingMarketRequest, tool_name="request_trading_market_data"),
    mutates=("trading",),
    external_effect=True,
)
async def request_trading_market_data(data):
    from backend.services.trading.market_requests import request_market_data

    return _result(await request_market_data(_run_id(), data))
