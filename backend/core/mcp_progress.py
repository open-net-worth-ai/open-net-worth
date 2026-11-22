"""Progress messages for a long inbound MCP tool call (MCP ``notifications/progress``).

``mcp.server`` binds a reporter around a ``tools/call`` whose client asked for progress (a
``progressToken``) and streams each message back while the call runs; a tool reports through
``report``. Unbound — any other caller — ``report`` does nothing. A ContextVar in ``core`` so the
setter (``mcp``) and the reporter (``ai``) share no import.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Awaitable, Callable, Optional

Reporter = Callable[[str], Awaitable[None]]

_reporter: ContextVar[Optional[Reporter]] = ContextVar("mcp_progress_reporter", default=None)


def bind(reporter: Reporter) -> Token:
    return _reporter.set(reporter)


def reset(token: Token) -> None:
    _reporter.reset(token)


async def report(message: str) -> None:
    reporter = _reporter.get()
    if reporter is not None and message:
        await reporter(message)
