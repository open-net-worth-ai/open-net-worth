"""Descriptors for clean store-backed types (class (a) per RFC §4.2).

payee, watchlist, automation, target — these have a JsonStore and a
simple all() / active() / list_hydrated() source.

Both search_override and resolve_override are set to the existing bespoke
functions (imported verbatim from providers.py / resolve.py) to guarantee
byte-identical output per the HARD RULE: a bespoke function may only be deleted
AFTER its descriptor produces byte-identical output, including sublabels.  The
generic search loop (make_search) is NOT used for these types — it would produce
functionally equivalent but not provably byte-identical results (e.g. watchlist
creates one WatchlistStore() per row in a sublabel_fn, while the bespoke
search_watchlist reuses one store instance across the whole loop).

RFC 2026-06-25-lucius-generic-entity-registry §4.2 class (a).
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.providers import (
    search_automation,
    search_payee,
    search_target,
    search_watchlist,
)
from backend.services.mentions.resolve import (
    _resolve_automation,
    _resolve_payee,
    _resolve_target,
    _resolve_watchlist,
)
from backend.services.mentions.types import SearchQuery

# ── payee (order=4) ──────────────────────────────────────────────────────────


def _payee_rows(query: SearchQuery) -> list[dict]:
    from backend.services import payee_registry

    # list_registry() rows carry the LIVE txn_count (recomputed from the ledger), matching the
    # search_override + Manage Payees — not the stored monotonic usage stat.
    return payee_registry.list_registry(with_usage=True)


def _payee_sublabel(row: dict) -> str:
    txn_count = row.get("txn_count", 0) or 0
    return f"{txn_count} txns"


register(
    EntityDescriptor(
        type="payee",
        label="Payee",
        icon="◉",
        order=4,
        tone="invest",
        surfaces=frozenset({"entity"}),
        rows=_payee_rows,
        id_field="id",
        label_field="name",
        search_fields=("name",),
        sublabel_fn=_payee_sublabel,
        search_override=search_payee,
        resolve_override=_resolve_payee,
        fields={
            "name": FieldSpec("Name"),
            "default_categories": FieldSpec("Default categories"),
            "txn_count": FieldSpec("Transactions", "qty"),
            "hidden": FieldSpec("Hidden"),
        },
    )
)

# ── watchlist (order=11) ──────────────────────────────────────────────────────


def _watchlist_rows(query: SearchQuery) -> list[dict]:
    from backend.db.watchlist_store import WatchlistStore

    return WatchlistStore().list_watchlists()


def _watchlist_sublabel(row: dict) -> str:
    from backend.db.watchlist_store import WatchlistStore

    description = (row.get("description") or "").strip()
    if description:
        return description
    try:
        item_count = WatchlistStore().count(watchlist_id=row["id"])
    except Exception:
        item_count = 0
    return f"{item_count} items"


register(
    EntityDescriptor(
        type="watchlist",
        label="Watchlist",
        icon="☆",
        order=11,
        tone="crypto",
        surfaces=frozenset({"entity"}),
        rows=_watchlist_rows,
        id_field="id",
        label_field="name",
        search_fields=("name",),
        sublabel_fn=_watchlist_sublabel,
        search_override=search_watchlist,
        resolve_override=_resolve_watchlist,
        fields={
            "name": FieldSpec("Name"),
            "description": FieldSpec("Description"),
            "item_count": FieldSpec("Items", "qty"),
            "is_auto_sync": FieldSpec("Auto sync"),
        },
    )
)

# ── automation (order=12) ─────────────────────────────────────────────────────


def _automation_rows(query: SearchQuery) -> list[dict]:
    from backend.db.automation_store import AutomationStore

    return AutomationStore().all()


def _automation_sublabel(row: dict) -> str:
    from backend.services.automations.schedules import describe_trigger

    return describe_trigger(row.get("trigger") or {"kind": "manual"})


register(
    EntityDescriptor(
        type="automation",
        label="Automation",
        icon="◷",
        order=12,
        tone="credit",
        surfaces=frozenset({"entity"}),
        rows=_automation_rows,
        id_field="id",
        label_field="name",
        search_fields=("name", "description", "instructions"),
        sublabel_fn=_automation_sublabel,
        search_override=search_automation,
        resolve_override=_resolve_automation,
        fields={
            "name": FieldSpec("Name"),
            "description": FieldSpec("Description"),
            "instructions": FieldSpec("Instructions"),
            "trigger": FieldSpec("Trigger"),
            "next_run_at": FieldSpec("Next run", "date"),
            "status": FieldSpec("Status"),
        },
    )
)

# ── target (order=8) ──────────────────────────────────────────────────────────
# Placed here (store_backed.py) but order=8 so it sorts between budget_month(7)
# and allocation_slice(9) in the registry — targets.list_hydrated() is the source.


def _target_rows(query: SearchQuery) -> list[dict]:
    from backend.services import targets

    return targets.list_hydrated()


def _target_sublabel(row: dict) -> str:
    remaining = row.get("remaining_base", 0) or 0
    status = row.get("status") or ""
    return f"{remaining} remaining • {status}".strip(" •")


register(
    EntityDescriptor(
        type="target",
        label="Target",
        icon="◎",
        order=8,
        tone="retire",
        surfaces=frozenset({"entity"}),
        rows=_target_rows,
        id_field="id",
        label_field="name",
        search_fields=("name",),
        sublabel_fn=_target_sublabel,
        search_override=search_target,
        resolve_override=_resolve_target,
        fields={
            "name": FieldSpec("Name"),
            "target_amount_base": FieldSpec("Target amount", "money"),
            "target_date": FieldSpec("Target date", "date"),
            "current_base": FieldSpec("Current", "money"),
            "remaining_base": FieldSpec("Remaining", "money"),
            "required_monthly_base": FieldSpec("Required monthly", "money"),
            "months_remaining": FieldSpec("Months remaining", "qty"),
            "status": FieldSpec("Status"),
        },
    )
)
