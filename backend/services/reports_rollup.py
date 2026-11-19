"""Reports-group Overview figures: multi-month income/expense rollups, the category table,
top merchants and the savings rate. The month-flow Sankey lives in `cash_flow_graph`.

Planning's per-month view already aggregates each category. What the browser was doing on top
of it was a full second reporting layer — six months fetched in parallel, folded into totals,
ranked into a top-N with an "Other" remainder, averaged, and divided into a savings rate. All
of it moves here, so every widget in the group reads the same month totals instead of each
re-folding them from its own fetch.
"""

from __future__ import annotations

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services import ledger as ledger_service
from backend.services import planning as planning_service
from backend.services import report_window
from backend.services.fx_cache import load_fx_cache

DEFAULT_MONTHS = 6
# The trailing saving: the average over this many complete months (needing at least the minimum
# to have anything in them). One definition for the Dashboard's Savings rate and the Outlook.
TRAILING_SAVING_MONTHS = 3
TRAILING_SAVING_MIN_MONTHS = 2
DEFAULT_SERIES_CAP = 6
OTHER_KEY = "__other__"
# Not "Other": a book can have a real category by that name, and the remainder must not read as it.
OTHER_LABEL = "All other"
_EPSILON = 0.005


def _round2(value: float) -> float:
    return round(value + 0.0, 2)


def _past_months(ym: str, count: int) -> list[str]:
    year, month = (int(p) for p in ym.split("-"))
    anchor = year * 12 + (month - 1)
    out = []
    for offset in range(count - 1, -1, -1):
        total = anchor - offset
        out.append(f"{total // 12}-{total % 12 + 1:02d}")
    return out


def _row_category_ids(row: dict) -> list[str]:
    """The ledger categories behind one Planning row. An envelope row's `key` is the ENVELOPE id
    (`env_…`) — its categories are `category_ids` (several once envelopes are merged); an
    unbudgeted row's `key` is the category id itself; Uncategorized has none."""
    ids = row.get("category_ids")
    if ids:
        return [str(i) for i in ids]
    key = row.get("key")
    return [key] if key and not str(key).startswith("env_") else []


def month_summary(ym: str, tags: list[str] | None = None) -> dict:
    """One month's spend/income, per category, from Planning's own aggregates — plus HISTORY:
    rows dated before an account's Starting Balance, which the budget leaves out (the opening
    balance already holds them) but a report of where money went must count. A statement
    imported at onboarding otherwise read "No spending recorded in Sep" (persona QA
    2026-10-13). Uncategorized money in counts as income for the same reason.

    Payment envelopes are excluded from spending: a credit-card payment moves money between
    the user's own accounts, so counting it as spend would double-count the original purchase.

    ``tags`` narrows it to the transactions carrying any of them: the same fold, over those
    rows only (``planning.tagged_activity_by_category``), history included."""
    month_view = planning_service.budget_month(ym)
    if tags:
        activity = planning_service.tagged_activity_by_category(ym, tags)
        expense_rows = _expense_rows(
            month_view, lambda c, ids: sum(activity.get(i, 0.0) for i in ids)
        )
        expense_rows.extend(_history_only_rows(activity, _claimed(month_view)))
        received = planning_service.tagged_income_by_category(ym, tags)
        income_rows = _income_rows(lambda key: received.get(key, 0.0), received.get("", 0.0))
    else:
        history = planning_service.history_activity_by_category(ym)
        expense_rows = _expense_rows(
            month_view,
            lambda c, ids: float(c.get("activity") or 0.0) + sum(history.get(i, 0.0) for i in ids),
        )
        expense_rows.extend(_history_only_rows(history, _claimed(month_view)))
        income_view = planning_service.income_view(ym)
        received_by = {
            c.get("key"): float(c.get("received") or 0.0)
            for c in income_view.get("categories") or []
        }
        income_rows = _income_rows(lambda key: received_by.get(key, 0.0), _uncategorized_income(ym))
    expense_rows.sort(key=lambda r: r["amount"], reverse=True)
    income_rows.sort(key=lambda r: r["amount"], reverse=True)
    return {
        "ym": ym,
        "expense_rows": expense_rows,
        "expense_total": _round2(sum(r["amount"] for r in expense_rows)),
        "income_rows": income_rows,
        "income_total": _round2(sum(r["amount"] for r in income_rows)),
    }


def _budget_rows(month_view: dict):
    """Each spending row of the budget month with its group, and the ledger categories behind
    it — payment envelopes left out (see ``month_summary``)."""
    for group_index, group in enumerate(month_view.get("groups") or []):
        for c in group.get("categories") or []:
            if c.get("is_payment_envelope"):
                continue
            # The Uncategorized row holds blank-category activity.
            ids = [""] if group.get("unsorted") else _row_category_ids(c)
            yield group_index, group, c, ids


def _claimed(month_view: dict) -> set[str]:
    return {i for _gi, _g, _c, ids in _budget_rows(month_view) for i in ids}


def _expense_rows(month_view: dict, activity_of) -> list[dict]:
    rows = []
    for group_index, group, c, ids in _budget_rows(month_view):
        activity = activity_of(c, ids)
        if activity < -_EPSILON:
            rows.append(
                {
                    "key": c.get("key"),
                    "label": c.get("label") or "",
                    "color": c.get("color") or "",
                    "group_id": group.get("id") or "",
                    "group": group.get("name") or "",
                    "group_index": group_index,
                    "category_ids": _row_category_ids(c),
                    "amount": _round2(-activity),
                }
            )
    return rows


def _income_rows(received_of, uncategorized_in: float) -> list[dict]:
    from backend.services.categories import get_all_categories

    rows = []
    for c in get_all_categories(include_hidden=True):
        if c.get("kind") != "income":
            continue
        received = received_of(c["id"])
        if received > _EPSILON:
            rows.append(
                {
                    "key": c["id"],
                    "label": c.get("label") or c["id"],
                    "color": c.get("color") or "",
                    "amount": _round2(received),
                }
            )
    if uncategorized_in > _EPSILON:
        rows.append(
            {
                "key": "",
                "label": "Uncategorized income",
                "color": "",
                "amount": _round2(uncategorized_in),
            }
        )
    return rows


def _history_only_rows(history: dict[str, float], claimed: set[str]) -> list[dict]:
    """History spent under a category no budget row carries (an unbudgeted category, or
    Uncategorized on a book with no such row). Only real categories and Uncategorized — a
    payment envelope or an internal coverage key is never a row of spending."""
    from backend.services.categories import get_all_categories

    known = {c["id"]: c for c in get_all_categories(include_hidden=True)}
    rows = []
    for cat, delta in history.items():
        if cat in claimed or delta > -_EPSILON or (cat and cat not in known):
            continue
        meta = known.get(cat) or {}
        rows.append(
            {
                "key": cat,
                "label": meta.get("label") or "Uncategorized",
                "color": meta.get("color") or "",
                "group_id": "",
                "group": "",
                "group_index": -1,
                "category_ids": [cat] if cat else [],
                "amount": _round2(-delta),
            }
        )
    return rows


def _uncategorized_income(ym: str) -> float:
    """Money in with no category this month: the budget reads it as cash to assign, a report
    reads it as income (an onboarding user's salary, still unsorted, read "No income")."""
    import calendar

    year, month = int(ym[:4]), int(ym[5:7])
    last = calendar.monthrange(year, month)[1]
    base, rates = base_currency(), load_fx_cache()
    txns = ledger_service.transactions.query_txns(
        kind="income", date_from=f"{ym}-01", date_to=f"{ym}-{last:02d}"
    )
    return _round2(
        sum(ledger_service.txn_base_amount(t, base, rates) for t in txns if not t.get("category"))
    )


def _spending_month(points: list[dict]) -> str:
    """The month the spending widgets (Spending by category, Top merchants) describe: this
    month, or, while it has no spending yet (the 1st of a month, a book that just imported last
    month's statement), the newest earlier month in the window that does. A new month otherwise
    read "No spending recorded" over a full September (persona QA 2026-10-14)."""
    current = points[-1]
    if current["expense_total"] > _EPSILON:
        return current["ym"]
    return next(
        (p["ym"] for p in reversed(points[:-1]) if p["expense_total"] > _EPSILON), current["ym"]
    )


def _stacked_series(points: list[dict], side: str, cap: int) -> list[dict]:
    """Top-`cap` categories by total across the window, with the remainder folded into
    `OTHER_LABEL` — keeps a stacked chart legible on a book with dozens of categories."""
    totals: dict[str, dict] = {}
    for point in points:
        for row in point[side]:
            entry = totals.setdefault(
                row["key"], {"label": row["label"], "color": row["color"], "total": 0.0}
            )
            entry["total"] += row["amount"]
            entry["label"] = row["label"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1]["total"], reverse=True)
    top = ranked[:cap]
    top_keys = {key for key, _ in top}

    series = [
        {
            "key": key,
            "label": meta["label"],
            "color": meta["color"],
            "data": [next((r["amount"] for r in p[side] if r["key"] == key), 0.0) for p in points],
        }
        for key, meta in top
    ]
    if len(ranked) > cap:
        series.append(
            {
                "key": OTHER_KEY,
                "label": OTHER_LABEL,
                "color": "",
                "data": [
                    _round2(sum(r["amount"] for r in p[side] if r["key"] not in top_keys))
                    for p in points
                ],
            }
        )
    return series


def _complete_months(points: list[dict]) -> list[dict]:
    """What an average is taken over: the window from the first month with any money in or out,
    less its in-progress last month — a half-elapsed month counted as a whole one drags every
    average down, and so does a month before the book started ("AVG S$159/MO" over a single
    imported September of S$795, persona QA round 7). A window with a single month, or no
    money anywhere, keeps what it has."""
    first = next(
        (i for i, p in enumerate(points) if p["income_total"] > 0 or p["expense_total"] > 0),
        0,
    )
    tracked = points[first:]
    return tracked[:-1] or tracked


def _table(points: list[dict], side: str) -> list[dict]:
    """Every category seen in the window with its current / average / total — a table can
    scroll, so nothing folds into the stacked chart's remainder here. The average is over the
    window's complete months; `total` still spans the whole window."""
    latest_ym = points[-1]["ym"] if points else None
    complete_yms = {p["ym"] for p in _complete_months(points)}
    agg: dict[str, dict] = {}
    for point in points:
        for row in point[side]:
            entry = agg.setdefault(
                row["key"],
                {
                    "label": row["label"],
                    "color": row["color"],
                    "total": 0.0,
                    "complete_total": 0.0,
                    "current": 0.0,
                },
            )
            entry["label"] = row["label"]
            entry["color"] = entry["color"] or row["color"]
            entry["total"] += row["amount"]
            if point["ym"] in complete_yms:
                entry["complete_total"] += row["amount"]
            if point["ym"] == latest_ym:
                entry["current"] = row["amount"]
    months = len(complete_yms) or 1
    rows = [
        {
            "key": key,
            "label": meta["label"],
            "color": meta["color"],
            "current": _round2(meta["current"]),
            "average": _round2(meta["complete_total"] / months),
            "total": _round2(meta["total"]),
        }
        for key, meta in agg.items()
    ]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def _merchants(yms: list[str], limit: int, tags: list[str] | None = None) -> list[dict]:
    """Spend grouped by payee over the months `yms` (only transactions carrying one of `tags`,
    when given). No endpoint does this per-month (`GET /api/payees/report` is all-time), so it
    folds the expense legs here — server-side, in base currency, instead of in the browser."""
    from backend.services import payee_registry

    names = payee_registry.payee_names()
    base = base_currency()
    rates = load_fx_cache()
    months = set(yms)
    wanted = {t.strip().lower() for t in tags or [] if t and t.strip()}
    totals: dict[str, dict] = {}
    for t in ledger_service.transactions.all():
        if t.get("deleted") or t.get("kind") != "expense":
            continue
        if (t.get("date") or "")[:7] not in months:
            continue
        if wanted and not any(str(g).strip().lower() in wanted for g in t.get("tags") or []):
            continue
        payee_id = t.get("payee_id")
        description = (t.get("description") or "").strip()
        key = payee_id or f"d:{description.lower()}"
        label = (payee_id and names.get(payee_id)) or description or "Unknown"
        entry = totals.setdefault(key, {"label": label, "amount": 0.0})
        entry["amount"] += abs(ledger_service.txn_base_amount(t, base, rates))
    month_total = sum(meta["amount"] for meta in totals.values())
    rows = [
        {
            "key": key,
            "label": meta["label"],
            "amount": _round2(meta["amount"]),
            # Share of the WHOLE period's spend, not of the top-N shown — a cut list never
            # inflates each row's share.
            "pct": round(meta["amount"] / month_total * 100, 2) if month_total > 0 else 0.0,
        }
        for key, meta in totals.items()
    ]
    rows.sort(key=lambda r: r["amount"], reverse=True)
    return rows[:limit]


def _side_totals(points: list[dict], key: str) -> dict:
    """Current / average / window total for one side — the table's three summary columns. The
    average is over complete months (`_complete_months`), like each row's."""
    values = [p[key] for p in points]
    complete = [p[key] for p in _complete_months(points)]
    return {
        "current": values[-1] if values else 0.0,
        "average": _round2(sum(complete) / (len(complete) or 1)),
        "total": _round2(sum(values)),
    }


def _savings(summary: dict) -> dict:
    income = summary["income_total"]
    saved = max(0.0, income - summary["expense_total"])
    return {
        "income": income,
        "spent": summary["expense_total"],
        "saved": _round2(saved),
        "rate_pct": round(min(100.0, max(0.0, (saved / income) * 100)), 2) if income > 0 else 0.0,
        "known": income > 0,
    }


def _latest_savings(points: list[dict]) -> dict:
    """The savings figure for the last COMPLETE month that has income, and which month that is
    (``ym``) — the same month the Money flow card reads. Mid-month the current month holds a
    stray dividend and no salary yet, which reads as "0% saved"; a finished month is the
    honest picture. Only when no finished month has income does the current one stand in
    (and failing that, the newest month, so a book with no income stays a known-false gauge)."""
    chosen = next(
        (p for p in reversed(points[:-1]) if p["income_total"] > 0),
        points[-1],
    )
    return {**_savings(chosen), "ym": chosen["ym"]}


def trailing_saving() -> dict | None:
    """What the ledger says is saved each month: income minus spending, averaged over the last
    `TRAILING_SAVING_MONTHS` complete months that have any activity, with that as a share of the
    income. The ONE trailing saving: the Outlook's "what you've been saving" check and the
    Dashboard's Savings rate both read it, so they can't quote different averages. `None` when
    fewer than `TRAILING_SAVING_MIN_MONTHS` complete months have anything in them."""
    this_month = user_today().strftime("%Y-%m")
    months = _past_months(this_month, TRAILING_SAVING_MONTHS + 1)[:-1]
    points = [month_summary(ym) for ym in months]
    active = [p for p in points if p["income_total"] > 0 or p["expense_total"] > 0]
    if len(active) < TRAILING_SAVING_MIN_MONTHS:
        return None
    income = sum(p["income_total"] for p in active)
    saved = sum(p["income_total"] - p["expense_total"] for p in active)
    return {
        "monthly": round(saved / len(active), 2),
        "rate_pct": round(saved / income * 100, 1) if income > 0 else None,
        "months": len(active),
        "from": active[0]["ym"],
        "to": active[-1]["ym"],
        "base_currency": base_currency(),
    }


def reports_aggregates(
    months: int = DEFAULT_MONTHS,
    merchant_limit: int = 10,
    series_cap: int = DEFAULT_SERIES_CAP,
    period: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Every reports-group figure, for the trailing `months` window ending this month — or,
    given a `period` (`report_window.PERIODS`), for that period's months, with the whole
    period summed under `period`. `tags` narrows every figure to the transactions carrying any
    of them."""
    tags = [t for t in tags or [] if t and t.strip()] or None
    this_month = user_today().strftime("%Y-%m")
    if period:
        yms = report_window.period_months(period)
    else:
        yms = _past_months(this_month, max(1, min(int(months), 24)))
    points = [month_summary(ym, tags) for ym in yms]
    latest = points[-1]
    spending_ym = _spending_month(points)

    expense_totals = _side_totals(points, "expense_total")
    income_totals = _side_totals(points, "income_total")
    out = {
        "base_currency": base_currency(),
        "months": yms,
        "tags": tags or [],
        "points": points,
        "current": latest,
        "expense_series": _stacked_series(points, "expense_rows", series_cap),
        "income_series": _stacked_series(points, "income_rows", series_cap),
        "expense_table": _table(points, "expense_rows"),
        "income_table": _table(points, "income_rows"),
        "expense_totals": expense_totals,
        "income_totals": income_totals,
        "net_totals": {
            "current": _round2(income_totals["current"] - expense_totals["current"]),
            "average": _round2(income_totals["average"] - expense_totals["average"]),
            "total": _round2(income_totals["total"] - expense_totals["total"]),
        },
        "avg_monthly_spend": expense_totals["average"],
        "net_total": _round2(income_totals["total"] - expense_totals["total"]),
        # The month the spending widgets show: `current_month` unless it has none yet.
        "current_month": this_month,
        "spending_month": spending_ym,
        "merchants": _merchants([spending_ym], merchant_limit, tags),
        "savings": {**_latest_savings(points), "trailing": trailing_saving()},
    }
    if period:
        out["period"] = _period_block(period, points, merchant_limit, tags)
    return out


def _period_block(period: str, points: list[dict], merchant_limit: int, tags) -> dict:
    """The whole period as one figure: what Spending by category, Top merchants and Savings
    rate show once a widget picks a period."""
    summary = report_window.combine(points)
    yms = summary["months"]
    return {
        "id": period,
        "from": yms[0],
        "to": yms[-1],
        **summary,
        "merchants": _merchants(yms, merchant_limit, tags),
        "savings": _savings(summary),
    }
