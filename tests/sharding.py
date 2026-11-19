"""``--shard=K/N``: run one of N disjoint slices of the collected suite (CI fans the gate out
over N runners; a 28k-test run on one 4-core runner took ~20 min).

Whole FILES are dealt, never single tests, so module fixtures and xdist's load balancing work
as before. Files are packed greedily, biggest first, onto the lightest shard by test count:
every shard collects the same items, so every shard (and every xdist worker inside it)
computes the same deal with no coordination. Union of the N slices = the unsharded run.
"""

from __future__ import annotations

from collections import Counter

import pytest


def parse(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        k, n = (int(p) for p in value.split("/"))
    except ValueError:
        raise pytest.UsageError(f"--shard expects K/N, got {value!r}") from None
    if not 1 <= k <= n:
        raise pytest.UsageError(f"--shard {value!r}: K must be in 1..N")
    return k, n


def assign(files: Counter[str], n: int) -> dict[str, int]:
    loads = [0] * n
    deal: dict[str, int] = {}
    for path, count in sorted(files.items(), key=lambda kv: (-kv[1], kv[0])):
        shard = min(range(n), key=lambda i: (loads[i], i))
        deal[path] = shard
        loads[shard] += count
    return deal


def select(config: pytest.Config, items: list[pytest.Item]) -> None:
    spec = parse(config.getoption("--shard"))
    if spec is None:
        return
    k, n = spec
    files = Counter(item.nodeid.split("::", 1)[0] for item in items)
    deal = assign(files, n)
    keep, drop = [], []
    for item in items:
        (keep if deal[item.nodeid.split("::", 1)[0]] == k - 1 else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
