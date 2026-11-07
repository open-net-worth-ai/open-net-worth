"""Resolving a review-list item: the person's choice is applied to BOTH sides, so afterwards
they agree and the item is gone. "Keep Open Net Worth's" sends the ledger's current version to
YNAB; "Keep YNAB's" writes YNAB's current version into the ledger."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core import audit_context, data_events
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.services import import_runs, planning
from backend.services import ledger as ledger_service
from backend.services.budget_app import current, refuse_while_others_run, starting
from backend.services.ynab_sync import (
    assigned,
    category_resolve,
    conflicts,
    context,
    delta,
    pull,
    push,
    recorder,
    runner,
    starts,
    units,
)
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.baselines import Baselines
from backend.services.ynab_sync.client import Budget, YnabClient
from backend.services.ynab_sync.txn_sync import _record_sent, follow_merges
from backend.services.ynab_sync.units import Unit


def resolve(conflict_id: str, keep: str, choice: str | None = None) -> dict:
    app = current()
    if keep not in ("onw", "ynab"):
        raise ValidationError("keep must be 'onw' or 'ynab'")
    if not sync_state.load()["plan_id"]:
        raise ValidationError(f"choose the {app.name} {app.plan_word} to sync first")
    item = conflicts.get(conflict_id)
    if item is None:
        raise NotFoundError("That review item is already resolved.")
    if not app.lock.acquire(blocking=False):
        raise ConflictError(
            f"{app.article} {app.name} sync is running; resolve this when it finishes."
        )
    if import_runs.has_running(app.key):
        app.lock.release()
        raise ConflictError(
            f"{app.article} {app.name} import is running; resolve this when it finishes."
        )
    try:
        with starting():
            refuse_while_others_run("resolve this")
    except ConflictError:
        app.lock.release()
        raise
    token = audit_context.set_audit_scope("user", app.surface, note="review list")
    rec = recorder.Recorder()
    rec_token = recorder.activate(rec)
    started = datetime.now(timezone.utc)
    budget = Budget(app.caps.resolve_request_cap)
    try:
        st = sync_state.load()
        meta, mirror_store = sync_state.load_meta(), app.stores.mirror()
        mirror = mirror_store.by_id()
        loaded = dict(mirror)
        base = Baselines(st["plan_id"])
        ctx = context.build(st, meta, mirror, base.assigned, base.cats, base.declined)
        with app.open_client(st["plan_id"], budget) as client:
            if item["entity"] == "start":
                starts.resolve(item, keep)
            elif item["entity"] in ("category", "group", "new_category", "budget"):
                category_resolve.apply(item, keep, choice, st, meta, base, client)
            elif _held_side(st["direction"], keep):
                _accept(ctx, item, keep, base, st)
            elif item["entity"] == "assigned":
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
        app.lock.release()
    if rec.sent_to_ynab():
        runner.store_record(st, rec, "manual", started, None, "Resolved a review item", budget.used)
    data_events.publish(["*"])
    return runner.status()


def _held_side(direction: str, keep: str) -> bool:
    """Keeping a side means writing the other one — unless the direction protects it."""
    return (direction, keep) in (("from_ynab", "onw"), ("to_ynab", "ynab"))


def _accept(ctx, item: dict, keep: str, base: Baselines, st: dict) -> None:
    """Keep ``keep``'s version WITHOUT writing the side the one-way direction protects: the other
    side's current state becomes the agreed one, so nothing moves now and a later change on both
    sides is asked about again."""
    if item["entity"] == "assigned":
        env = item["env"]
        onw = round(planning.assignments_store.month_map(item["month"]).get(env, 0.0) * 1000)
        base.set_assigned(
            item["month"], item["category"], item["ynab_milli"] if keep == "onw" else onw
        )
        return
    unit = _unit(ctx, item, base)
    if unit.onw_id is None:
        return
    gone_there = unit.refs and unit.onw is not None and unit.ynab is None
    if gone_there and current().caps.links_without_baseline:
        return _accept_missing(unit, keep, base, st)
    other = unit.ynab if keep == "onw" else unit.onw
    if other is None:
        base.drop_txn(unit.onw_id)
    else:
        base.set_txn(unit.onw_id, unit.refs, other)


def _unlink(unit: Unit) -> None:
    """Its ids name rows the app no longer holds, and they mustn't stand for this one."""
    held = units.ynab_refs(unit.row)
    for ref in (r for r in unit.refs if r in held):
        ledger_service.detach_external_ref(
            unit.onw_id, current().source, ref, reason="no longer in the budget app"
        )


def _accept_missing(unit: Unit, keep: str, base: Baselines, st: dict) -> None:
    """The person chose, for a row the app no longer holds, and the direction protects the side
    that choice writes. It waits as what one way holds back does, for a switch to both ways."""
    if keep == "ynab":  # remove it here, once the book may change
        base.set_txn(unit.onw_id, unit.refs, unit.onw)
        st["held_ynab"] = sorted(set(st["held_ynab"]) | set(unit.refs))
    else:  # send it as new, once the app may change: until then it is linked to nothing
        _unlink(unit)
        base.drop_txn(unit.onw_id)
        st["held"] = sorted(set(st["held"]) | {unit.onw_id})
    sync_state.save(st)


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
        raise ConflictError(f"{current().name} did not accept it: {rejected[0][1]}")
    base.set_assigned(it.month, it.cid, it.onw)
    name = next((c["name"] for c in meta["categories"] if c["id"] == it.cid), "Category")
    recorder.assigned_change("sent", name, it.month, it.ynab, it.onw)


def _unit(ctx, item: dict, base: Baselines) -> Unit:
    onw_id = follow_merges(base).get(item.get("onw_txn_id"), item.get("onw_txn_id"))
    row = ledger_service.transactions.find_by_id(onw_id) if onw_id else None
    refs = (base.txns.get(onw_id) or {}).get("refs") or (
        [] if not item["key"].startswith("y:") else [item["key"][2:]]
    )
    if not refs and row and current().caps.links_without_baseline:
        refs = units.ynab_refs(row)
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
            raise ConflictError(f"{current().name}'s version cannot be brought in: {unit.blocked}")
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
        raise ConflictError(f"{current().name} cannot take this change: {why}")
    if out.deferred:
        raise ConflictError(f"Out of {current().name} requests for now; try again shortly.")
    if out.moved:
        raise ConflictError(
            f"This changed in {current().name} since the last sync. Sync, then look at it again."
        )
    recorder.txn_change("sent", ctx, send.onw_id, send.base, send.onw)
    _record_sent(send, out, base)
    if unit.refs and unit.ynab is None and current().caps.links_without_baseline:
        _unlink(unit)
