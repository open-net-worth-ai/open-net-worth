"""What the account's Statements sheet says about each statement beyond its own record.

Every row carries the same facts whatever its state (owner, 2026-10-25: rows waiting for review
showed no date, no period and no size): how many rows the file holds, and — once something was
booked from it — when, how many transactions it added that are still in the ledger, and how many
existing entries it linked to instead of duplicating.

A statement's bookings are found two ways: the record's own `batch_ids`, and every import batch
that names it (`statement_ref_id`). Older records carry their batch ids only in the promoted
column, which the record payload never received, so the payload alone said nothing was booked.
A rolled-back batch booked nothing that is still there, so it never counts.

`added` is read from the ledger, not from the batch's own `transaction_count` (which counts only
the commit's explicit creates): a transaction later deleted or merged away is no longer "added".
"""

from __future__ import annotations

from collections import defaultdict

from backend.services import ledger as ledger_service


def batches_by_statement() -> dict[str, list[dict]]:
    """Every live import batch, keyed by the statement it came from — built once per list."""
    index: dict[str, list[dict]] = defaultdict(list)
    for batch in ledger_service.import_batches.all():
        if batch.get("statement_ref_id") and batch.get("status") != "rolled_back":
            index[batch["statement_ref_id"]].append(batch)
    return index


def import_facts(record: dict, by_statement: dict[str, list[dict]] | None = None) -> dict:
    batches = {b["id"]: b for b in (by_statement or {}).get(record.get("id") or "", [])}
    for batch_id in record.get("batch_ids") or []:
        if batch_id not in batches:
            batch = ledger_service.import_batches.find_by_id(batch_id)
            if batch and batch.get("status") != "rolled_back":
                batches[batch_id] = batch
    facts: dict = {
        "rows_read": len(record.get("import_rows") or []) or None,
        "imported_at": max((b.get("created_at") or "" for b in batches.values()), default="")
        or record.get("filed_at"),
        "added": None,
        "linked": None,
    }
    if batches:
        facts["added"] = sum(
            1
            for b in batches.values()
            for t in ledger_service.transactions.query_by_batch(b["id"], include_deleted=False)
            if not t.get("merged_into")
        )
        facts["linked"] = sum(len(b.get("merges") or []) for b in batches.values())
    return facts
