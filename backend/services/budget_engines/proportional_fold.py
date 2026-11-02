"""50/30/20's money math — the envelope-based model (owner design, 2026-09-25; ADR
``docs/adr/2026-09-25-fifty-thirty-twenty-is-envelope-based-with-caps.md``, superseding the
pure category-fold model this file used to implement). Split out of ``proportional.py`` to
keep that file (entry point/orchestration) under the ~400-line guidance; ``ViewModel``
assembly itself lives in ``proportional_view.py`` (split again to keep BOTH under it).

``compute_month()`` is the ONE computation both consumers share — ``services/budget_engines/
service.py``'s ``GET /api/planning/proportional/month`` (the screen) and
``ProportionalEngine.fold()`` (``get_budget_view``/``GET /api/planning/view`` — Lucius and
the generic renderer, via ``proportional_view.to_view_model``) — so the two can never
silently disagree: ``to_view_model()`` is a pure RESHAPE of this function's own output,
never a second pass over transactions.

Every leg is valued via ``ledger.txn_base_amount``/``txn_split_base_amount`` (never a
caller's own FX, never a raw stored snapshot) — the same rule every other engine in this
package follows. An envelope's ``activity`` for a month is a NET fold over its member
categories: an EXPENSE leg adds, a categorized INCOME leg (a refund posted to the SAME
category as the original spend) subtracts, a TRANSFER never counts, a split contributes
only its own line's fraction.

ELIGIBILITY (2026-09-25 amendment, ADR ``2026-09-25-fifty-thirty-twenty-is-envelope-based-
with-caps.md``): every expense/income leg is gated through Zero-Based's OWN rule, reused
verbatim rather than re-derived (``planning.plan_leg_ok`` — its account must be a known
plan-source account, dated on/after that account's tracking start), so an envelope's
activity/history/``no_envelope``/``uncategorized_expense`` are identical to Zero-Based's for
the same book/month. ``compute_month`` threads ``ctx["plan_source_ids"]``/``ctx["cutoff_by_id"]``
into every fold below (populated by ``shared_context.resolve_shared``/
``service._shared_context``, the same way ``envelopes``/``groups`` were). A TRANSFER still
never counts and an investment leg still isn't folded at all — both remain this engine's own
prior, documented simplification (no Ready-to-Assign identity for a transfer's cash side to
reconcile against), unchanged by this amendment — see the ADR's amendment section.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from statistics import median

from backend.core.user_time import user_today
from backend.services import ledger as ledger_service
from backend.services import planning
from backend.services.budget_engines import proportional_state as state_

# ── Trailing-window date math ────────────────────────────────────────────────────────


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def window_months(period: str, count: int) -> list[str]:
    """``[period, period-1, ..., period-(count-1)]`` — newest first."""
    months = [period]
    for _ in range(count - 1):
        months.append(_prev_month(months[-1]))
    return months


# ── One-pass category activity, over a window of months ───────────────────────────────


def _split_lines(t: dict, base: str, rates: dict) -> list[tuple[str | None, float]]:
    splits = t.get("splits")
    if splits:
        return [
            (s.get("category"), ledger_service.txn_split_base_amount(t, s, base, rates))
            for s in splits
        ]
    return [(t.get("category"), ledger_service.txn_base_amount(t, base, rates))]


def _category_activity_pass(
    txns: list[dict],
    base: str,
    rates: dict,
    months: set[str],
    day_limit: dict[str, int] | None,
    plan_source_ids: frozenset[str] | set[str],
    cutoff_by_id: dict,
) -> dict[str, dict[str, float]]:
    """``{month: {category_id: net_activity}}`` — the ONE fold ``category_activity_window``/
    ``category_activity_window_clipped`` both specialize. An EXPENSE leg adds, a categorized
    INCOME leg subtracts (a refund posted to the same category), a TRANSFER never counts.
    Blank-category legs are dropped (tracked separately — see ``uncategorized_expense``).
    ``day_limit``, when given, drops a leg dated after ``day_limit[month]`` (inclusive).
    ``plan_source_ids``/``cutoff_by_id`` gate each leg via ``planning.plan_leg_ok`` (module
    docstring) on its moving account (expense: ``from_account_id``; income: ``to_account_id``)."""
    by_month: dict[str, dict[str, float]] = {m: {} for m in months}
    for t in txns:
        if t.get("deleted"):
            continue
        date_str = t.get("date") or ""
        month = date_str[:7]
        if month not in months:
            continue
        if day_limit is not None:
            try:
                if int(date_str[8:10]) > day_limit[month]:
                    continue
            except (ValueError, IndexError):
                continue
        kind = t.get("kind")
        if kind == "expense":
            leg, sign = "from_account_id", 1.0
        elif kind == "income":
            leg, sign = "to_account_id", -1.0
        else:
            continue
        if not planning.plan_leg_ok(t, leg, plan_source_ids, cutoff_by_id):
            continue
        bucket = by_month[month]
        for cat, amt in _split_lines(t, base, rates):
            if cat:
                bucket[cat] = bucket.get(cat, 0.0) + sign * amt
    return {m: {c: round(a, 2) for c, a in cats.items()} for m, cats in by_month.items()}


def category_activity_window(
    txns: list[dict],
    base: str,
    rates: dict,
    period: str,
    *,
    months: int = 4,
    plan_source_ids: frozenset[str] | set[str] = frozenset(),
    cutoff_by_id: dict | None = None,
) -> dict[str, dict[str, float]]:
    """``{month: {category_id: net_activity}}`` for ``period`` and the ``months - 1`` months
    before it — ONE pass over ``txns``. ``plan_source_ids`` defaults fail CLOSED (mirrors
    ``planning._activity_by_category``); a real caller always threads ``ctx``'s values."""
    return _category_activity_pass(
        txns,
        base,
        rates,
        set(window_months(period, months)),
        None,
        plan_source_ids,
        cutoff_by_id or {},
    )


def category_activity_window_clipped(
    txns: list[dict],
    base: str,
    rates: dict,
    months: list[str],
    day: int,
    plan_source_ids: frozenset[str] | set[str] = frozenset(),
    cutoff_by_id: dict | None = None,
) -> dict[str, dict[str, float]]:
    """Same fold, but a leg dated after ``day`` (clamped to each month's own length) is
    excluded — the "so far this month" pace cut, applied to EACH of ``months`` at its own
    length (viewing day 25 clips February at 25, not 28). ``plan_source_ids``/``cutoff_by_id``
    — see ``category_activity_window``'s own defaults."""
    limit = {m: min(day, monthrange(int(m[:4]), int(m[5:7]))[1]) for m in months}
    return _category_activity_pass(
        txns, base, rates, set(months), limit, plan_source_ids, cutoff_by_id or {}
    )


def uncategorized_expense(
    txns: list[dict],
    period: str,
    base: str,
    rates: dict,
    plan_source_ids: frozenset[str] | set[str] = frozenset(),
    cutoff_by_id: dict | None = None,
) -> tuple[float, int]:
    """(amount, count) of blank-category EXPENSE spend in ``period`` — never netted against
    a refund (an uncategorized inflow is inflow-to-assign, not a credit against unlabeled
    spend; see ``services.planning``'s own ``_INFLOW_CATEGORIES`` convention). Gated via
    ``plan_leg_ok`` like every fold in this module (defaults fail closed)."""
    cutoff_by_id = cutoff_by_id or {}
    total, count = 0.0, 0
    for t in txns:
        if t.get("deleted") or t.get("kind") != "expense":
            continue
        if not (t.get("date") or "").startswith(period):
            continue
        if not planning.plan_leg_ok(t, "from_account_id", plan_source_ids, cutoff_by_id):
            continue
        for cat, amt in _split_lines(t, base, rates):
            if not cat:
                total += amt
                count += 1
    return round(total, 2), count


def income_by_category(
    txns: list[dict],
    month: str,
    base: str,
    rates: dict,
    plan_source_ids: frozenset[str] | set[str] = frozenset(),
    cutoff_by_id: dict | None = None,
) -> dict[str, float]:
    """Every category's OWN received income for ``month`` (``kind == "income"`` legs only,
    blank-category ones dropped — that's inflow-to-assign, not a category's own figure).

    Gated via ``plan_leg_ok`` like every fold here (defaults fail closed): Zero-Based applies
    that SAME check to every income leg before deciding refund-vs-inflow, so this fold — the
    closest 50/30/20 has to "money that reached the plan" — follows suit rather than counting
    a leg Zero-Based would have ignored as off-budget or pre-tracking-start (ADR amendment,
    2026-09-25, has the fuller reasoning)."""
    cutoff_by_id = cutoff_by_id or {}
    by_cat: dict[str, float] = {}
    for t in txns:
        if t.get("deleted") or t.get("kind") != "income":
            continue
        if not (t.get("date") or "").startswith(month):
            continue
        if not planning.plan_leg_ok(t, "to_account_id", plan_source_ids, cutoff_by_id):
            continue
        for cat, amt in _split_lines(t, base, rates):
            if cat:
                by_cat[cat] = by_cat.get(cat, 0.0) + amt
    return {c: round(a, 2) for c, a in by_cat.items()}


def month_status(period: str, today: date) -> tuple[str, int, int]:
    """(status, day, days_in_month) for ``period`` relative to ``today`` — "current" reports
    today's real day-of-month; "past"/"future" report that month's own full length (so a
    ``*_by_day`` pace figure the caller never clips for those two statuses is already,
    trivially, the full-month one)."""
    year, month = int(period[:4]), int(period[5:7])
    days_in_month = monthrange(year, month)[1]
    if (year, month) == (today.year, today.month):
        return "current", today.day, days_in_month
    if (year, month) > (today.year, today.month):
        return "future", days_in_month, days_in_month
    return "past", days_in_month, days_in_month


def resolve_income(status: str, profile_income: float | None, actual: float) -> tuple[float, str]:
    """The month's income figure + where it came from: current/future always reads the
    profile's usual monthly income (0 if unset — never a fabricated actual for a month that
    hasn't happened); a past month prefers what was actually received, falling back to the
    profile (then 0) only when nothing was received."""
    if status == "past" and actual > 0:
        return round(actual, 2), "actual"
    return round(profile_income or 0.0, 2), "profile"


def sum_income(by_cat: dict[str, float], category_ids: set[str]) -> float:
    return round(sum(v for c, v in by_cat.items() if c in category_ids), 2)


def _unlinked_categories(
    by_cat: dict[str, float], covered: set[str], income_ids: set[str], cat_rows: list[dict]
) -> list[dict]:
    """Every expense-side category with spending this period that no row covers, biggest
    first: ``{id, label, color, activity}``. Its sum, floored at zero, is ``no_envelope``."""
    meta = {r["id"]: r for r in cat_rows}
    out = [
        {
            "id": cat,
            "label": (meta.get(cat) or {}).get("label") or cat,
            "color": (meta.get(cat) or {}).get("color") or None,
            "activity": round(amt, 2),
        }
        for cat, amt in by_cat.items()
        if cat not in covered and cat not in income_ids and abs(amt) >= 0.005
    ]
    return sorted(out, key=lambda r: -r["activity"])


# ── Pace history ─────────────────────────────────────────────────────────────────────


def envelope_history(
    category_ids: list[str],
    by_month: dict[str, dict[str, float]],
    by_month_clipped: dict[str, dict[str, float]] | None,
    prior_months: list[str],
    months_with_data: set[str],
) -> dict:
    """``{last, avg3, last_by_day, avg3_by_day, median3, rest_median3}`` for one envelope. ``last`` is the single
    month immediately before the viewed period; ``avg3`` is the mean over the SAME trailing
    3-month window ``last`` is the newest entry of, skipping a window month the book has NO
    transactions for AT ALL (a month before the book existed) — an envelope's own $0 in a
    month the book WAS tracking still counts and drags the average down; a month with no
    book data at all does not. ``by_day`` variants repeat the same math against
    ``by_month_clipped`` (only built by the caller when viewing the CURRENT month); when
    ``None`` (a past or future period), they simply equal the full-month figures — no
    partial-month concept applies to a closed or not-yet-started month.

    ``median3``/``rest_median3`` feed the running month's projection only: the MEDIAN month,
    and the median of what each month added after the viewed day. A mean let one trip or
    one tax bill become "usual" and forecast it again (owner, 2026-11-02: "it's just the new
    month and it says I am heading to 22%"); with three months, one outlier never sets the
    median. ``rest_median3`` is 0 for a closed or future month — no rest of it to add."""

    def total(monthmap: dict[str, float]) -> float:
        return round(sum(monthmap.get(c, 0.0) for c in category_ids), 2)

    last_month = prior_months[0]
    last = total(by_month.get(last_month, {}))
    counted = [m for m in prior_months if m in months_with_data]
    fulls = [total(by_month.get(m, {})) for m in counted]
    avg3 = round(sum(fulls) / len(fulls), 2) if fulls else 0.0
    median3 = round(median(fulls), 2) if fulls else 0.0

    if by_month_clipped is None:
        return {
            "last": last,
            "avg3": avg3,
            "last_by_day": last,
            "avg3_by_day": avg3,
            "median3": median3,
            "rest_median3": 0.0,
        }

    clipped = [total(by_month_clipped.get(m, {})) for m in counted]
    last_by_day = total(by_month_clipped.get(last_month, {}))
    avg3_by_day = round(sum(clipped) / len(clipped), 2) if clipped else 0.0
    rests = [f - c for f, c in zip(fulls, clipped)]
    return {
        "last": last,
        "avg3": avg3,
        "last_by_day": last_by_day,
        "avg3_by_day": avg3_by_day,
        "median3": median3,
        "rest_median3": round(median(rests), 2) if rests else 0.0,
    }


# ── The shared computation ───────────────────────────────────────────────────────────


def compute_month(
    period: str,
    ctx: dict,
    state: dict,
    *,
    managed_ids: set[str],
    profile_income: float | None,
    configured: bool,
    today: date | None = None,
) -> dict:
    """The REST-contract-shaped computation both ``GET /api/planning/proportional/month``
    and ``ProportionalEngine.fold()`` build on — see module docstring. ``state`` is the
    ALREADY-NORMALIZED shape (``proportional_state.normalize_own_state``);
    ``managed_ids`` is ``envelopes_service.bot_managed_envelope_ids()`` (read by the
    caller, never here — this function stays a pure fold over its arguments, testable with
    a synthetic ``shared``, no database)."""
    today = today or user_today()
    base, rates, txns = ctx["base"], ctx["rates"], ctx["txns"]
    cat_rows = ctx["categories"]
    # Zero-Based's own eligibility gate (module docstring) — REQUIRED, never defaulted: every
    # real ctx populates both, the same way it populates ``envelopes``/``groups``.
    plan_source_ids, cutoff_by_id = ctx["plan_source_ids"], ctx["cutoff_by_id"]

    status, day, days_in_month = month_status(period, today)

    rows_meta = state_.row_envelopes(ctx["envelopes"], ctx["groups"], managed_ids)
    all_row_category_ids: set[str] = set()
    for env in rows_meta:
        all_row_category_ids.update(env.get("category_ids") or [])
    income_kind_ids = {r["id"] for r in cat_rows if r.get("kind") == "income"}

    months = window_months(period, 4)  # [period, period-1, period-2, period-3]
    prior_months = months[1:]
    by_month = category_activity_window(
        txns,
        base,
        rates,
        period,
        months=4,
        plan_source_ids=plan_source_ids,
        cutoff_by_id=cutoff_by_id,
    )
    by_month_clipped = (
        category_activity_window_clipped(
            txns, base, rates, prior_months, day, plan_source_ids, cutoff_by_id
        )
        if status == "current"
        else None
    )
    months_with_data = {
        (t.get("date") or "")[:7] for t in txns if not t.get("deleted") and t.get("date")
    }

    by_cat_this_period = by_month.get(period, {})
    uncategorized_amount, uncategorized_count = uncategorized_expense(
        txns, period, base, rates, plan_source_ids, cutoff_by_id
    )
    # "No envelope" is spend on a real, EXPENSE-side category that no row covers — an
    # income-kind category's own activity (ordinary salary, never a "refund" of anything
    # an envelope tracks) must never net against it, or a big enough paycheck would zero
    # out or invert a genuine uncovered-spend figure (the same "holds holes, never
    # surpluses" clamp the RTA no-bucket hold already applies, but that clamp alone isn't
    # enough while an income category's own inflow is still in the same sum).
    no_envelope_amount = round(
        max(
            0.0,
            sum(
                amt
                for cat, amt in by_cat_this_period.items()
                if cat not in all_row_category_ids and cat not in income_kind_ids
            ),
        ),
        2,
    )

    caps = state["caps"]
    groups = ctx["groups"]
    # An envelope with no colour of its own wears its first category's — the same fallback
    # Zero-Based's rows use (planning._budget_rows), so one envelope looks the same in both.
    cat_color = {r["id"]: r.get("color") or "" for r in cat_rows}
    # An app-kept group's envelope (a commitment's) is a row only in a month it spent — its
    # category's spending is real spending, and it must be SEEN, not fold unseen into "not in an
    # envelope"; a month it didn't spend it would only be a S$0 line (owner, 2026-09-28).
    synced = {g["id"] for g in groups if g.get("synced")}
    left_out = set(state["excluded_envelopes"])
    envelopes_out: list[dict] = []
    rows_by_group: dict[str, list[str]] = {}
    for env in rows_meta:
        env_id = env["id"]
        cat_ids = list(env.get("category_ids") or [])
        activity = round(sum(by_cat_this_period.get(c, 0.0) for c in cat_ids), 2)
        gid = state_.row_group_id(env, groups) or state_.NO_GROUP
        if gid in synced and abs(activity) < 0.005:
            continue
        rows_by_group.setdefault(gid, []).append(env_id)
        envelopes_out.append(
            {
                "id": env_id,
                "label": env.get("name", env_id),
                "color": (
                    env.get("color") or (cat_color.get(cat_ids[0]) if cat_ids else None) or None
                ),
                "group_id": gid,
                "group": env.get("group_name", ""),
                "category_ids": cat_ids,
                "cap": state_.cap_at(caps.get(env_id) or [], period),
                "activity": activity,
                "excluded": env_id in left_out,
                "history": envelope_history(
                    cat_ids, by_month, by_month_clipped, prior_months, months_with_data
                ),
            }
        )
    groups_out = state_.groups_out(groups, rows_by_group, envelopes_out, state)

    counted_ids = state["income_category_ids"]
    counted_set = set(counted_ids) if counted_ids is not None else income_kind_ids
    income_by_cat = income_by_category(txns, period, base, rates, plan_source_ids, cutoff_by_id)
    actual_income = sum_income(income_by_cat, counted_set)
    income_amount, income_source = resolve_income(status, profile_income, actual_income)
    last_month_income_by_cat = (
        income_by_category(txns, prior_months[0], base, rates, plan_source_ids, cutoff_by_id)
        if prior_months
        else {}
    )
    income_categories = [
        {
            "id": r["id"],
            "label": r.get("label", r["id"]),
            "color": r.get("color"),
            "counted": r["id"] in counted_set,
            "last_month": last_month_income_by_cat.get(r["id"], 0.0),
        }
        for r in sorted(
            (r for r in cat_rows if r["id"] in income_kind_ids),
            key=lambda r: (r.get("label") or "").lower(),
        )
    ]

    spent = round(sum(g["activity"] for g in groups_out if not g["excluded"]), 2)
    target_total = round(sum(g["target"] or 0 for g in groups_out if not g["excluded"]), 6)
    return {
        "month": period,
        "status": status,
        "day": day,
        "days_in_month": days_in_month,
        "configured": configured,
        "groups": groups_out,
        # The residual: income less every counted group's spend and the spend no row owns.
        "savings": {
            "amount": round(income_amount - spent - uncategorized_amount - no_envelope_amount, 2),
            # What the group targets leave for savings — None until a target is set.
            "target": round(100 - target_total, 6) if target_total > 0 else None,
        },
        "income": {
            "amount": income_amount,
            "source": income_source,
            "profile": round(profile_income, 2) if profile_income is not None else None,
            "actual": actual_income,
        },
        "income_categories": income_categories,
        "unassigned": {
            "uncategorized": uncategorized_amount,
            "no_envelope": no_envelope_amount,
            # The spend "not in an envelope" as rows — each category no row covers, so it is
            # seen, not only subtracted from savings (owner, 2026-09-28).
            "categories": _unlinked_categories(
                by_cat_this_period, all_row_category_ids, income_kind_ids, cat_rows
            ),
        },
        "envelopes": envelopes_out,
        # Internal-only (never on the REST wire — the REST route/service pops it before
        # returning): the uncategorized-spend LINE count, so `to_view_model` can populate
        # `Unmapped.uncategorized_count` without a second pass over transactions.
        "_uncategorized_count": uncategorized_count,
    }
