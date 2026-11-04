"""Run-bound trading identity and the dependency-inverted agent runner seam."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterator

TRADING_TOOLS = frozenset(
    {
        "get_trading_environment",
        "get_trading_account",
        "get_trading_market_data",
        "submit_trading_order",
        "cancel_trading_order",
        "list_trading_orders",
        "list_trading_fills",
        "record_trading_decision",
        "run_trading_strategy",
        "write_trading_file",
        "read_trading_files",
        "record_trading_work",
        "search_trading_research",
        "fetch_trading_research",
        "request_trading_market_data",
    }
)


@dataclass(frozen=True)
class TradingContext:
    run_id: str
    principal: str
    grant_digest: str | None = None
    cycle_lease_id: str | None = None


_context: ContextVar[TradingContext | None] = ContextVar("trading_context", default=None)
_runner: Callable[..., Awaitable[dict]] | None = None


def trading_context() -> TradingContext | None:
    return _context.get()


@contextmanager
def bind_trading_context(
    run_id: str, principal: str, grant_digest: str | None = None, cycle_lease_id: str | None = None
) -> Iterator[TradingContext]:
    context = TradingContext(
        run_id=run_id, principal=principal, grant_digest=grant_digest, cycle_lease_id=cycle_lease_id
    )
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def trading_tool_refusal(name: str) -> str | None:
    if _context.get() is not None and name not in TRADING_TOOLS:
        return "This trading run can call only its scoped trading tools."
    return None


def register_trading_runner(runner: Callable[..., Awaitable[dict]] | None) -> None:
    global _runner
    _runner = runner


async def run_trading_agent(run_id: str, **kwargs) -> dict:
    if _runner is None:
        raise RuntimeError("Trading agent runtime is not registered")
    return await _runner(run_id, **kwargs)
