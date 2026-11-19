"""The tags already in use across the ledger — what a tag picker offers to select."""

from __future__ import annotations

from backend.db.transaction_store import TransactionStore


def tag_usage() -> list[dict]:
    """Every tag on a live transaction with its count and last-used day, most used first
    (ties: most recently used, then name)."""
    usage = TransactionStore().tag_usage()
    rows = [{"tag": tag, "count": n, "last_used": last} for tag, (n, last) in usage.items()]
    rows.sort(key=lambda r: r["tag"])
    rows.sort(key=lambda r: (r["count"], r["last_used"]), reverse=True)
    return rows
