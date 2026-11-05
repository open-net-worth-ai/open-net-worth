"""What changed in a freshly built plan since the sync's stored mirror (PURE).

``ynab_sync.delta.apply_read`` folds a read into the mirror whether it is the whole plan or only
what changed, so for Actual (whose server has no delta read) the client builds the whole plan each
run and reduces it here. Re-exported by ``shape``.
"""

from __future__ import annotations

from backend.services.ynab_migration import source

_NESTING = ("subtransactions", "payee_name")


def _live_lines(row: dict) -> dict[str, dict]:
    return {s["id"]: s for s in row.get("subtransactions") or [] if not s.get("deleted")}


def differs(old: dict, new: dict) -> bool:
    """Whether ``new`` says anything ``old`` doesn't, its live lines included."""
    if any(old.get(k) != v for k, v in new.items() if k != "subtransactions"):
        return True
    before, after = _live_lines(old), _live_lines(new)
    if before.keys() != after.keys():
        return True
    return any(before[i].get(k) != v for i, line in after.items() for k, v in line.items())


def _gone(old: dict, keep: dict[str, dict]) -> list[dict]:
    """The old lines that are no longer there, marked deleted: ``merge_transactions`` keeps a
    row's previous lines whenever the new row brings none."""
    return [
        {
            **{k: v for k, v in s.items() if k != "payee_name"},
            "transaction_id": old["id"],
            "deleted": True,
        }
        for i, s in _live_lines(old).items()
        if i not in keep
    ]


def delta_against(plan: dict, mirror: dict[str, dict], meta: dict) -> dict:
    """``plan`` reduced to what differs from ``mirror`` (nested rows by id) and ``meta`` (payee
    names): changed or new transactions with all their lines, removed rows and lines with
    ``deleted: True``, changed payees. Accounts, groups, categories and months are always full."""
    nested = {t["id"]: t for t in source._nest_transactions(plan)}
    flat = {t["id"]: t for t in plan.get("transactions") or []}
    lines: dict[str, list[dict]] = {}
    for s in plan.get("subtransactions") or []:
        lines.setdefault(s["transaction_id"], []).append(s)
    sent_t: list[dict] = []
    sent_s: list[dict] = []
    for tid, row in nested.items():
        old = mirror.get(tid)
        if old is not None and not differs(old, row):
            continue
        sent_t.append(flat[tid])
        sent_s += lines.get(tid, [])
        if old is not None:
            sent_s += _gone(old, _live_lines(row))
    for tid, old in mirror.items():
        if tid not in nested:
            sent_t.append({**{k: v for k, v in old.items() if k not in _NESTING}, "deleted": True})
            sent_s += _gone(old, {})
    names = meta.get("payees") or {}
    payees = [
        p for p in plan.get("payees") or [] if p["id"] not in names or names[p["id"]] != p["name"]
    ]
    return {**plan, "payees": payees, "transactions": sent_t, "subtransactions": sent_s}
