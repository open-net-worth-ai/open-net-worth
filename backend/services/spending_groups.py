"""The `groups` section of ``spending_breakdown``: each category broken down by month and by
payee, and each payee across the categories it was booked under.

It reads the SAME rows the breakdown totals: a split row's lines are already in their own
categories (``_Row.parts``), money paid back is already a negative row, bills and day-to-day
spending alike. Asked for month-vs-month by category with payees, Lucius once had no tool for
it, grouped ``transactions.category`` in raw SQL, and lost every split line, since a split row's
own category is ``split`` (live, 2026-11-18).

Every cell is net of money paid back and never below zero, like ``_totals``. A category's
``amount`` nets bills and day-to-day separately, exactly as ``spending.total`` does, so the
categories always add up to it; its months and payees are clamped cell by cell, so a refund
landing in a later month than its purchase can leave them adding up to more.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from backend.core.errors import ValidationError

GROUPINGS = ("month", "payee")
MAX_PAYEES = 15


def validate(group_by: Iterable[str] | None) -> tuple[str, ...]:
    chosen = tuple(dict.fromkeys(group_by or ()))
    unknown = [g for g in chosen if g not in GROUPINGS]
    if unknown:
        raise ValidationError(f"group_by accepts {', '.join(GROUPINGS)}; got {', '.join(unknown)}")
    return chosen


def _cells(rows, key) -> dict:
    """{(cat_id, key(row)): net amount} over every part of every row."""
    net: dict = defaultdict(float)
    for r in rows:
        for cat_id, _, amount in r.parts:
            net[(cat_id, key(r))] += amount
    return net


def _capped(entries: list[dict]) -> tuple[list[dict], dict | None]:
    entries.sort(key=lambda e: -e["amount"])
    shown, rest = entries[:MAX_PAYEES], entries[MAX_PAYEES:]
    other = {"amount": round(sum(e["amount"] for e in rest), 2), "payees": len(rest)}
    return shown, (other if rest else None)


def _payee_counts(rows) -> dict:
    counts: dict = defaultdict(int)
    for r in rows:
        if not r.paid_back:
            for cat_id in {p[0] for p in r.parts}:
                counts[(cat_id, r.payee)] += 1
    return counts


def _by_category(rows, chosen: tuple[str, ...], labels: dict) -> list[dict]:
    totals = _cells(rows, lambda r: bool(r.bill_cadence))
    months = _cells(rows, lambda r: r.date[:7]) if "month" in chosen else {}
    payees = _cells(rows, lambda r: r.payee) if "payee" in chosen else {}
    counts = _payee_counts(rows) if payees else {}
    out = []
    for cat_id in {c for c, _ in totals}:
        entry = {
            "category_id": cat_id,
            "category": labels[cat_id],
            "amount": round(sum(max(0.0, v) for (c, _), v in totals.items() if c == cat_id), 2),
        }
        if not entry["amount"]:
            continue
        if "month" in chosen:
            entry["months"] = [
                {"month": m, "amount": round(max(0.0, v), 2)}
                for (c, m), v in sorted(months.items())
                if c == cat_id and v > 0
            ]
        if "payee" in chosen:
            shown, other = _capped(
                [
                    {"payee": p, "amount": round(v, 2), "count": counts.get((c, p), 0)}
                    for (c, p), v in payees.items()
                    if c == cat_id and v > 0
                ]
            )
            entry["payees"] = shown
            if other:
                entry["payees_other"] = other
        out.append(entry)
    return sorted(out, key=lambda e: -e["amount"])


def _by_payee(rows, labels: dict) -> tuple[list[dict], dict | None]:
    per: dict = defaultdict(list)
    for (cat_id, payee), v in _cells(rows, lambda r: r.payee).items():
        if v > 0:
            per[payee].append({"category": labels[cat_id], "amount": round(v, 2)})
    entries = [
        {
            "payee": payee,
            "amount": round(sum(c["amount"] for c in cats), 2),
            "categories": sorted(cats, key=lambda c: -c["amount"]),
        }
        for payee, cats in per.items()
    ]
    return _capped(entries)


def groups(rows, chosen: tuple[str, ...]) -> dict:
    labels: dict[str, str] = {}
    for r in rows:
        for cat_id, label, _ in r.parts:
            labels.setdefault(cat_id, label)
    out: dict = {"by": list(chosen), "by_category": _by_category(rows, chosen, labels)}
    if "payee" in chosen:
        out["by_payee"], other = _by_payee(rows, labels)
        if other:
            out["by_payee_other"] = other
    return out
