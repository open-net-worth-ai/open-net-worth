"""Undo one sync run (ADR 2026-11-13, Amendment 13).

A run that books or removes anything records an import batch under the id its rows already carry
(``bank_sync:{run_id}``), the way a statement import does, so ``import_undo.undo_import`` has ONE
implementation for both: the run's new rows are removed, the rows it folded a card payment into
go back to what they were (only the fields the promotion changed, and only while they are
still as it left them: a later edit or delete of yours stands), the pending bookings the bank dropped come back (``deleted_ids``) and
are tracked again (``pending_entries``). A pending booking a settled row REPLACED stays gone: its
successor's ref is held, and bringing both back would count the spend twice. What a batch can't
hold, this adds:

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
            "promotions": result.promoted,
            "deleted_ids": result.removed_ids,
            "pending_entries": result.removed_entries,
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


def keep_openings(run_id: str, before: dict[str, dict]) -> bool:
    """File the Starting Balances this run moved on its batch. Best effort: a failure leaves the
    run undoable, with those balances not restored (False)."""
    try:
        now = openings(set(before))
        moved = {a: b for a, b in before.items() if now.get(a) != b}
        if moved and ledger_service.import_batches.find_by_id(batch_id(run_id)):
            ledger_service.import_batches.update_by_id(
                batch_id(run_id), {"opening_restores": moved}
            )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("bank_data_sync: run %s openings not kept for Undo", run_id)
        return False


def _groups(provider: str, txns: list[dict]) -> list[list[str]]:
    """The refs of each row, as one group per row: a card payment filed as a transfer holds the
    bank's two rows, which are re-booked together."""
    out = []
    for t in txns:
        refs = [
            str(e.get("ref") or "")
            for e in t.get("external_refs") or []
            if e.get("source") == provider
        ]
        if t.get("kind") == "transfer" and len(refs) >= 2:
            out.append(refs[:2])
        else:
            out.extend([r] for r in refs)
    return out


def _check_undoable(provider: str, run_id: str) -> dict:
    run = import_runs.get(run_id)
    if run.get("provider") != provider or run.get("kind") != "sync":
        raise NotFoundError("That run doesn't exist.")
    batch = ledger_service.import_batches.find_by_id(batch_id(run_id))
    if batch is None:
        raise ConflictError("This sync booked and removed nothing, so there is nothing to undo.")
    if batch.get("status") == "rolled_back":
        raise ConflictError("This sync is already undone.")
    ordered = ledger_service.import_batches.all()  # in the order they were recorded
    after = ordered[[b.get("id") for b in ordered].index(batch["id"]) + 1 :]
    later = [b for b in after if b.get("provider") == provider and b.get("status") == "active"]
    if later:
        raise ConflictError("A later sync changed your book too. Undo that one first.")
    return batch


def _hold(provider: str, run_id: str, batch: dict, groups: list[list[str]]) -> int:
    """One open ``duplicate`` item per undone row (the bank's row, to Book again or Dismiss); a
    card payment's two rows are ONE item, so they come back as one transfer."""
    views = batch.get("bank_rows") or {}
    held = 0
    seen: set[str] = set()
    for group in groups:
        group = [r for r in dict.fromkeys(group) if r and r not in seen]
        seen.update(group)
        found = [(r, views[r]) for r in group if r in views]
        if len(found) < len(group):
            logger.warning("bank_data_sync: undone refs %s have no bank row to offer", group)
        if not found:
            continue
        found.sort(key=lambda rv: float(rv[1].get("amount") or 0))  # the outflow first
        ref, view = found[0]
        detail = {"row": view, "reason": UNDONE_REASON, "undone": True}
        if len(found) == 2:
            detail |= {"pair": found[1][1], "refs": [ref, found[1][0]]}
        reviews.record(
            provider,
            "duplicate",
            ref,
            run_id=run_id,
            account_id=view.get("linked_account_id"),
            detail=detail,
        )
        held += 1
    return held


def _restore_openings(batch: dict) -> tuple[int, list[str]]:
    """Put back the Starting Balances the run moved. Returns how many, and a plain sentence for
    each account whose start couldn't be put back."""
    restored = 0
    failed: list[str] = []
    for account_id, was in (batch.get("opening_restores") or {}).items():
        try:
            ledger_service.set_account_opening(account_id, was["amount"], as_of=was["as_of"])
            restored += 1
        except Exception:  # noqa: BLE001 — the rows are undone; say so rather than fail
            logger.exception("bank_data_sync: opening of %s not restored", account_id)
            account = ledger_service.accounts.find_by_id(account_id) or {}
            failed.append(
                f"{account.get('name') or 'An account'}'s start couldn't be put back, "
                "so check its balance."
            )
    return restored, failed


def _tracked_again(batch: dict) -> dict[str, dict]:
    """The pending bookings Undo brought back, tracked again so the bank settles or drops them as
    it would any pending row (an untracked uncleared row would sit there for good)."""
    out = {}
    for ref, entry in (batch.get("pending_entries") or {}).items():
        txn = ledger_service.transactions.find_by_id(entry.get("txn_id") or "")
        if txn and not txn.get("deleted"):
            out[ref] = entry
    return out


def _revert_promotions(batch: dict) -> tuple[list[list[str]], list[str]]:
    """Put back each row the run folded a card payment into, changing ONLY the fields the
    promotion changed and only while they are still as it left them. A row you deleted since
    stays deleted (it is not brought back past the ledger's memory of deletions); one whose
    promoted fields you edited is left as it is. Returns the bank refs of the rows reverted (held
    for review) and a plain sentence for each row left alone."""
    reverted: list[list[str]] = []
    left: list[str] = []
    for p in batch.get("promotions") or []:
        live = ledger_service.transactions.find_by_id(p["txn_id"])
        if live is None or live.get("deleted"):
            continue
        changed = p["changed"]
        if any(live.get(k) != c["after"] for k, c in changed.items()):
            name = live.get("description") or "A transaction"
            left.append(f"{name} was left as the transfer it became, because you changed it since.")
            continue
        ledger_service.transactions.upsert({**live, **{k: c["before"] for k, c in changed.items()}})
        reverted.append(p["refs"])
    return reverted, left


def _mark_undone(run_id: str, warnings: list[str]) -> None:
    run = import_runs.get(run_id)
    counts = {**(run.get("counts") or {}), "undone": 1}
    outcome = f"{run.get('outcome') or 'Sync'} (undone)"
    import_runs.update(
        run_id,
        counts=counts,
        outcome=outcome,
        warnings=[*(run.get("warnings") or []), *warnings],
    )


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
        groups = _groups(provider, rows)
        token = audit_context.set_audit_scope("user", booker.SURFACE, note=f"undo run:{run_id}")
        try:
            result = import_undo.undo_import(bid)
            restored_openings, opening_failures = _restore_openings(batch)
            reverted, not_reverted = _revert_promotions(batch)
            not_reverted += opening_failures
        finally:
            audit_context.clear_audit_scope(token)
        gone = {r["id"] for r in rows}
        entries = {
            k: e for k, e in pending.tracked(provider).items() if e.get("txn_id") not in gone
        }
        entries |= _tracked_again(batch)
        pending.save(provider, entries)
        held = _hold(provider, run_id, batch, groups + reverted)
        _mark_undone(run_id, not_reverted)
        data_events.publish(["*"])
    return {
        "run_id": run_id,
        "removed": result["deleted"],
        "restored": result["restored"] + len(reverted),
        "not_reverted": not_reverted,
        "held_for_review": held,
        "openings_restored": restored_openings,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
