"""How early the owner usually pays each card, so a bill reminder lands before their habit.

A flat "due within N days" window reminds someone who pays ten days early only after they would
normally have paid. The window is therefore the LARGER of a one-week floor and how many days
before the due date they paid, read from their own last few statements: last cycle's lead and the
average lead, whichever is earlier in the month. Read-only; derived, never stored.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

REMIND_FLOOR_DAYS = 7
HABIT_CYCLES = 3
# A payment this many days after the due date still belongs to that cycle (paid late, lead 0).
LATE_SLACK_DAYS = 3
# With no printed statement date, a cycle's payments are looked for this far before its due date.
DEFAULT_CYCLE_DAYS = 25


def _parse(value: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _past_cycles(account_id: str, records: list[dict], today: date) -> list[tuple[date, date]]:
    """(statement date, due date) of this card's printed statements already due, newest first."""
    by_due: dict[date, date] = {}
    for r in records:
        if r.get("account_id") != account_id and account_id not in (r.get("account_ids") or []):
            continue
        meta = r.get("import_meta") or {}
        due = _parse(meta.get("payment_due_date"))
        if due is None or due >= today:
            continue
        opened = _parse(meta.get("statement_date")) or _parse(meta.get("period_end"))
        by_due[due] = opened or due - timedelta(days=DEFAULT_CYCLE_DAYS)
    return [(by_due[d], d) for d in sorted(by_due, reverse=True)][:HABIT_CYCLES]


def _first_payment(account_id: str, txns: list[dict], after: date, until: date) -> Optional[date]:
    """The earliest payment (a transfer INTO the card) dated after ``after`` through ``until``."""
    dates = [
        d
        for t in txns
        if not t.get("deleted")
        and not t.get("merged_into")
        and t.get("kind") == "transfer"
        and t.get("to_account_id") == account_id
        and (d := _parse(t.get("date"))) is not None
        and after < d <= until
    ]
    return min(dates) if dates else None


def payment_habit(account_id: str, today: date, *, records: list[dict], txns: list[dict]) -> dict:
    """``lead_days_last`` / ``lead_days_avg``: days before the due date the card was paid (last
    cycle / mean of up to three). ``remind_days_before_due``: the reminder window, never under
    a week. Cycles with no payment found are skipped; none at all leaves the one-week floor."""
    leads: list[int] = []
    for opened, due in _past_cycles(account_id, records, today):
        paid = _first_payment(account_id, txns, opened, due + timedelta(days=LATE_SLACK_DAYS))
        if paid is not None:
            leads.append(max(0, (due - paid).days))
    last = leads[0] if leads else None
    avg = math.ceil(sum(leads) / len(leads)) if leads else None
    return {
        "lead_days_last": last,
        "lead_days_avg": avg,
        "cycles_read": len(leads),
        "remind_days_before_due": max(REMIND_FLOOR_DAYS, last or 0, avg or 0),
    }


def load_payment_habit(account_id: str, today: date) -> dict:
    """``payment_habit`` read from the uploaded statements and the live ledger."""
    from backend.db.transaction_store import TransactionStore
    from backend.services.card_statements import _statement_records

    return payment_habit(
        account_id, today, records=_statement_records(), txns=TransactionStore().all()
    )
