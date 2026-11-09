"""What a category has available each month, ported from Actual's own budget sheets.

Actual's database keeps only what was assigned; the available figure is computed by its client.
The envelope rule (``loot-core`` ``budget/envelope.ts``): a month's available is its assigned plus
its activity plus the PREVIOUS month's available, where that carry is the figure as it was if the
previous month's carryover flag is on, else floored at zero. A tracking budget has no floor and
carries nothing unless flagged. ``make actual-smoke`` checks this against the official client.
"""

from __future__ import annotations

from typing import Iterable

Budgets = dict[tuple[int, str], tuple[int, bool]]  # (YYYYMM, category) -> (assigned, carryover)
Activity = dict[tuple[int, str], int]


def next_month(month: int) -> int:
    year, number = divmod(month, 100)
    return (year + 1) * 100 + 1 if number == 12 else month + 1


def month_range(first: int, last: int) -> list[int]:
    out, month = [], first
    while month <= last:
        out.append(month)
        month = next_month(month)
    return out


def available(
    series: Iterable[int],
    categories: Iterable[str],
    budgets: Budgets,
    activity: Activity,
    mode: str = "envelope",
) -> dict[tuple[int, str], int]:
    """Every category's available in every month of ``series`` (ascending and contiguous):
    ``{(YYYYMM, category): amount}``. Inputs and result share one unit."""
    months = list(series)
    out: dict[tuple[int, str], int] = {}
    for category in categories:
        previous, previous_flag = 0, False
        for month in months:
            assigned, flagged = budgets.get((month, category), (0, False))
            if previous_flag:
                carried = previous
            elif mode == "envelope":
                carried = max(0, previous)
            else:
                carried = 0
            previous = assigned + activity.get((month, category), 0) + carried
            previous_flag = flagged
            out[(month, category)] = previous
    return out
