"""The stored shape of a migration run's report: counts per item, problems with a plain-language
hint, and verification with its mismatches apart from its matches (what the report screen reads)."""

from __future__ import annotations

from collections import Counter

from backend.services.budget_app import current

_HINTS = {
    "failed": "Open Net Worth refused this row. Fix the cause in {app}, or add the transaction by "
    "hand.",
    "refused_duplicate": "It looks like a transaction already in the book. If it is a separate "
    "one, add it by hand.",
    "previously_deleted": "You deleted this transaction here earlier, so it was not booked again. "
    "Restore it from Accounts if you want it back.",
    "assigned": "Set that month's Assigned by hand in Plan.",
}


def count(created: int = 0, existing: int = 0, refused: int = 0, failed: int = 0) -> dict:
    return {"created": created, "existing": existing, "refused": refused, "failed": failed}


def transaction_counts(tally: Counter) -> dict:
    """A row matched to one already in a linked account is already there, like a re-run's."""
    return count(
        created=tally["created"],
        existing=tally["already_present"] + tally["matched"],
        refused=tally["refused_duplicate"] + tally["previously_deleted"],
        failed=tally["failed"],
    )


def _n(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else many or one + 's'}"


def transactions_line(tally: Counter) -> str:
    """What booking the transactions did, as one sentence for the run's log."""
    here = tally["already_present"] + tally["matched"]
    parts = [f"{_n(tally['created'], 'transaction')} added"]
    if here:
        parts.append(f"{here:,} {'was' if here == 1 else 'were'} already here")
    if refused := tally["refused_duplicate"] + tally["previously_deleted"]:
        parts.append(f"{refused:,} {'was' if refused == 1 else 'were'} left out (see the problems)")
    if tally["failed"]:
        parts.append(f"{_n(tally['failed'], 'transaction')} could not be added")
    return "; ".join(parts) + "."


def categories_line(made: Counter) -> str:
    added = [
        _n(made[k], one, many)
        for k, one, many in (
            ("categories", "category", "categories"),
            ("envelopes", "envelope", None),
            ("groups", "group", None),
        )
        if made[k]
    ]
    if not added:
        return "All the categories and envelopes were already here."
    return f"Added {_join(added)}; the rest were already here."


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def starter_line(removed: Counter) -> str:
    gone = [
        _n(removed[k], one, many)
        for k, one, many in (
            ("categories", "category", "categories"),
            ("envelopes", "envelope", None),
            ("groups", "group", None),
        )
        if removed[k]
    ]
    return (
        f"Removed unused starter items: {_join(gone)}."
        if gone
        else "No unused starter items to remove."
    )


def problem(raw: dict) -> dict:
    """``raw`` is what ``apply`` collected: ``{ref, reason, kind}``."""
    return {
        "item": raw["ref"],
        "reason": raw.get("reason") or "",
        "hint": _HINTS.get(raw.get("kind", ""), "").format(app=current().name),
    }


def _check(row: dict) -> dict:
    expected, actual = row["expected"], row["actual"]
    numeric = all(isinstance(v, (int, float)) for v in (expected, actual))
    return {
        "check": row["label"],
        "unit": row.get("unit", "money") if numeric else "text",
        "ynab_value": expected,
        "onw_value": actual,
        "difference": round(actual - expected, 2) if numeric else None,
        "note": row.get("note") or "",
    }


def verification(raw: dict) -> dict:
    mismatches = [_check(r) for r in raw["rows"] if not r["ok"]]
    matches = [_check(r) for r in raw["rows"] if r["ok"]]
    return {
        "ok": raw["ok"],
        "checks_total": len(raw["rows"]),
        "checks_matching": len(matches),
        "mismatches": mismatches,
        "matches": matches,
    }


def outcome(kind: str, run: dict) -> str:
    """One line for the history list."""
    ver, preview, counts = run["verification"], run["preview"], run["counts"]
    check = (
        f"{ver['checks_matching']} of {ver['checks_total']} checks match" if ver is not None else ""
    )
    if kind == "verify":
        return check
    if kind == "preview":
        return (
            ""
            if preview is None
            else (
                f"Would bring across {len(preview['accounts'])} accounts and "
                f"{preview['transactions']} transactions"
            )
        )
    made = f"{counts['accounts']['created']} accounts, {counts['transactions']['created']} transactions"
    return f"Created {made}; {check}" if check else f"Created {made}"
