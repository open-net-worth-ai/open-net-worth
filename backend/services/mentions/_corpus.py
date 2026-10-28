"""Short-lived memo of the data the mention picker searches over.

Typing "@herbal" runs the whole search once per settled keystroke, and every run rebuilt the
same budget month (category + envelope), hydrated portfolio (holding + instrument +
allocation slice) and payee usage from scratch — over a second of pure Python per keystroke,
minutes when the machine is under load. A corpus is reused while the data is unchanged: the
tag is the read-cache epoch (any DB commit, file write or new local day) on the same engine,
plus a TTL that bounds drift from live prices, which move in memory without a write.

Search rows only. Resolve, chips handed to Lucius and every money screen still compute fresh.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from typing import Any

from backend.db import read_cache
from backend.db.engine import get_engine

_TTL_S = 30.0

_lock = threading.Lock()
_entries: dict[tuple, tuple[Any, tuple, float, Any]] = {}  # key -> (engine, epoch, at, value)
_key_locks: dict[tuple, threading.Lock] = {}


def corpus(fn: Callable[..., Any], *args: Any) -> Any:
    """``fn(*args)``, served from the memo while the data it read is unchanged.

    Keyed on the function object, so a monkeypatched function never sees another's value.
    Concurrent misses for one key compute once. Callers get a deepcopy."""
    epoch = read_cache.current_epoch()
    if epoch is None:  # non-SQLite: no change signal to key on
        return fn(*args)
    engine = get_engine()
    key = (fn, args)
    with _lock:
        key_lock = _key_locks.setdefault(key, threading.Lock())
    with key_lock:
        hit = _entries.get(key)
        if (
            hit is not None
            and hit[0] is engine
            and hit[1] == epoch
            and time.monotonic() - hit[2] < _TTL_S
        ):
            return copy.deepcopy(hit[3])
        value = fn(*args)
        # Tagged with the epoch read BEFORE computing: a write landing mid-compute moves the
        # epoch on, so this entry is never served for the newer data.
        with _lock:
            _entries[key] = (engine, epoch, time.monotonic(), value)
        return copy.deepcopy(value)


def clear() -> None:
    with _lock:
        _entries.clear()
        _key_locks.clear()
