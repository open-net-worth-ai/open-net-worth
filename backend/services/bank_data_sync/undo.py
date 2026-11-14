"""Undo one sync run (ADR 2026-11-13, Amendment 13).

A run that books or removes anything records an import batch under the id its rows already carry
(``bank_sync:{run_id}``), the way a statement import does, so ``import_undo.undo_import`` has ONE
implementation for both: the run's new rows are removed, the rows it folded a card payment into
go back to what they were (``merges``), the pending bookings it took off the book come back
(``deleted_ids``). What a batch can't hold, this adds:

* ``bank_rows`` — the bank's own view of each ref the run booked. Undone rows are not booked
  again silently (that would make Undo pointless for a bad run — duplicates, say): each ref is
  held back as an open ``duplicate`` review item (``detail.undone``) with **Book again** and
  **Dismiss**, so the next sync neither re-books nor forgets them.
* ``opening_restores`` — an account's Starting Balance the run moved (the first import's
  alignment, the bank's balance), put back so the balance doesn't change by what Undo removes.

Not undone, by design: a pending row the bank settled (cleared and re-dated — no money moved), and
a transaction you edited since (Undo removes the run's rows as they are now). Only the most recent
run that changed anything can be undone: an older run's rows may since have been folded into a
later one."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from backend.core import audit_context, data_events
from backend.core.errors import ConflictError, NotFoundError
from backend.services import import_runs, import_undo
from backend.services import ledger as ledger_service
from backend.services.bank_data_sync import booker, pending, reviews

logger = logging.getLogger(__name__)

UNDONE_REASON = "You undid the sync that booked this, so it was not booked again."


def batch_id(run_id: str) -> str:
    return f"bank_sync:{run_id}"


def record(provider: str, run_id: str, label: str, result: booker.Booked) -> bool:
    """Open the run's batch when it changed the book. False when it changed nothing."""
    if not (result.created or result.removed_ids or result.promoted):
        return False
    batches = ledger_service.import_batches
    batches.create_batch(
        description=f"{label} sync",
        transaction_count=len(result.created),
        batch_id=batch_id(run_id),
    )
    batches.update_by_id(
        batch_id(run_id),
        {
            "provider": provider,
            "run_id": run_id,
            "merges": result.promoted,
            "deleted_ids": result.removed_ids,
            "bank_rows": result.booked_views,
        },
    )
    return True


def openings(account_ids: set[str]) -> dict[str, dict]:
    """Each account's Starting Balance now — read before a run that may move it."""
    txns = ledger_service.transactions.all()
    out: dict[str, dict] = {}
    for account_id in account_ids:
        account = ledger_service.accounts.find_by_id(account_id)
        if account is not None:
            out[account_id] = {
                "amount": ledger_service.opening_amount(account, txns),
                "as_of": ledger_service.opening_date(account, txns),
            }
    return out


def keep_openings(run_id: str, before: dict[str, dict]) -> None:
    """File the Starting Balances this run moved on its batch. Best effort: a failure leaves the
    run undoable, with those balances not restored."""
    try:
        now = openings(set(before))
        moved = {a: b for a, b in before.items() if now.get(a) != b}
        if moved and ledger_service.import_batches.find_by_id(batch_id(run_id)):
            ledger_service.import_batches.update_by_id(
                batch_id(run_id), {"opening_restores": moved}
            )
    except Exception:  # noqa: BLE001
        logger.exception("bank_data_sync: run %s openings not kept for Undo", run_id)


def _refs(provider: str, txns: list[dict]) -> list[str]:
    return [
        str(e.get("ref") or "")
        for t in txns
        for e in t.get("external_refs") or []
        if e.get("source") == provider
    ]


def _check_undoable(provider: str, run_id: str) -> dict:
    run = import_runs.get(run_id)
    if run.get("provider") != provider or run.get("kind") != "sync":
        raise NotFoundError("That run doesn't exist.")
    batch = ledger_service.import_batches.find_by_id(batch_id(run_id))
    if batch is None:
        raise ConflictError("This sync booked and removed nothing, so there is nothing to undo.")
    if batch.get("status") == "rolled_back":
        raise ConflictError("This sync is already undone.")
    later = [
        b
        for b in ledger_service.import_batches.all()
        if b.get("provider") == provider
        and b.get("status") == "active"
        and (b.get("created_at") or "") > (batch.get("created_at") or "")
    ]
    if later:
        raise ConflictError("A later sync changed your book too. Undo that one first.")
    return batch


def _hold(provider: str, run_id: str, batch: dict, refs: list[str]) -> int:
    """One open ``duplicate`` item per undone ref (the bank's row, to Book again or Dismiss)."""
    views = batch.get("bank_rows") or {}
    held = 0
    for ref in dict.fromkeys(refs):
        view = views.get(ref)
        if not ref or not view:
            logger.warning("bank_data_sync: undone ref %s has no bank row to offer", ref)
            continue
        reviews.record(
            provider,
            "duplicate",
            ref,
            run_id=run_id,
            account_id=view.get("linked_account_id"),
            detail={"row": view, "reason": UNDONE_REASON, "undone": True},
        )
        held += 1
    return held


def _restore_openings(batch: dict) -> int:
    restored = 0
    for account_id, was in (batch.get("opening_restores") or {}).items():
        try:
            ledger_service.set_account_opening(account_id, was["amount"], as_of=was["as_of"])
            restored += 1
        except Exception:  # noqa: BLE001 — the rows are undone; say so rather than fail
            logger.exception("bank_data_sync: opening of %s not restored", account_id)
    return restored


def _mark_undone(run_id: str) -> None:
    run = import_runs.get(run_id)
    counts = {**(run.get("counts") or {}), "undone": 1}
    outcome = f"{run.get('outcome') or 'Sync'} (undone)"
    import_runs.update(run_id, counts=counts, outcome=outcome)


@contextmanager
def _exclusive(provider: str):
    from backend.services.bank_data_sync import runner

    lock = runner.slot(provider)
    if not lock.acquire(blocking=False):
        raise ConflictError("A sync is running. Undo this one when it has finished.")
    try:
        yield
    finally:
        lock.release()


def undo_run(provider: str, run_id: str) -> dict:
    """Undo ``run_id`` of ``provider`` as the user's act. See the module docstring."""
    with _exclusive(provider):
        batch = _check_undoable(provider, run_id)
        bid = batch_id(run_id)
        rows = ledger_service.transactions.query_by_batch(bid, include_deleted=False)
        refs = _refs(provider, rows) + [r for m in batch.get("merges") or [] for r in m["refs"]]
        token = audit_context.set_audit_scope("user", booker.SURFACE, note=f"undo run:{run_id}")
        try:
            result = import_undo.undo_import(bid)
            restored_openings = _restore_openings(batch)
        finally:
            audit_context.clear_audit_scope(token)
        gone = {r["id"] for r in rows}
        entries = {
            k: e for k, e in pending.tracked(provider).items() if e.get("txn_id") not in gone
        }
        pending.save(provider, entries)
        held = _hold(provider, run_id, batch, refs)
        _mark_undone(run_id)
        data_events.publish(["*"])
    return {
        "run_id": run_id,
        "removed": result["deleted"],
        "restored": result["restored"] + result["unmerged"],
        "held_for_review": held,
        "openings_restored": restored_openings,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
