"""The ``actual_budget_sync`` flag's switch point (docs/FEATURE_FLAGS.md). Off, the Actual Budget
connector is gone: it is not listed, its routes answer 404 and nothing of its runs. Its stored
state is kept, so turning the flag back on carries on from where it was."""

from __future__ import annotations

import functools
from typing import Callable, TypeVar

from backend.core.errors import NotFoundError
from backend.services.feature_flags import enabled

ACTUAL_FLAG = "actual_budget_sync"

F = TypeVar("F", bound=Callable)


def on() -> bool:
    return enabled(ACTUAL_FLAG)


def require() -> None:
    """Raise ``NotFoundError`` while the flag is off."""
    if not on():
        raise NotFoundError("Actual Budget import and sync are turned off.")


def gated(fn: F) -> F:
    """``fn``, refused with ``NotFoundError`` while the flag is off."""

    @functools.wraps(fn)
    def call(*args, **kwargs):
        require()
        return fn(*args, **kwargs)

    return call  # type: ignore[return-value]
