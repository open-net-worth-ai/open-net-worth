"""Undo the last sync here: what the last run that changed this book did to its transactions,
put back. The run's own audit events (its surface, between the marks the runner records) say
what it did: a row it created is deleted, a row it changed gets back the values it had, a row
it deleted comes back. A row anyone has touched since is left alone and named. The sync is
switched off afterwards, so nothing is sent until the person turns it back on: then the rows put
back here are sent to the app like any change made here. The app's own side is undone with a
restore point (``actual_sync.restore_points``); Assigned, categories and Starting Balances are
not part of this."""

from __future__ import annotations

import logging

from backend.core import audit_context
from backend.core.errors import ConflictError, OpenNetWorthError, ValidationError
from backend.db.transaction_audit_store import TransactionAuditStore
from backend.services import ledger as ledger_service
from backend.services import rejected_identities
from backend.services.budget_app import current
from backend.services.ynab_sync import state as sync_state

logger = logging.getLogger(__name__)

# What an audit "before" may hold that an edit doesn't take back.
_NOT_EDITABLE = {"id", "created_at", "updated_at", "deleted", "deleted_at", "batch_id", "seq"}


def _put_back(event: dict) -> None:
    txn_id, op = event["txn_id"], event.get("op")
    if op == "create":
        ledger_service.bulk_delete_transactions([txn_id])
    elif op == "update":
        patch = {k: v for k, v in (event.get("before") or {}).items() if k not in _NOT_EDITABLE}
        if patch:
            ledger_service.update_transaction(txn_id, patch)
    elif op == "delete":
        row = ledger_service.transactions.find_by_id(txn_id)
        if row and row.get("deleted"):
            ledger_service.transactions.update_by_id(txn_id, {"deleted": False})
            rejected_identities.forget_for_txns([txn_id])


def undo_last() -> dict:
    """``{"put_back", "skipped": [titles], "sync_off"}``. Refused while a sync runs, and when no
    run has changed this book since the last undo."""
    app = current()
    st = sync_state.load()
    mark = st.get("undo")
    if not mark:
        raise ValidationError("No sync has changed this book since the last undo.")
    if not app.lock.acquire(blocking=False):
        raise ConflictError(f"{app.article} {app.name} sync is running; undo when it finishes.")
    token = audit_context.set_audit_scope("user", "ui", note=f"undo of a {app.name} sync")
    try:
        store = TransactionAuditStore()
        events = store.of_surface(mark["from"], upto=mark["to"], surface=app.surface)
        ids = {e["txn_id"] for e in events}
        later = store.touched_after(mark["to"], ids)
        put_back, skipped = 0, []
        for txn_id in sorted(ids - later):
            mine = [e for e in events if e["txn_id"] == txn_id]
            try:
                for event in reversed(mine):
                    _put_back(event)
                put_back += 1
            except OpenNetWorthError as exc:
                logger.info("undo of %s refused: %s", txn_id, exc)
                skipped.append(_title(txn_id))
        skipped += [_title(t) for t in sorted(later)]
        st = sync_state.load()
        st["undo"], st["enabled"] = None, False
        sync_state.save(st)
        return {"put_back": put_back, "skipped": skipped, "sync_off": True}
    finally:
        audit_context.clear_audit_scope(token)
        app.lock.release()


def _title(txn_id: str) -> str:
    row = ledger_service.transactions.find_by_id(txn_id) or {}
    return (
        f"{row.get('date') or ''} {(row.get('description') or '').strip() or '(no payee)'}".strip()
    )
