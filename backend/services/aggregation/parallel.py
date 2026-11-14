"""Per-account reads from a bank's API, side by side.

A bank API answers one account per request (details, balances), each taking a second or more,
so listing a connection's accounts one after another took 8–13 s on Enable Banking's sandbox.
``each`` runs them at once and keeps the input order. Every task runs in a copy of the caller's
context, so a write it makes (a cached detail) is attributed like the caller's own."""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Banks see one user's handful of accounts; more than this at once only invites a 429.
_MAX_AT_ONCE = 6


def each(fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
    """``[fn(item) for item in items]``, the calls made at the same time. An exception from
    one call is raised here, as the list comprehension would."""
    todo = list(items)
    if len(todo) <= 1:
        return [fn(item) for item in todo]
    with ThreadPoolExecutor(max_workers=min(_MAX_AT_ONCE, len(todo))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in todo]
        return [f.result() for f in futures]
