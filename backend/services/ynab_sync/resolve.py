"""Resolving a review-list item: the person's choice is applied to BOTH sides, so afterwards
they agree and the item is gone. "Keep Open Net Worth's" sends the ledger's current version to
YNAB; "Keep YNAB's" writes YNAB's current version into the ledger."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core import audit_context, data_events
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.ynab_sync_store import YnabMirrorStore
from backend.services import import_runs, planning
from backend.services import ledger as ledger_service
from backend.services.ynab_sync import (
    assigned,
    conflicts,
    context,
    delta,
    pull,
    push,
    recorder,
    runner,
    units,
)
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.baselines import Baselines
from backend.services.ynab_sync.client import Budget, YnabClient
from backend.services.ynab_sync.txn_sync import _record_sent
from backend.services.ynab_sync.units import Unit

RESOLVE_REQUESTS = 6  # one resolution never needs more than a PATCH, its note and a DELETE


def resolve(conflict_id: str, keep: str) -> dict:
    if keep not in ("onw", "ynab"):
        raise ValidationError("keep must be 'onw' or 'ynab'")
    if not sync_state.load()["plan_id"]:
        raise ValidationError("choose the YNAB plan to sync first")
    item = conflicts.get(conflict_id)
    if item is None:
        raise NotFoundError("That review item is already resolved.")
    if not runner._lock.acquire(blocking=False):
        raise ConflictError("A YNAB sync is running; resolve this when it finishes.")
    if import_runs.has_running("ynab"):
        runner._lock.release()
        raise ConflictError("A YNAB import is running; resolve this when it finishes.")
    token = audit_context.set_audit_scope("user", sync_state.SURFACE, note="review list")
    rec = recorder.Recorder()
    rec_token = recorder.activate(rec)
    started = datetime.now(timezone.utc)
    try:
        st = sync_state.load()
        meta, mirror_store = sync_state.load_meta(), YnabMirrorStore()
        mirror = mirror_store.by_id()
        loaded = dict(mirror)
        base = Baselines()
        ctx = context.build(st, meta, mirror, base.assigned)
        client = YnabClient(st["plan_id"], Budget(RESOLVE_REQUESTS))
        if item["entity"] == "assigned":
            _assigned(item, keep, client, base, meta)
        else:
            _transaction(ctx, item, keep, client, base)
        base.flush()
        delta.save_changes(mirror_store, loaded, mirror, set(), False)
        sync_state.save_meta(meta)
        conflicts.close(item["key"])
    finally:
        recorder.deactivate(rec_token)
        audit_context.clear_audit_scope(token)
        runner._lock.release()
    if rec.sent_to_ynab():
        runner.store_record(
            st, rec, "manual", started, None, "Resolved a review item", client.budget.used
        )
    data_events.publish(["*"])
    return runner.status()


def _assigned(item: dict, keep: str, client: YnabClient, base: Baselines, meta: dict) -> None:
    it = assigned.Item(
        item["month"],
        item["category"],
        item["env"],
        item["ynab_milli"],
        item["onw_milli"],
        item["ynab_milli"],
        keep,
        False,
    )
    if keep == "ynab":
        assigned.pull(it)
        base.set_assigned(it.month, it.cid, it.ynab)
        return
    it.onw = round(planning.assignments_store.month_map(it.month).get(it.env, 0.0) * 1000)
    sent, _, rejected = assigned.push(client, [it], {c["id"]: c for c in meta["categories"]})
    if rejected:
        raise ConflictError(f"YNAB did not accept it: {rejected[0][1]}")
    base.set_assigned(it.month, it.cid, it.onw)
    name = next((c["name"] for c in meta["categories"] if c["id"] == it.cid), "Category")
    recorder.assigned_change("sent", name, it.month, it.ynab, it.onw)


def _unit(ctx, item: dict, base: Baselines) -> Unit:
    onw_id = item.get("onw_txn_id")
    row = ledger_service.transactions.find_by_id(onw_id) if onw_id else None
    refs = (base.txns.get(onw_id) or {}).get("refs") or (
        [] if not item["key"].startswith("y:") else [item["key"][2:]]
    )
    rows = {onw_id: row} if row else {}
    index = {r: onw_id for r in refs} if onw_id else {}
    found = units.build(
        ctx,
        rows,
        {onw_id: (base.txns.get(onw_id) or {}).get("canon")} if onw_id else {},
        index,
        set(refs),
        set(refs) - set(ctx.mirror) - set(ctx.sub_parent),
    )
    unit = next((u for u in found if u.onw_id == onw_id), None)
    if unit is None:
        raise ConflictError("Neither side of this item exists any more.")
    return unit


def _transaction(ctx, item: dict, keep: str, client: YnabClient, base: Baselines) -> None:
    unit = _unit(ctx, item, base)
    if keep == "ynab":
        if unit.blocked:
            raise ConflictError(f"YNAB's version cannot be brought in: {unit.blocked}")
        now, row_id = pull.apply(unit.row, unit.rendered if unit.ynab is not None else None)
        if unit.onw_id and row_id != unit.onw_id:
            base.drop_txn(unit.onw_id)
        if now is None:
            if row_id:
                base.drop_txn(row_id)
        else:
            base.set_txn(row_id, unit.refs, now)
        return
    if unit.onw_id is None or (item["kind"] == conflicts.UNSUPPORTED and not unit.refs):
        return  # nothing sendable: the ledger's version just stays where it is, unsynced
    # Send the whole of the ledger's version: YNAB's current state is the starting point.
    send = Unit(
        unit.onw_id,
        unit.row,
        unit.refs if unit.ynab is not None else [],
        unit.ynab,
        unit.onw,
        None,
        None,
        None,
    )
    out = push.Outcome()
    sender = {"update": push.send_updates, "create": push.send_creates, "delete": push.send_deletes}
    kind = "delete" if send.onw is None else ("update" if send.refs else "create")
    sender[kind](ctx, client, [send], out)
    if out.unsupported or out.failed:
        why = (out.unsupported or out.failed)[0][1]
        raise ConflictError(f"YNAB cannot take this change: {why}")
    if out.deferred:
        raise ConflictError("Out of YNAB requests for now; try again shortly.")
    recorder.txn_change("sent", ctx, send.onw_id, send.base, send.onw)
    _record_sent(send, out, base)
