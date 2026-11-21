"""spending_breakdown — a window's spending split into RECURRING BILLS and DAY-TO-DAY spend,
set against the previous period and the user's USUAL period.

Why it exists: a report that compares "this week" with "last week" in one lump is dominated by
whatever fixed monthly bill happened to land in it. Rent paid on the 1st makes that week read
as a big jump on the week before and most of all spending, which says nothing about how the
person is actually spending. The reader needs the bills named and set aside, and the rest compared with
what is normal — the mean of the periods before, not the single one beside it.

Money is the ledger's, not a second opinion: the rows are ``kind=expense``, soft-deleted rows
are excluded by the store, and each amount is ``txn_base_amount`` — the same pair
``get_transactions`` sums into ``summary.total_expenses_base``, so ``spending.gross`` equals it
for the same window to the cent. Spending is NET of money paid back under an expense category
(a refund, a friend's share of dinner — ``spending_income``'s "paid back"), per category and
never below zero (``paid_back_beyond`` carries a repayment bigger than the window's spending
in its category, e.g. one landing a week after the purchase it repays): a 1,000 watch
bought and refunded in the month was 0 spent, not 1,000 (owner, 2026-10-06), and the Plan
screen's category activity nets it the same way. The netting happens per category in EVERY
window — this one, the prior, the usual and the year-ago — so comparisons stay like for like.

A BILL is an expense row claimed by a recurring OUT stream (``recurring_baseline``): a matured
or user-confirmed detected stream, including one that has since ended (a cancelled subscription
was still a bill in the periods it ran) — or any row in a BILLS CATEGORY, one where most of the
category's rows in the detection window were claimed by such streams. The category rule exists
because a stream needs three regular payments: rent paid to a new landlord (or by a new route)
would otherwise read as day-to-day spending for months — live, 2026-10-05, a month's rent
became the week's "day-to-day" headline. Everything else is day-to-day. Detection only claims
rows inside its own trailing window (``detection_window``), so older periods cannot be split —
``notes`` says so rather than silently calling an old rent payment day-to-day.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date

from backend.core.errors import ValidationError
from backend.db.transaction_store import txn_text
from backend.models.transaction import SPLIT
from backend.services import labels, spending_bills, spending_groups, spending_income
from backend.services import ledger as ledger_service
from backend.services import spending_periods as periods
from backend.services.fx_cache import load_fx_cache
from backend.services.recurring_baseline import resolve_baseline

DEFAULT_BASELINE_WEEKS = 8
DEFAULT_BASELINE_MONTHS = 6
MAX_BASELINE = 26
MAX_WINDOW_DAYS = 400
MAX_BILL_ITEMS = 50
LARGEST_ROWS = 5
UNSORTED = "Uncategorized"


@dataclass(frozen=True)
class _Row:
    id: str
    date: str
    amount: float
    payee: str
    account: str
    category_id: str
    category: str
    parts: tuple[tuple[str, str, float], ...]  # (category_id, label, amount) — splits expanded
    bill_cadence: str | None
    description: str = ""  # what the payment was FOR — bank text, as stored
    memo: str = ""  # the user's own note, as stored
    paid_back: bool = False  # money returning under an expense category, held as a NEGATIVE row


# ── rows ──────────────────────────────────────────────────────────────────────────


def _claims(base: str, rates: dict) -> tuple[dict[str, str], dict]:
    """({txn_id: cadence}, the baseline result) from ONE `resolve_baseline` call."""
    result = resolve_baseline(base, rates)
    streams = list(result["streams"]) + [
        c
        for c in result["candidates"]
        if c.get("status") == "ended" and c["confidence"] == "matured"
    ]
    claimed: dict[str, str] = {}
    for s in streams:
        if s.get("direction") != "out":
            continue
        for tid in s.get("txn_ids") or []:
            claimed.setdefault(tid, s["cadence"])
    return claimed, result


def _split_parts(t: dict, amount: float, cats: dict) -> tuple[tuple[str, str, float], ...]:
    """A split row's money allocated to its lines' categories by their share of the leg."""
    leg = ledger_service.allocated_leg_amount(t)
    lines = [ln for ln in t.get("splits") or [] if isinstance(ln, dict)]
    if not leg or not lines:
        return (("split", "Split", amount),)
    return tuple(
        (
            ln.get("category") or "",
            cats.get(ln.get("category") or "") or UNSORTED,
            abs(float(ln.get("amount") or 0.0)) / leg * amount,
        )
        for ln in lines
    )


def _build_row(
    t: dict, base: str, rates: dict, maps: dict, claimed: dict[str, str], bill_cats: dict
) -> _Row:
    amount = ledger_service.txn_base_amount(t, base, rates)
    cats = maps["categories"]
    cat_id = t.get("category") or ""
    if cat_id == SPLIT:
        parts = _split_parts(t, amount, cats)
        category = "Split"
    else:
        category = cats.get(cat_id) or (UNSORTED if not cat_id else "Unknown category")
        parts = ((cat_id, category, amount),)
    return _Row(
        id=t["id"],
        date=t["date"],
        amount=amount,
        payee=maps["payees"].get(t.get("payee_id") or "") or txn_text(t) or "(no description)",
        account=maps["accounts"].get(t.get("from_account_id") or "") or "",
        category_id=cat_id,
        category=category,
        parts=parts,
        bill_cadence=claimed.get(t["id"]) or bill_cats.get(cat_id),
        description=txn_text(t),
        memo=str(t.get("memo") or ""),
    )


def _paid_back_rows(upto: str, base: str, rates: dict, maps: dict, bill_cats: dict) -> list[_Row]:
    """Money that came back under an EXPENSE category — a refund, a friend's share of dinner —
    as negative rows, so every window nets it from the category it returned to, as the Plan
    screen's activity does. Earned (income-kind) and uncategorised parts are not spending."""
    earned, cats = spending_income.income_kinds(), maps["categories"]
    out = []
    for t in ledger_service.transactions.query_txns(kind="income", date_to=upto):
        amount = ledger_service.txn_base_amount(t, base, rates)
        parts = tuple(
            (cat_id, cats.get(cat_id) or "Unknown category", -part)
            for cat_id, part in spending_income.parts(t, amount)
            if cat_id and cat_id != SPLIT and cat_id not in earned
        )
        if not parts:
            continue
        cat_id = t.get("category") or ""
        out.append(
            _Row(
                id=t["id"],
                date=t["date"],
                amount=sum(p[2] for p in parts),
                payee=maps["payees"].get(t.get("payee_id") or "") or txn_text(t) or "",
                account=maps["accounts"].get(t.get("to_account_id") or "") or "",
                category_id=cat_id,
                category="Split" if cat_id == SPLIT else parts[0][1],
                parts=parts,
                bill_cadence=bill_cats.get(cat_id),  # as `_build_row`: a split is never a bill
                paid_back=True,
            )
        )
    return out


class _Book:
    """Expense rows sorted by date, sliceable by window."""

    def __init__(self, rows: list[_Row]):
        self.rows = sorted(rows, key=lambda r: r.date)
        self._dates = [r.date for r in self.rows]

    def window(self, start: date, end: date) -> list[_Row]:
        lo = bisect.bisect_left(self._dates, start.isoformat())
        hi = bisect.bisect_right(self._dates, end.isoformat())
        return self.rows[lo:hi]


def _totals(rows: list[_Row]) -> dict:
    """Each category nets what was paid back against what was spent, and never below zero: a
    repayment landing in a later window than the spending it repays left a whole week at
    "SGD −1,276.84 went out" (live weekly report, 2026-10-07). The excess is reported as
    ``paid_back_beyond`` — money back with no spending to offset in this window — so
    ``total == gross − paid_back + paid_back_beyond`` always closes."""
    net: dict[tuple[bool, str], float] = {}
    for r in rows:
        for cat_id, _, amount in r.parts:
            key = (bool(r.bill_cadence), cat_id)
            net[key] = net.get(key, 0.0) + amount
    bills = sum(max(0.0, v) for (is_bill, _), v in net.items() if is_bill)
    day_to_day = sum(max(0.0, v) for (is_bill, _), v in net.items() if not is_bill)
    return {
        "total": bills + day_to_day,
        "bills": bills,
        "day_to_day": day_to_day,
        "gross": sum(r.amount for r in rows if not r.paid_back),
        "paid_back": -sum(r.amount for r in rows if r.paid_back),
        "paid_back_beyond": sum(max(0.0, -v) for v in net.values()),
    }


def _by_category(rows: list[_Row]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        if r.bill_cadence:
            continue
        for cat_id, label, amount in r.parts:
            slot = out.setdefault(
                cat_id, {"category": label, "amount": 0.0, "count": 0, "paid_back": 0.0}
            )
            slot["amount"] += amount
            if r.paid_back:
                slot["paid_back"] -= amount
        if not r.paid_back:
            for cat_id in {p[0] for p in r.parts}:
                out[cat_id]["count"] += 1
    for slot in out.values():  # never below zero — see _totals
        slot["paid_back_beyond"] = max(0.0, -slot["amount"])
        slot["amount"] = max(0.0, slot["amount"])
    return out


# ── sections ──────────────────────────────────────────────────────────────────────


def _money(totals: dict, *, divisor: int = 1) -> dict:
    return {k: round(v / divisor, 2) for k, v in totals.items()}


def _pct(current: float, reference: float) -> float | None:
    return round((current - reference) / reference * 100, 1) if reference else None


def _bills_section(rows: list[_Row], totals: dict) -> dict:
    bills = sorted((r for r in rows if r.bill_cadence and not r.paid_back), key=lambda r: -r.amount)
    items = [
        {
            "txn_id": r.id,
            "date": r.date,
            "payee": r.payee,
            "category": r.category,
            "amount": round(r.amount, 2),
            "cadence": r.bill_cadence,
            "description": r.description,
            "memo": r.memo,
        }
        for r in bills[:MAX_BILL_ITEMS]
    ]
    return {
        "total": round(totals["bills"], 2),  # net of money paid back under a bills category
        "count": len(bills),
        "showing": len(items),
        "items": items,
    }


def _day_to_day_section(
    rows: list[_Row],
    totals: dict,
    usual_by_cat: dict[str, float],
    usual_periods: int,
    month_ago_by_cat: dict[str, float] | None,
) -> dict:
    daily = [r for r in rows if not r.bill_cadence and not r.paid_back]
    categories = []
    current = _by_category(rows)
    ids = sorted(
        set(current) | set(month_ago_by_cat or {}),
        key=lambda c: -current.get(c, {}).get("amount", 0.0),
    )
    for cat_id in ids:
        slot = current.get(cat_id) or {
            "category": "",
            "amount": 0.0,
            "count": 0,
            "paid_back": 0.0,
            "paid_back_beyond": 0.0,
        }
        usual = usual_by_cat.get(cat_id, 0.0) / usual_periods if usual_periods else 0.0
        entry = {
            "category_id": cat_id,
            "category": slot["category"],
            "amount": round(slot["amount"], 2),
            "count": slot["count"],
            "paid_back": round(slot["paid_back"], 2),
            "paid_back_beyond": round(slot.get("paid_back_beyond", 0.0), 2),
            "usual": round(usual, 2),
            "vs_usual_pct": _pct(slot["amount"], round(usual, 2)),
        }
        if month_ago_by_cat is not None:
            ago = round(month_ago_by_cat.get(cat_id, 0.0), 2)
            entry["month_ago"] = ago
            entry["vs_month_ago_pct"] = _pct(slot["amount"], ago)
        if entry["amount"] or entry["paid_back"] or entry.get("month_ago"):
            categories.append(entry)
    largest = sorted(daily, key=lambda r: -r.amount)[:LARGEST_ROWS]
    return {
        "total": round(totals["day_to_day"], 2),
        "count": len(daily),
        "by_category": categories,
        "largest": [
            {
                "txn_id": r.id,
                "date": r.date,
                "payee": r.payee,
                "category": r.category,
                "account": r.account,
                "amount": round(r.amount, 2),
                "description": r.description,
                "memo": r.memo,
            }
            for r in largest
        ],
    }


# ── entry point ───────────────────────────────────────────────────────────────────


def _usual_section(
    baseline: list[tuple[date, date]], book: _Book, requested: int, kind: str, through_day=None
):
    """The mean-per-period figures over the baseline windows, plus the rows they came from."""
    rows = [r for w in baseline for r in book.window(*w)]
    n = len(baseline)
    totals = _totals(rows)
    per_period = {k: v / max(n, 1) for k, v in totals.items()}
    section = {
        "periods": n,
        "periods_requested": requested,
        "periods_with_data": sum(1 for w in baseline if book.window(*w)),
        "from": baseline[0][0].isoformat() if baseline else None,
        "to": baseline[-1][1].isoformat() if baseline else None,
        **_money(per_period),
        "basis": periods.basis(kind, n, through_day),
    }
    return section, per_period, _cat_amounts(rows)


def _cat_amounts(rows: list[_Row]) -> dict[str, float]:
    return {k: v["amount"] for k, v in _by_category(rows).items()}


def _labelled(rows: list[_Row], labels_by_id: dict[str, str]) -> None:
    """Remember every category's label, so a category present only in the comparison period
    still has its name."""
    for r in rows:
        for cat_id, label, _ in r.parts:
            labels_by_id.setdefault(cat_id, label)


def _change(cur: dict, prior: dict, usual: dict, month_ago: dict | None) -> dict:
    out = {
        "total_vs_prior_pct": _pct(cur["total"], prior["total"]),
        "total_vs_usual_pct": _pct(cur["total"], usual["total"]),
        "day_to_day_vs_prior_pct": _pct(cur["day_to_day"], prior["day_to_day"]),
        "day_to_day_vs_usual_pct": _pct(cur["day_to_day"], usual["day_to_day"]),
    }
    if month_ago is not None:
        out["total_vs_month_ago_pct"] = _pct(cur["total"], month_ago["total"])
        out["day_to_day_vs_month_ago_pct"] = _pct(cur["day_to_day"], month_ago["day_to_day"])
    return out


def _span(window: tuple[date, date], totals: dict) -> dict:
    return {"from": window[0].isoformat(), "to": window[1].isoformat(), **_money(totals)}


def spending_breakdown(
    date_from: str,
    date_to: str,
    *,
    period: str | None = None,
    baseline_periods: int | None = None,
    include_year_ago: bool = False,
    group_by: list[str] | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    grouping = spending_groups.validate(group_by)
    start, end = periods.parse(date_from, "date_from"), periods.parse(date_to, "date_to")
    if end < start:
        raise ValidationError("date_to is before date_from")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise ValidationError(f"window longer than {MAX_WINDOW_DAYS} days")
    kind = periods.resolve_period(start, end, period)
    default_n = DEFAULT_BASELINE_MONTHS if kind == "month" else DEFAULT_BASELINE_WEEKS
    requested = max(1, min(MAX_BASELINE, int(baseline_periods or default_n)))

    base, rates = ledger_service.base_currency(), load_fx_cache()
    claimed, resolved = _claims(base, rates)
    maps = labels.label_maps(categories=True, payees=True, accounts=True)
    upto = max(end, today).isoformat()
    raw = ledger_service.transactions.query_txns(kind="expense", date_to=upto)
    bill_cats = spending_bills.bill_categories(
        raw, claimed, resolved["detection_window"]["from"], base, rates
    )
    book = _Book(
        [_build_row(t, base, rates, maps, claimed, bill_cats) for t in raw]
        + _paid_back_rows(upto, base, rates, maps, bill_cats)
    )

    cur_rows = book.window(start, end)
    cur = _totals(cur_rows)
    prior_win = periods.shift(start, end, kind, 1)
    prior = _totals(book.window(*prior_win))
    first_spend = next((r.date for r in book.rows if not r.paid_back), None)
    baseline = [
        w
        for w in (periods.shift(start, end, kind, k) for k in range(requested, 0, -1))
        if first_spend and w[1].isoformat() >= first_spend
    ]
    usual_section, usual, usual_by_cat = _usual_section(
        baseline, book, requested, kind, end.day if periods.is_month_to_date(start, end) else None
    )
    # A week is compared with the SAME week last month — the Monday-to-Sunday four weeks
    # back, so weekdays line up (owner, 2026-10-05). A month's "last month" is `prior`.
    ma_win = periods.shift(start, end, kind, 4) if kind == "week" else None
    ma_rows = book.window(*ma_win) if ma_win else []
    month_ago = _totals(ma_rows) if ma_win else None

    streams = [s for s in resolved["streams"] if s.get("direction") == "out"]
    ya_win = periods.year_ago(start, end, kind) if include_year_ago else None
    detection_from = date.fromisoformat(resolved["detection_window"]["from"])
    out = {
        "base_currency": base,
        "window": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "days": (end - start).days + 1,
            "period": kind,
        },
        "spending": {
            "total": round(cur["total"], 2),
            "gross": round(cur["gross"], 2),
            "paid_back": round(cur["paid_back"], 2),
            "paid_back_beyond": round(cur["paid_back_beyond"], 2),
            "count": sum(1 for r in cur_rows if not r.paid_back),
            "bills": _bills_section(cur_rows, cur),
            "day_to_day": _day_to_day_section(
                cur_rows,
                cur,
                usual_by_cat,
                len(baseline),
                _cat_amounts(ma_rows) if ma_win else None,
            ),
        },
        "income": spending_income.income_split(start, end, base, rates, maps),
        "prior": _span(prior_win, prior),
        "usual": usual_section,
        "change": _change(cur, prior, usual, month_ago),
        "bills_due_not_seen": spending_bills.worth_reporting(
            spending_bills.bills_due_not_seen(streams, book, start, end, today), usual["day_to_day"]
        ),
        "notes": spending_bills.breakdown_notes(
            first_spend, baseline, requested, [start, ya_win and ya_win[0]], detection_from, streams
        ),
    }
    if ma_win and month_ago is not None:
        out["month_ago"] = _span(ma_win, month_ago)
        names: dict[str, str] = {}
        _labelled(ma_rows, names)
        for entry in out["spending"]["day_to_day"]["by_category"]:
            entry["category"] = entry["category"] or names.get(entry["category_id"], UNSORTED)
    if ya_win:
        out["year_ago"] = _span(ya_win, _totals(book.window(*ya_win)))
    if grouping:
        out["groups"] = spending_groups.groups(cur_rows, grouping)
    return out
