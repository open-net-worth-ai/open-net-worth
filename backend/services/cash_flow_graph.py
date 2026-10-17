"""The money-flow (Sankey) graph for one month: income → total → spending groups → spending
categories, plus a Saved or a "From savings" node so every column sums to the same total.

Reads the SAME per-category month summary as the Overview reports (`reports_rollup.
month_summary`) — one set of inclusion rules (payment envelopes are not spend, refunds net
inside their category). Every amount and share is computed here; the browser only lays out
what it is given.

Income categories belong to no group, so they link straight to the total. Columns (full):
0 income category · 1 total · 2 spending (envelope) group / saved · 3 spending category.
Compact (the Dashboard card): 0 income category · 1 total · 2 spending group / saved.
"""

from __future__ import annotations

import re
from datetime import date

from backend.core.errors import ValidationError
from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services.reports_rollup import month_summary

FOLD_SHARE = 0.02
MAX_CATEGORIES_PER_GROUP = 8
# Planning's derived group of LABELED categories linked to no envelope (planning.budget_month).
UNBUDGETED_GROUP_ID = "__unbudgeted__"
NOT_IN_BUDGET = "Not in budget"
COMPACT_MAX_INCOME = 3
COMPACT_MAX_GROUPS = 5
_EPSILON = 0.005
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _round2(value: float) -> float:
    return round(value + 0.0, 2)


def _share(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole > 0 else 0.0


def default_month(today: date | None = None) -> str:
    """The last FULL month — a month in progress over-weights early paydays."""
    d = today or user_today()
    total = d.year * 12 + d.month - 2
    return f"{total // 12}-{total % 12 + 1:02d}"


def _fold(rows: list[dict], keep_max: int, floor: float) -> tuple[list[dict], float]:
    """Largest-first rows that clear `floor` (max `keep_max`); the rest summed separately.

    An "Other" must stand for SEVERAL rows beside named ones: a group whose every row is small
    names its largest instead (a lone "Other needs" was the whole group, anonymised), and a
    single leftover row is named, never folded alone (owner: "why so many Other here?")."""
    ranked = sorted(rows, key=lambda r: r["amount"], reverse=True)
    kept = [r for r in ranked if r["amount"] >= floor][:keep_max] or ranked[:keep_max]
    if len(ranked) - len(kept) == 1:
        kept = ranked
    kept_ids = {id(r) for r in kept}
    rest = _round2(sum(r["amount"] for r in ranked if id(r) not in kept_ids))
    return kept, rest


def _other_label(group: str) -> str:
    """ "Other housing" — but a group already named "Other(s)" reads as just "Other"."""
    return "Other" if group.lower().startswith("other") else f"Other {group.lower()}"


UNCATEGORIZED_SPENDING = "Uncategorized spending"


def _by_group(rows: list[dict], fallback: str) -> list[tuple[str, str, int, list[dict]]]:
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(
            r.get("group_id") or "",
            {
                "name": (r.get("group") or "").strip() or fallback,
                "index": r.get("group_index", 0),
                "rows": [],
            },
        )
        g["rows"].append(r)
    out = [
        # A group holding only uncategorized money (the budget's own "Uncategorized" group, or
        # history rows with no group at all, which read "Spending") says so on the money-flow
        # card, where the compact view stops at groups.
        (
            gid,
            UNCATEGORIZED_SPENDING
            if all(not r.get("key") for r in g["rows"])
            # "Unbudgeted" read as a kind of spending; these are categories with no envelope.
            else NOT_IN_BUDGET
            if gid == UNBUDGETED_GROUP_ID
            else g["name"],
            g["index"],
            g["rows"],
        )
        for gid, g in groups.items()
    ]
    out.sort(key=lambda g: sum(r["amount"] for r in g[3]), reverse=True)
    return out


class _Graph:
    def __init__(self, flow_total: float) -> None:
        self.flow_total = flow_total
        self.nodes: list[dict] = []
        self.links: list[dict] = []

    def node(self, node_id: str, label: str, column: int, kind: str, amount: float, **extra) -> str:
        share = (amount / self.flow_total * 100) if self.flow_total > 0 else 0.0
        self.nodes.append(
            {
                "id": node_id,
                "label": label,
                "column": column,
                "kind": kind,
                "amount": _round2(amount),
                "pct": round(share, 1),
                **extra,
            }
        )
        return node_id

    def link(self, source: str, target: str, amount: float) -> None:
        if amount > _EPSILON:
            self.links.append({"source": source, "target": target, "amount": _round2(amount)})


def _income_flat(g: _Graph, rows: list[dict], floor: float, cap: int) -> None:
    kept, rest = _fold(rows, cap, floor)
    for r in kept:
        g.link(
            g.node(f"inc:{r['key']}", r["label"], 0, "income", r["amount"]), "total", r["amount"]
        )
    if rest > _EPSILON:
        g.link(g.node("inc-other", "Other income", 0, "income", rest), "total", rest)


def _spend(g: _Graph, rows: list[dict], floor: float, compact: bool) -> None:
    groups = _by_group(rows, "Spending")
    # Colour follows the group, not its rank this month: slots are the groups' Planning order.
    slots = {grp[0]: slot for slot, grp in enumerate(sorted(groups, key=lambda grp: grp[2]))}
    shown = groups[:COMPACT_MAX_GROUPS] if compact else groups
    col = 2
    for gid, name, _index, grows in shown:
        group_total = sum(r["amount"] for r in grows)
        group_node = g.node(
            f"sg:{gid}", name, col, "spend_group", group_total, color_slot=slots[gid]
        )
        g.link("total", group_node, group_total)
        if compact:
            continue
        # Spend outside the budget is the spend worth noticing: each category is named.
        group_floor = 0.0 if gid == UNBUDGETED_GROUP_ID else floor
        kept, rest = _fold(grows, MAX_CATEGORIES_PER_GROUP, group_floor)
        for r in kept:
            node = g.node(
                f"sp:{r['key'] or 'unsorted'}",
                r["label"] if r.get("key") else UNCATEGORIZED_SPENDING,
                col + 1,
                "spend",
                r["amount"],
                color_slot=slots[gid],
                # Drill-down targets: the row's ledger categories, or Uncategorized (no category).
                category_ids=r.get("category_ids") or [],
                uncategorized=r.get("key") is None,
                group_pct=_share(r["amount"], group_total),
            )
            g.link(group_node, node, r["amount"])
        if rest > _EPSILON:
            node = g.node(
                f"sp-other:{gid}",
                _other_label(name),
                col + 1,
                "spend",
                rest,
                color_slot=slots[gid],
                group_pct=_share(rest, group_total),
            )
            g.link(group_node, node, rest)
    hidden = groups[len(shown) :]
    if hidden:
        rest = sum(r["amount"] for grp in hidden for r in grp[3])
        g.link("total", g.node("sg-other", "Other spending", col, "spend_group", rest), rest)


def build_graph(summary: dict, *, compact: bool = False) -> dict:
    """Pure: a `month_summary` → the Sankey graph. Every column sums to `flow_total`."""
    income_rows = summary["income_rows"]
    spend_rows = summary["expense_rows"]
    income = _round2(sum(r["amount"] for r in income_rows))
    spent = _round2(sum(r["amount"] for r in spend_rows))
    saved = _round2(max(income - spent, 0.0))
    shortfall = _round2(max(spent - income, 0.0))
    flow_total = max(income, spent)
    g = _Graph(flow_total)
    floor = flow_total * FOLD_SHARE

    g.node("total", "Spent" if shortfall > _EPSILON else "Income", 1, "total", flow_total)
    # Income is never folded by size: a book has a handful of sources and each is worth naming
    # (owner: S$15 of dividends read as an anonymous "Other income"). Only the count is capped.
    _income_flat(g, income_rows, 0.0, COMPACT_MAX_INCOME if compact else MAX_CATEGORIES_PER_GROUP)
    if shortfall > _EPSILON:
        g.link(g.node("shortfall", "From savings", 0, "shortfall", shortfall), "total", shortfall)
    _spend(g, spend_rows, floor, compact)
    if saved > _EPSILON:
        g.link("total", g.node("saved", "Saved", 2, "saved", saved), saved)

    return {
        "income_total": income,
        "spend_total": spent,
        "saved": saved,
        "shortfall": shortfall,
        "flow_total": _round2(flow_total),
        "savings_rate_pct": round(saved / income * 100, 1) if income > 0 else None,
        "columns": 3 if compact else 4,
        "nodes": g.nodes,
        "links": g.links,
    }


def cash_flow_graph(month: str | None = None, *, compact: bool = False) -> dict:
    """The money-flow graph for `month` (YYYY-MM; default the last full month)."""
    ym = month or default_month()
    if not _MONTH_RE.match(ym):
        raise ValidationError(f"month must be YYYY-MM, got {ym!r}")
    graph = build_graph(month_summary(ym), compact=compact)
    return {"month": ym, "base_currency": base_currency(), **graph}
