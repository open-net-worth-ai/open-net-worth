"""Undo one import batch — the WHOLE import, one implementation for every caller.

It soft-deletes the transactions the import created, puts every transaction it merged into back
to its exact pre-import snapshot (a decision commit records them on the batch), and marks the
batch ``rolled_back`` with the ids it deleted, so a later restore un-deletes only those. Lucius's
``rollback_import_batch`` tool, the REST rollback and the import review's **Undo import** all call
this; the REST rollback used to delete the created rows only, leaving every linked entry merged.

Rollback is not a rejection: nothing is written to ``rejected_identities``, so the same statement
can be reviewed and booked again (ADR 2026-09-02-a-deleted-identity-is-remembered).
"""

from __future__ import annotations

import logging

from backend.core.errors import ConflictError, NotFoundError
from backend.services import ledger as ledger_service

logger = logging.getLogger(__name__)


def undo_import(batch_id: str) -> dict:
    batch = ledger_service.import_batches.find_by_id(batch_id)
    if batch is None:
        raise NotFoundError("Import batch not found")
    if batch.get("status") == "rolled_back":
        raise ConflictError("This import is already undone")
    txns = ledger_service.transactions.query_by_batch(batch_id, include_deleted=False)
    deleted, _ = ledger_service.transactions.bulk_soft_delete([t["id"] for t in txns])
    ledger_service.import_batches.set_status(batch_id, "rolled_back")
    ledger_service.import_batches.set_rolled_back_txn_ids(batch_id, list(deleted))
    unmerged = 0
    for snap in batch.get("merges") or []:
        before = snap.get("before") or {}
        if before.get("id"):
            ledger_service.transactions.upsert(dict(before))
            unmerged += 1
    logger.info("undo_import: batch=%s deleted=%d unmerged=%d", batch_id, len(deleted), unmerged)
    return {
        "batch_id": batch_id,
        "statement_id": batch.get("statement_ref_id"),
        "deleted": len(deleted),
        "unmerged": unmerged,
    }
