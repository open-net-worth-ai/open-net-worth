"""The transactions half of a run: pair every changed ledger transaction with YNAB's version,
decide, apply the pulls through the ledger's chokes, and send the pushes."""

from __future__ import annotations

import logging
from collections import Counter

from backend.core.errors import ConflictError, OpenNetWorthError
from backend.services import ledger as ledger_service
from backend.services.ynab_migration.mapping import SOURCE
from backend.services.ynab_sync import conflicts, pull, push, recorder, units
from backend.services.ynab_sync.baselines import Baselines
from backend.services.ynab_sync.canon import canon, view
from backend.services.ynab_sync.client import YnabClient
from backend.services.ynab_sync.context import Context
from backend.services.ynab_sync.delta import Change
from backend.services.ynab_sync.units import Unit, decide

logger = logging.getLogger(__name__)

MAX_REMOVAL_FRACTION = 0.25  # a read that would delete more of the ledger than this is refused
MIN_REMOVAL_GUARD = 25  # ...but never for fewer rows than this


def _rows(ctx: Context, base: Baselines, change: Change, first: bool, scan: set[str]):
    """(ledger rows to look at, their baselines, ref -> ledger id, refs YNAB no longer holds)."""
    index = {ref: i for i, d in base.txns.items() for ref in d["refs"]}
    if first:
        everything = {r["id"]: r for r in ledger_service.transactions.all()}
        for r in everything.values():
            for ref in units.ynab_refs(r):
                index.setdefault(ref, r["id"])
        rows = {
            i: r
            for i, r in everything.items()
            if units.ynab_refs(r) or (r["kind"] in units.CREATABLE and not r.get("deleted"))
        }
        bases = units.first_run_bases([r for r in rows.values() if units.ynab_refs(r)])
        gone = (
            {ref for r in rows.values() for ref in units.ynab_refs(r)}
            - set(ctx.mirror)
            - set(ctx.sub_parent)
        )
        return rows, bases, index, gone
    wanted = scan | {index[r] for r in change.touched if r in index}
    wanted |= {index[s] for t in change.touched for s in _line_ids(ctx, t) if s in index}
    # Changes nobody audited (a checkpoint rewind, a batch rollback) are found by comparing
    # every baselined row with the ledger as it is now — local, no audit event needed.
    everything = {r["id"]: r for r in ledger_service.transactions.all()}
    wanted |= {
        i
        for i, d in base.txns.items()
        if i in everything
        and (None if everything[i].get("deleted") else canon(everything[i])) != d["canon"]
    }
    rows = {i: everything[i] for i in wanted if i in everything}
    bases = {i: base.txns[i]["canon"] for i in rows if i in base.txns}
    gone = {r for r in change.touched if r not in ctx.mirror and r not in ctx.sub_parent}
    return rows, bases, index, gone


def _line_ids(ctx: Context, top: str) -> list[str]:
    return [s["id"] for s in ctx.mirror.get(top, {}).get("subtransactions") or []]


def _file(ctx: Context, unit: Unit, kind: str, reason: str) -> None:
    onw, ynab = view(unit.onw, ctx.names), view(unit.ynab, ctx.names)
    shown = unit.onw or unit.ynab or unit.base
    title = (shown or {}).get("description") or "(no payee)"
    conflicts.file(
        kind,
        unit.key,
        title,
        reason,
        onw,
        ynab,
        entity="txn",
        onw_txn_id=unit.onw_id,
        date=(shown or {}).get("date"),
    )


def _title(unit: Unit) -> str:
    return ((unit.row or {}).get("description") or "").strip() or "(no payee)"


def _apply_pull(ctx: Context, unit: Unit, base: Baselines, tally: Counter) -> None:
    try:
        now, row_id = pull.apply(unit.row, unit.rendered if unit.ynab is not None else None)
    except pull.LedgerChanged:
        _file(ctx, unit, conflicts.CONFLICT, "Changed here while the sync was running.")
        return
    except OpenNetWorthError as exc:
        _file(ctx, unit, conflicts.FAILED, f"The ledger refused YNAB's version: {exc.message}")
        return
    if unit.onw_id and row_id != unit.onw_id:
        base.drop_txn(unit.onw_id)  # a deleted row booked back is a new row
    if now is None:
        base.drop_txn(row_id) if row_id else None
    else:
        base.set_txn(row_id, unit.refs, now)
    recorder.txn_change("pulled", ctx, row_id, unit.onw, unit.ynab)
    tally["pulled"] += 1


def _seed(unit: Unit, base: Baselines) -> None:
    """Record the agreed state of a linked transaction when nothing needed doing."""
    if not (unit.onw_id and unit.refs):
        return
    if unit.attach:
        ledger_service.update_transaction(
            unit.onw_id, {"external_refs": [{"source": SOURCE, "ref": r} for r in unit.refs]}
        )
    if unit.onw is None:
        base.drop_txn(unit.onw_id)
    elif unit.onw_id not in base.txns or base.txns[unit.onw_id]["canon"] != unit.onw:
        base.set_txn(unit.onw_id, unit.refs, unit.onw)


def settle(
    ctx: Context,
    client: YnabClient,
    base: Baselines,
    change: Change,
    first: bool,
    scan: set[str],
    tally: Counter,
) -> list[str]:
    """Reconcile every changed transaction; returns the ledger ids whose push is still owed."""
    rows, bases, index, gone = _rows(ctx, base, change, first, scan)
    all_units = units.build(ctx, rows, bases, index, change.touched, gone)
    _guard_removals(all_units)
    open_items = conflicts.open_keys()
    todo: list[Unit] = []
    for u in all_units:
        action = decide(u.base, u.onw, u.ynab)
        if u.blocked and u.onw_id is None:
            _file(ctx, u, conflicts.UNSUPPORTED, u.blocked)
            continue
        if u.key in open_items:
            if action != "adopt":
                continue
            conflicts.close(u.key)
        if action == "conflict":
            _file(
                ctx,
                u,
                conflicts.CONFLICT,
                "Changed in both Open Net Worth and YNAB since the last sync.",
            )
        elif action == "pull":
            _apply_pull(ctx, u, base, tally)
        elif action == "push":
            todo.append(u)
        else:
            _seed(u, base)
    return _push_all(ctx, client, base, todo, tally)


def _guard_removals(all_units: list[Unit]) -> None:
    linked = sum(1 for u in all_units if u.refs)
    removals = sum(
        1 for u in all_units if u.refs and u.ynab is None and u.onw is not None and u.base == u.onw
    )
    if removals > max(MIN_REMOVAL_GUARD, MAX_REMOVAL_FRACTION * linked):
        raise ConflictError(
            f"YNAB's answer would delete {removals} of {linked} synced transactions here; "
            "nothing was changed. Check the plan in YNAB, then run the sync again."
        )


def _push_all(
    ctx: Context, client: YnabClient, base: Baselines, todo: list[Unit], tally: Counter
) -> list[str]:
    out = push.Outcome()
    out.on_sent = lambda unit: _recorded(ctx, unit, out, base)
    creates = [u for u in todo if not u.refs and u.onw]
    deletes = [u for u in todo if u.refs and u.onw is None]
    updates = [u for u in todo if u.refs and u.onw]
    if len(deletes) > max(MIN_REMOVAL_GUARD, MAX_REMOVAL_FRACTION * max(len(base.txns), 1)):
        for u in deletes:
            _file(
                ctx,
                u,
                conflicts.CONFLICT,
                f"{len(deletes)} deletions would be sent to YNAB at once; confirm each one.",
            )
        deletes = []
    push.send_updates(ctx, client, updates, out)
    push.send_creates(ctx, client, creates, out)
    push.send_deletes(ctx, client, deletes, out)
    for u, reason in out.unsupported:
        _file(ctx, u, conflicts.UNSUPPORTED, reason)
    for u, reason in out.failed:
        _file(ctx, u, conflicts.FAILED, f"YNAB did not accept it: {reason}")
    tally["sent"] += len(out.sent)
    for u in out.deferred:
        recorder.deferred(_title(u), "Waiting for the next run (YNAB request limit).")
    return [u.onw_id for u in out.deferred if u.onw_id]


def _recorded(ctx: Context, unit: Unit, out: push.Outcome, base: Baselines) -> None:
    """Link and baseline one confirmed unit now. If the ledger refuses the link, YNAB still holds
    the row: file it, and the unowned row is claimed by the ledger row on the next read."""
    recorder.txn_change("sent", ctx, unit.onw_id, unit.base, unit.onw)
    try:
        _record_sent(unit, out, base)
    except OpenNetWorthError as exc:
        _file(
            ctx, unit, conflicts.FAILED, f"Sent to YNAB, but linking it here failed: {exc.message}"
        )


def _record_sent(u: Unit, out: push.Outcome, base: Baselines) -> None:
    if u.onw is None:
        base.drop_txn(u.onw_id)
        return
    refs, now = u.refs, u.onw
    if u.key in out.new_refs:
        refs = out.new_refs[u.key]
        updated = ledger_service.update_transaction(
            u.onw_id, {"external_refs": [{"source": SOURCE, "ref": r} for r in refs]}
        )
        now = canon(updated)
    base.set_txn(u.onw_id, refs, now)
