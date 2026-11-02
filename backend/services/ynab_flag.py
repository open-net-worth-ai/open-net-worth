"""The ``ynab_import_and_sync`` flag's switch point (docs/FEATURE_FLAGS.md). Off is main's YNAB
connector: the plan import and the two-way sync are not there (their routes answer 404) and
nothing of theirs runs. Their stored state is kept, so turning the flag back on carries on from
where it was."""

from __future__ import annotations

import functools
from typing import Callable, TypeVar

from backend.core.errors import NotFoundError
from backend.services.feature_flags import enabled

FLAG = "ynab_import_and_sync"

F = TypeVar("F", bound=Callable)


def on() -> bool:
    return enabled(FLAG)


def gated(fn: F) -> F:
    """``fn``, refused with ``NotFoundError`` while the flag is off."""

    @functools.wraps(fn)
    def call(*args, **kwargs):
        if not on():
            raise NotFoundError("YNAB import and two-way sync are turned off.")
        return fn(*args, **kwargs)

    return call  # type: ignore[return-value]
