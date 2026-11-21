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


def to_budget(plan: dict, month: str) -> int | None:
    """Actual's To Budget for ``month`` ("YYYY-MM"), from the plan's months, in its unit: ported
    from ``budget/envelope.ts``. Each month: what last month left to budget, plus this month's
    income, less this month's assigned, less what an envelope overspent last month without a
    carryover flag, less money held for later. A hold from an earlier month has come back by now,
    so only this month's counts. ``None`` for a tracking budget, which has no To Budget."""
    if plan.get("budget_mode") == "tracking":
        return None
    income = {c["id"] for c in plan.get("categories") or [] if c.get("is_income")}
    held = {h["month"]: h["amount"] for h in plan.get("held") or []}
    unlisted = _unlisted_income(plan)
    months = sorted(plan.get("months") or [], key=lambda m: m["month"])
    left, overspent = 0, 0
    for m in months:
        key = m["month"][:7]
        cats = m.get("categories") or []
        got = sum(c.get("activity", 0) for c in cats if c["id"] in income)
        got += unlisted.get(key, 0)
        assigned = sum(c.get("budgeted", 0) for c in cats if c["id"] not in income)
        current = left + got - assigned + overspent - (held.get(key, 0) if key == month else 0)
        if key == month:
            return current
        left = current
        overspent = sum(
            c["balance"]
            for c in cats
            if c["id"] not in income and c.get("balance", 0) < 0 and not c.get("carryover")
        )
    return None


def _unlisted_income(plan: dict) -> dict[str, int]:
    """Income the plan's months leave out, by month: an account's opening balance, which Actual
    files under Starting Balances and counts towards To Budget, and the plan carries with no
    category. Known by its payee, as the sync knows openings (``context._openings``). Rows in budget
    accounts only."""
    on_budget = {a["id"] for a in plan.get("accounts") or [] if a.get("on_budget")}
    opening_payees = {
        p["id"]
        for p in plan.get("payees") or []
        if (p.get("name") or "").strip().lower() == "starting balance"
    }
    out: dict[str, int] = {}
    for t in plan.get("transactions") or []:
        if t.get("deleted") or t.get("account_id") not in on_budget or t.get("category_id"):
            continue
        if t.get("payee_id") not in opening_payees or t.get("transfer_account_id"):
            continue
        month = (t.get("date") or "")[:7]
        out[month] = out.get(month, 0) + int(t.get("amount") or 0)
    return out
