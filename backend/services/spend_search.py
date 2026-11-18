"""Free-text spend search bucketed into weeks — the `lux-coffee` widget's shape.

The widget fetched matching transactions and summed them into Monday-start weeks in the
browser. The search itself was already a server filter (ledger's cross-store payee/category
join); only the weekly fold was client-side. It runs here now, so the bars and the "avg
$X/wk" figure come from the same place as every other Overview number.
"""

from __future__ import annotations

from datetime import date, timedelta

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services import payee_registry
from backend.services.fx_cache import load_fx_cache

DEFAULT_WEEKS = 6
MAX_WEEKS = 52


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def spend_search(query: str, weeks: int = DEFAULT_WEEKS) -> dict:
    """Expense transactions matching `query` (description/payee/memo/category), summed into
    Monday-start weeks. `a|b` matches either word. An empty query returns empty weeks rather
    than the whole book.
    `average` divides by COMPLETED weeks only (the last bucket, Monday through today, is
    always the still-open current week) — falling back to that partial bucket's own total
    when there are no completed weeks yet, never a division by zero."""
    weeks = max(1, min(int(weeks), MAX_WEEKS))
    today = user_today()
    first_monday = _monday_of(today) - timedelta(weeks=weeks - 1)
    buckets = [
        {
            "key": (first_monday + timedelta(weeks=i)).isoformat(),
            "start": (first_monday + timedelta(weeks=i)).isoformat(),
            "end": (first_monday + timedelta(weeks=i, days=6)).isoformat(),
            "amount": 0.0,
        }
        for i in range(weeks)
    ]

    term = (query or "").strip()
    # `a|b|c` matches any of the words: one word missed most of what it was named for ("coffee"
    # never matched an "Espresso" bar or a "Kopi" stall).
    needles = [n for n in (part.strip().lower() for part in term.split("|")) if n]
    if needles:
        base = base_currency()
        rates = load_fx_cache()
        payee_names = payee_registry.payee_names()
        cat_labels = {
            c["id"]: c.get("label", "")
            for c in categories_service.get_all_categories(include_hidden=True)
        }
        window_start = first_monday.isoformat()
        window_end = today.isoformat()
        for t in ledger_service.transactions.query_txns(kind=["expense"]):
            when = (t.get("date") or "")[:10]
            if not (window_start <= when <= window_end):
                continue
            if not any(
                ledger_service.txn_matches_q(t, needle, "any", payee_names, cat_labels)
                for needle in needles
            ):
                continue
            for bucket in buckets:
                if bucket["start"] <= when <= bucket["end"]:
                    bucket["amount"] += abs(ledger_service.txn_base_amount(t, base, rates))
                    break

    for bucket in buckets:
        bucket["amount"] = round(bucket["amount"], 2)
    total = round(sum(b["amount"] for b in buckets), 2)
    completed = buckets[:-1]
    completed_total = round(sum(b["amount"] for b in completed), 2)
    average = round(completed_total / len(completed), 2) if completed else total
    return {
        "base_currency": base_currency(),
        "query": term,
        "weeks": buckets,
        "total": total,
        "average": average,
        "max": round(max((b["amount"] for b in buckets), default=0.0), 2),
    }
