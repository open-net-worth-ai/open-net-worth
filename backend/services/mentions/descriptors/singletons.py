"""Descriptors for synthesized-singleton types (class (d) per RFC §4.2).

net_worth, returns, allocation_slice, category, budget_month, envelope — these
have no backing store and/or synthesize their ref from live data.  Both
search_override and resolve_override are set to the existing functions imported
verbatim so the generic loop is bypassed and behavior is byte-identical to Phase 0.

RFC 2026-06-25-lucius-generic-entity-registry §4.2 class (d).
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.providers import (
    search_allocation_slice,
    search_budget_month,
    search_category,
    search_envelope,
    search_net_worth,
    search_returns,
)
from backend.services.mentions.resolve import (
    _resolve_allocation_slice,
    _resolve_budget_month,
    _resolve_category,
    _resolve_envelope,
    _resolve_net_worth,
    _resolve_returns,
)
from backend.services.mentions.types import SearchQuery

# ── net_worth (order=0) ───────────────────────────────────────────────────────

register(
    EntityDescriptor(
        type="net_worth",
        label="Net Worth",
        icon="▲",
        order=0,
        tone="lucius",
        surfaces=frozenset({"entity"}),
        search_override=search_net_worth,
        resolve_override=_resolve_net_worth,
        fields={
            "net_worth": FieldSpec("Net worth", "money"),
            "assets": FieldSpec("Assets", "money"),
            "liabilities": FieldSpec("Liabilities", "money"),
            "market_positions": FieldSpec("Market positions", "money"),
            # Split out of "assets" so a real-estate/receivable/settlement-cash account
            # is never read as plain "cash" (the account_classes fix).
            "cash": FieldSpec("Cash (bank/e-wallet)", "money"),
            "settlement_cash": FieldSpec(
                "Brokerage/exchange settlement cash (negative = margin)", "money"
            ),
            "fixed_deposit": FieldSpec("Fixed deposits", "money"),
            "retirement": FieldSpec("Retirement", "money"),
            "property": FieldSpec("Property", "money"),
            "receivable": FieldSpec("Loans receivable", "money"),
        },
    )
)

# ── holding (order=1) — class (b) service-hydrated; defined in financials.py ─
# ── instrument (order=2) — class (b) service-hydrated; defined in financials.py ─
# ── account (order=3) — class (b) service-hydrated; defined in financials.py ─
# ── transaction (order=5) — class (b) service-hydrated; defined in financials.py ─

# ── category (order=6) ────────────────────────────────────────────────────────


def _category_rows(query: SearchQuery) -> list[dict]:
    """Every stored category row (hidden included) — the store's own identity rows, not the
    budget-row projection ``search_category`` builds. Read only by the receipt-chip label
    path; search/resolve keep their overrides."""
    from backend.services.categories import all_category_rows

    return all_category_rows()


register(
    EntityDescriptor(
        type="category",
        label="Category",
        icon="▣",
        order=6,
        tone="invest",
        surfaces=frozenset({"entity"}),
        rows=_category_rows,
        id_field="id",
        label_field="label",
        search_override=search_category,
        resolve_override=_resolve_category,
        fields={
            "key": FieldSpec("Key", "id", show_in_popover=False),
            "label": FieldSpec("Name"),
            "group": FieldSpec("Group"),
            "month": FieldSpec("Month"),
            "assigned": FieldSpec("Assigned", "money"),
            "activity": FieldSpec("Activity", "money"),
            "available": FieldSpec("Available", "money"),
            "target": FieldSpec("Target", "money"),
            "status": FieldSpec("Status"),
            # store-identity fallback for a category with no budget row (ADR 2026-07-11)
            "kind": FieldSpec("Kind", "enum"),
            "budgeted": FieldSpec("Budgeted"),
        },
    )
)

# ── budget_month (order=7) ────────────────────────────────────────────────────

register(
    EntityDescriptor(
        type="budget_month",
        label="Budget Month",
        icon="◱",
        order=7,
        tone="credit",
        surfaces=frozenset({"entity"}),
        search_override=search_budget_month,
        resolve_override=_resolve_budget_month,
        fields={
            "month": FieldSpec("Month"),
            "ready_to_assign": FieldSpec("Ready to assign", "money"),
            "pre_budget_baseline": FieldSpec("Pre-budget baseline", "money"),
            "held_in_envelopes": FieldSpec("Held in envelopes", "money"),
            "held_outside_envelopes": FieldSpec("Held outside envelopes", "money"),
            "card_spend_not_covered": FieldSpec("Card spend not covered this month", "money"),
            "assigned_in_future_months": FieldSpec("Assigned in future months", "money"),
            # `flow` is the RTA identity reframed as YNAB's flow terms (carried-in / inflow /
            # cash overspending charged now), null at the earliest budgeted month — a nested
            # object with no sensible single-row rendering, so it's excluded from the popover
            # rather than stringifying to "[object Object]" (security review, 2026-09-13).
            "flow": FieldSpec(
                "Flow (carried-in / inflow / cash overspending)", "text", show_in_popover=False
            ),
            # This month's own cash overspending, still open — charged to next month's Ready to
            # Assign at the roll if still unresolved.
            "cash_overspending": FieldSpec("Cash overspending", "money"),
            "group_count": FieldSpec("Groups", "qty"),
        },
    )
)

# ── target (order=8) — class (a) clean store-backed; defined in store_backed.py ─

# ── allocation_slice (order=9) ────────────────────────────────────────────────

register(
    EntityDescriptor(
        type="allocation_slice",
        label="Allocation",
        icon="◭",
        order=9,
        tone="invest",
        surfaces=frozenset({"entity"}),
        search_override=search_allocation_slice,
        resolve_override=_resolve_allocation_slice,
        fields={
            "axis": FieldSpec("Axis"),
            "selector": FieldSpec("Selector"),
            "value_base": FieldSpec("Value", "money"),
            "holdings": FieldSpec("Holdings", "qty"),
        },
    )
)

# ── returns (order=10) ─────────────────────────────────────────────────────────

register(
    EntityDescriptor(
        type="returns",
        label="Returns",
        icon="↗",
        order=10,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        search_override=search_returns,
        resolve_override=_resolve_returns,
        fields={
            "period": FieldSpec("Period"),
            "twr": FieldSpec("TWR", "pct"),
            "mwr": FieldSpec("MWR", "pct"),
            "period_gain_base": FieldSpec("Period gain", "money"),
            "start_value": FieldSpec("Start value", "money"),
            "end_value": FieldSpec("End value", "money"),
        },
    )
)


def _envelope_rows(query: SearchQuery) -> list[dict]:
    """Raw envelope rows — ARCHIVED INCLUDED, deliberately.

    Only the receipt-chip label path (``services.mentions.labels``) reads this; search and
    resolve still go through their overrides. A turn that DELETED an envelope archives it,
    and the chip for that delete still has to say which envelope it was — filtering
    archived here would label exactly the row the user most needs named."""
    from backend.services import envelopes as envelopes_service

    return envelopes_service.all_envelopes()


# ── envelope (order=26) ────────────────────────────────────────────────────────
# order was 25, colliding with `disposal` (equity_lots.py). Two descriptors at the same
# order make their head-group / picker tie-break fall back to module IMPORT order, which
# is exactly the instability `order` exists to remove. equity_lots imports before
# singletons, so disposal already sorted first — 26 pins that sequence explicitly instead
# of leaving it to alphabetical luck in descriptors/__init__.py.
# The Plan tab's row identity (envelope⊥category full split). DISTINCT from the
# "category" type: a category mention is a transaction label's identity (and the
# category provider deliberately skips zero-category envelopes); an envelope
# mention is the budget row itself, so a ZERO-CATEGORY envelope (commitment pot,
# card-payment envelope, named savings pot) is mentionable only through this type.

register(
    EntityDescriptor(
        type="envelope",
        label="Envelope",
        icon="◫",
        order=26,
        tone="credit",
        surfaces=frozenset({"entity"}),
        rows=_envelope_rows,
        id_field="id",
        label_field="name",
        search_override=search_envelope,
        resolve_override=_resolve_envelope,
        fields={
            "key": FieldSpec("Key", "id", show_in_popover=False),
            "label": FieldSpec("Name"),
            "group": FieldSpec("Group"),
            "month": FieldSpec("Month"),
            "categories": FieldSpec("Linked categories", "qty"),
            "assigned": FieldSpec("Assigned", "money"),
            "activity": FieldSpec("Activity", "money"),
            "available": FieldSpec("Available", "money"),
            "target": FieldSpec("Target", "money"),
            "status": FieldSpec("Status"),
            "available_for_payment": FieldSpec("Available for payment", "money"),
            "underfunded": FieldSpec("Underfunded", "money"),
        },
    )
)
