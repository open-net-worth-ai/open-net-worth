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
from backend.services import audit, rejected_identities
from backend.services import ledger as ledger_service
from backend.services.budget_app import current, refuse_while_others_run
from backend.services.ynab_sync import state as sync_state

logger = logging.getLogger(__name__)

# What an audit "before" may hold that an edit doesn't take back.
_NOT_EDITABLE = {"id", "created_at", "updated_at", "deleted", "deleted_at", "batch_id", "seq"}


def changes(events: list[dict]) -> list[dict]:
    """A run's events that changed what a row says: not the app's ids attached to a row it sent,
    which is bookkeeping, not a change anyone would undo."""
    return [
        e
        for e in events
        if e.get("op") != "update" or set(e.get("before") or {}) - {"external_refs", "updated_at"}
    ]


def _put_back(event: dict) -> None:
    txn_id, op = event["txn_id"], event.get("op")
    if op == "create":
        # A rollback, not a rejection: the app's row may come back here later.
        ledger_service.bulk_delete_transactions([txn_id], reject_identities=False)
    elif op == "update":
        patch = {k: v for k, v in (event.get("before") or {}).items() if k not in _NOT_EDITABLE}
        if patch:
            # The values as they were stored: no payee default or Automation fills a blank.
            ledger_service.update_transaction(txn_id, patch, labels_as_given=True)
    elif op == "delete":
        row = ledger_service.transactions.find_by_id(txn_id)
        if row and row.get("deleted"):
            ledger_service.transactions.update_by_id(txn_id, {"deleted": False})
            rejected_identities.forget_for_txns([txn_id])
            # Audited like any restore, so the next sync sees it as a change made here.
            audit.record_txn_event(
                "restore", txn_id, before={"deleted": True}, after={"deleted": False}
            )


def undo_last() -> dict:
    """``{"put_back", "skipped": [titles], "partly": [titles], "sync_off"}``: ``skipped`` were
    changed by someone since and stay as they are; ``partly`` failed partway (a category or
    account gone since) and keep what was put back. Refused while a sync or an import runs, and
    when the last sync changed no transaction here."""
    app = current()
    if not app.lock.acquire(blocking=False):
        raise ConflictError(f"{app.article} {app.name} sync is running; undo when it finishes.")
    token = audit_context.set_audit_scope("user", "ui", note=f"undo of a {app.name} sync")
    try:
        refuse_while_others_run("undo a sync")
        st = sync_state.load()
        mark = st.get("undo")
        if not mark:
            raise ValidationError("The last sync changed no transaction here: nothing to undo.")
        store = TransactionAuditStore()
        events = changes(store.of_surface(mark["from"], upto=mark["to"], surface=app.surface))
        ids = {e["txn_id"] for e in events}
        later = store.touched_after(mark["to"], ids)
        put_back, partly = 0, []
        for txn_id in sorted(ids - later):
            mine = [e for e in events if e["txn_id"] == txn_id]
            try:
                for event in reversed(mine):
                    _put_back(event)
                put_back += 1
            except OpenNetWorthError as exc:
                logger.info("undo of %s stopped partway: %s", txn_id, exc)
                partly.append(_title(txn_id))
        st = sync_state.load()
        st["undo"], st["enabled"] = None, False
        sync_state.save(st)
        skipped = [_title(t) for t in sorted(later)]
        return {"put_back": put_back, "skipped": skipped, "partly": partly, "sync_off": True}
    finally:
        audit_context.clear_audit_scope(token)
        app.lock.release()


def _title(txn_id: str) -> str:
    row = ledger_service.transactions.find_by_id(txn_id) or {}
    return (
        f"{row.get('date') or ''} {(row.get('description') or '').strip() or '(no payee)'}".strip()
    )
