"""What the next sync would do, without doing it: the app is read as a real run reads it, every
transaction and each month's Assigned is decided by the same rules, and nothing is written on
either side. No ledger row, no row in the app, no baseline, mirror, sync state or review item.
Category changes are left out: their step writes as it decides, so it can't be dry-run, and the
answer says so. Reading stores the app's raw rows as evidence, as any read does (``raw_rows``)."""

from __future__ import annotations

import copy
from collections import Counter

from backend.core.errors import ConflictError, ValidationError
from backend.services import planning
from backend.services.budget_app import current
from backend.services.ynab_sync import (
    assigned,
    budget_mode,
    conflicts,
    context,
    delta,
    merge,
    txn_sync,
    units,
)
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.baselines import Baselines
from backend.services.ynab_sync.client import Budget
from backend.services.ynab_sync.units import decide_unit

SAMPLES = 8  # rows named per kind of change; the counts cover them all

_KEPT = {"noop", "adopt"}


def preview() -> dict:
    app = current()
    st = sync_state.load()
    if not st["plan_id"]:
        raise ValidationError(f"Choose the {app.name} {app.plan_word} to sync first.")
    if not st["bootstrapped"]:
        raise ValidationError(
            "The first sync after the import compares everything; there is nothing to preview yet."
        )
    if not app.lock.acquire(blocking=False):
        raise ConflictError(f"{app.article} {app.name} sync is running; preview when it finishes.")
    try:
        with app.open_client(st["plan_id"], Budget(sync_state.request_cap())) as client:
            return _decide(copy.deepcopy(st), client)
    finally:
        app.lock.release()


def _decide(st: dict, client) -> dict:
    upto = units.TransactionAuditStore().max_seq()
    meta, mirror = copy.deepcopy(sync_state.load_meta()), current().stores.mirror().by_id()
    data = client.read_plan(st["server_knowledge"])
    change = delta.apply_read(data, mirror, meta, False)
    base = Baselines(st["plan_id"])
    syncs_assigned = budget_mode.settle(data, meta, base)
    ctx = context.build(st, meta, mirror, base.assigned, base.cats, base.declined)
    scan = units.audit_touched(st["audit_cursor"], upto) | set(st["pending"]) | set(st["held"])
    scan |= {c["onw_txn_id"] for c in conflicts.listing() if c.get("onw_txn_id")}
    change.touched |= set(st["held_ynab"])
    rows, bases, index, gone = txn_sync._rows(ctx, base, change, False, scan)
    built = units.build(ctx, rows, bases, index, change.touched, gone)
    out = {"transactions": _transactions(built, st["direction"])}
    if syncs_assigned:
        out["assigned"] = _assigned(ctx, base, change, st["direction"])
    out["categories_left_out"] = True
    return out


def _side(u) -> str:
    v = u.onw or u.ynab or u.base or {}
    title = ((u.row or {}).get("description") or "").strip() or "(no payee)"
    return f"{v.get('date') or ''} {title}".strip()


def _transactions(built: list, direction: str) -> dict:
    """Counts per outcome and a few rows each, as the run would decide them."""
    open_items = conflicts.open_keys()
    counts: Counter = Counter()
    samples: dict[str, list[str]] = {}
    for u in built:
        if u.key in open_items:
            continue  # already waiting for the person: the run leaves it
        action = decide_unit(u)
        if action in _KEPT:
            continue
        if u.missing or (u.blocked and u.onw_id is None):
            kind = "review"
        elif action == "conflict":
            kind = "merge" if direction == "both" and merge.plan(u) else "review"
        elif action == "push" and direction == "from_ynab":
            kind = "held"
        elif action == "pull" and direction == "to_ynab":
            kind = "held"
        elif action == "push":
            kind = (
                "delete_there" if u.onw is None else "add_there" if not u.refs else "change_there"
            )
        else:
            kind = (
                "delete_here" if u.ynab is None else "add_here" if not u.onw_id else "change_here"
            )
        counts[kind] += 1
        names = samples.setdefault(kind, [])
        if len(names) < SAMPLES:
            names.append(_side(u))
    return {"counts": dict(counts), "samples": samples}


def _assigned(ctx, base: Baselines, change, direction: str) -> dict:
    items = assigned.evaluate(
        ctx, base.assigned, change.assigned, planning.assignments_store.all_map()
    )
    counts: Counter = Counter()
    for it in items:
        if it.action == "push" and direction != "from_ynab":
            counts["change_there"] += 1
        elif it.action == "pull" and direction != "to_ynab":
            counts["change_here"] += 1
        elif it.action == "conflict":
            counts["review"] += 1
    return {"counts": dict(counts)}
