"""One read of the book per request: a request-local memo of RAW store reads.

Why: a Dashboard load used to rebuild the same foundation in every request it fired — the whole
transaction table decoded 16 times, the holdings context built 19 times, the price maps ~20
times (docs/launch handoff, 2026-11-21). `GET /api/query` answers a whole page's sources in one
request; inside it this memo makes each raw read happen once.

What it is NOT: a cache. Nothing outlives the scope (ADR
`2026-08-12-no-derived-value-caching-by-default` is about serving a stored value later; a scope
lives for one request and is dropped with it). Outside an open scope every call computes, exactly
as before — the scheduler, Lucius's tools and every other route are untouched.

Correctness rules:
- **A write clears it.** `db.engine` calls `invalidate()` on every commit made in the same
  context, so a read after a write inside the scope reads the write.
- **Callers get their own copy** where the value is a list of rows a caller might sort or mark
  (`copy_rows`); a shared object is returned only for contexts already designed to be shared
  read-only (`positions.hydration_context`).
- **Explicit use, not a context var that leaks**: a streamed response computes each item in a
  fresh thread-pool context, so the scope is an object the request holds and re-enters with
  `use(scope)` around each item.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Hashable, Iterator, TypeVar

T = TypeVar("T")


class RequestMemo:
    """The memo of one request. `hits`/`misses` are for the profile scripts and tests."""

    __slots__ = ("_values", "hits", "misses")

    def __init__(self) -> None:
        self._values: dict[Hashable, Any] = {}
        self.hits = 0
        self.misses = 0

    def clear(self) -> None:
        self._values.clear()


_current: ContextVar[RequestMemo | None] = ContextVar("onw_request_memo", default=None)


@contextmanager
def use(scope: RequestMemo) -> Iterator[RequestMemo]:
    """Make `scope` the active one for the duration of the block (re-entrant)."""
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


@contextmanager
def opened() -> Iterator[RequestMemo]:
    """Open a fresh scope for the block."""
    with use(RequestMemo()) as scope:
        yield scope


def active() -> RequestMemo | None:
    return _current.get()


def memo(key: Hashable, compute: Callable[[], T]) -> T:
    """`compute()` once per open scope for `key`; outside a scope, always `compute()`."""
    scope = _current.get()
    if scope is None:
        return compute()
    if key in scope._values:
        scope.hits += 1
        return scope._values[key]
    scope.misses += 1
    value = compute()
    scope._values[key] = value
    return value


def copy_rows(rows: list[dict]) -> list[dict]:
    """A caller-owned copy of a memoised row list: a new list of new (shallow) dicts, so a
    caller that sorts the list or sets a key on a row cannot change what the next caller reads."""
    return [dict(r) for r in rows]


def memo_rows(key: Hashable, compute: Callable[[], list[dict]]) -> list[dict]:
    """`memo` for a list of row dicts, handing every caller its own copy (see `copy_rows`)."""
    if _current.get() is None:
        return compute()
    return copy_rows(memo(key, compute))


def invalidate() -> None:
    """Drop everything the active scope holds — called after every commit in this context."""
    scope = _current.get()
    if scope is not None:
        scope.clear()
