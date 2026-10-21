"""Folds one YNAB read (the whole plan, or only what changed since ``server_knowledge``) into the
sync's mirror of YNAB: its transactions (split lines nested, payee names resolved), its accounts,
categories, groups and payees, and the Assigned figures it reported."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.ynab_migration import mapping, source


@dataclass
class Change:
    full: bool
    knowledge: int
    touched: set[str] = field(default_factory=set)  # YNAB transaction ids added/changed/removed
    assigned: dict[tuple[str, str], int] = field(default_factory=dict)  # (month, category) -> milli


def _upsert(items: list[dict], incoming: list[dict]) -> list[dict]:
    by_id = {i["id"]: i for i in items}
    for row in incoming:
        by_id[row["id"]] = row
    return list(by_id.values())


def merge_meta(plan: dict, meta: dict) -> None:
    for p in plan.get("payees") or []:
        meta["payees"][p["id"]] = p.get("name")
    meta["accounts"] = _upsert(meta["accounts"], plan.get("accounts") or [])
    meta["groups"] = _upsert(
        meta["groups"],
        [
            {k: g.get(k) for k in ("id", "name", "hidden", "deleted")}
            for g in plan.get("category_groups") or []
        ],
    )
    keep = ("id", "name", "category_group_id", "hidden", "internal", "deleted", "note")
    meta["categories"] = _upsert(
        meta["categories"], [{k: c.get(k) for k in keep} for c in plan.get("categories") or []]
    )
    months = {m["month"][:7] for m in plan.get("months") or []}
    meta["months"] = sorted(set(meta["months"]) | months)


def _merge_lines(row: dict, lines: list[dict]) -> None:
    by_id = {s["id"]: s for s in row.get("subtransactions") or []}
    by_id.update({s["id"]: s for s in lines})
    row["subtransactions"] = list(by_id.values())


def merge_transactions(plan: dict, mirror: dict[str, dict], meta: dict, full: bool) -> set[str]:
    """Update ``mirror`` in place; returns the ids of every transaction the read touched."""
    nested = source._nest_transactions(
        {
            "payees": [{"id": i, "name": n} for i, n in meta["payees"].items()],
            "transactions": plan.get("transactions") or [],
            "subtransactions": plan.get("subtransactions") or [],
        }
    )
    mapping.validate_amounts([], nested, [])
    if full:
        mirror.clear()
    touched: set[str] = set()
    for t in nested:
        touched.add(t["id"])
        previous = mirror.get(t["id"])
        touched |= {s["id"] for s in (previous or {}).get("subtransactions") or []}
        if t.get("deleted"):
            mirror.pop(t["id"], None)
            continue
        if previous and not t["subtransactions"]:
            t["subtransactions"] = previous.get("subtransactions") or []
        mirror[t["id"]] = t
    sent = {t["id"] for t in nested}
    orphans: dict[str, list[dict]] = {}
    for s in plan.get("subtransactions") or []:
        if s.get("transaction_id") not in sent:
            orphans.setdefault(s["transaction_id"], []).append(s)
    for parent, lines in orphans.items():
        if parent in mirror:
            _merge_lines(mirror[parent], lines)
            touched.add(parent)
    return touched


def assigned_of(plan: dict) -> dict[tuple[str, str], int]:
    return {
        (m["month"][:7], c["id"]): c.get("budgeted", 0)
        for m in plan.get("months") or []
        for c in m.get("categories") or []
    }


def apply_read(data: dict, mirror: dict[str, dict], meta: dict, full: bool) -> Change:
    plan = data.get("plan") or data.get("budget") or {}
    merge_meta(plan, meta)
    touched = merge_transactions(plan, mirror, meta, full)
    assigned = assigned_of(plan)
    mapping.validate_amounts([], [], [{"categories": [{"budgeted": v} for v in assigned.values()]}])
    return Change(full, data["server_knowledge"], touched, assigned)


def save_changes(
    store, loaded: dict[str, dict], mirror: dict[str, dict], touched: set[str], full: bool
) -> None:
    """Write back only the mirror rows that differ from what was loaded (the read's rows and the
    ones a push replaced); a first run rewrites the lot in one transaction."""
    if full:
        store.replace_all(list(mirror.values()))
        return
    changed = (
        touched
        | {k for k, v in mirror.items() if loaded.get(k) is not v}
        | (loaded.keys() - mirror.keys())
    )
    for tid in changed:
        store.upsert(mirror[tid]) if tid in mirror else store.delete(tid)
