"""What the plan's shape has no place for, reported beside it (PURE). Used by ``shape``."""

from __future__ import annotations

from backend.services.actual_sync.shape_values import flag, iso, milli, text


def split_gaps(top: list[dict], effective_amount) -> list[dict]:
    """Splits whose stated amount is not the sum of their lines. Actual allows it and counts the
    lines, so the plan does too, and these go on the record for a person to see."""
    out = []
    for t in top:
        if flag(t.get("isParent")):
            stated, lines = milli(t.get("amount")), effective_amount(t)
            if stated != lines:
                out.append(
                    {
                        "id": t["id"],
                        "account_id": t["acct"],
                        "date": iso(t["date"]),
                        "parent": stated,
                        "lines": lines,
                    }
                )
    return out


def held(rows: list[dict], today: str) -> list[dict]:
    """Money held for later in this month or a coming one. An earlier hold has already come back
    to Actual's To Budget."""
    out = []
    for h in rows:
        month = text(h.get("id"), 7)
        amount = milli(h.get("buffered"))
        if amount and month >= today[:7]:
            out.append({"month": month, "amount": amount})
    return out
