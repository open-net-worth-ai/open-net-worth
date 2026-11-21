"""A transaction changed on both sides since the last sync, in different fields: both changes
stand. The app's fields are written here first (through the ledger's choke, under the sync's
audit surface, as any pull), then ours are sent as an ordinary update from the app's version. Only
the same field changed to different values on the two sides is a conflict for the person.

Fields that travel together are one: a category and a split's lines, and a row's kind with its
two legs (amounts and accounts). One side may change a group, never both."""

from __future__ import annotations

from dataclasses import dataclass, replace

from backend.services import ledger as ledger_service
from backend.services.budget_app import current
from backend.services.ynab_sync import pull
from backend.services.ynab_sync.canon import FIELDS, canon
from backend.services.ynab_sync.units import Unit, comparable

_GROUPS = (
    ("category", "splits"),
    ("kind", "from", "to"),
    ("date",),
    ("description",),
    ("memo",),
    ("cleared",),
)
assert {f for g in _GROUPS for f in g} == set(FIELDS)


@dataclass(frozen=True)
class Plan:
    theirs: list[str]  # fields the app changed: written here
    mine: list[str]  # fields changed here: sent to the app


def plan(u: Unit) -> Plan | None:
    """How to keep both sides' changes, or ``None`` when they touch the same group of fields
    (or the row is new, removed, or not linked on either side)."""
    if None in (u.base, u.onw, u.ynab) or not (u.onw_id and u.refs and u.row) or u.blocked:
        return None
    b, o, y = comparable(u.base), comparable(u.onw), comparable(u.ynab)
    theirs: list[str] = []
    mine: list[str] = []
    for group in _GROUPS:
        here = any(o[f] != b[f] for f in group)
        there = any(y[f] != b[f] for f in group)
        if here and there and any(o[f] != y[f] for f in group):
            return None
        if there and not here:
            theirs += [f for f in group if y[f] != b[f]]
        elif here and not there:
            mine += [f for f in group if o[f] != b[f]]
    if not theirs or not mine:
        return None
    return Plan(theirs, mine)


def apply_theirs(u: Unit, p: Plan) -> Unit:
    """Write the app's fields here; returns the unit to send ours with, from the app's version.
    Raises what ``pull`` raises (``pull.LedgerChanged`` when the row moved under the run)."""
    row = pull._unchanged(u.row)
    patch = pull.row_changes(u.rendered, row, p.theirs)
    given = {"labels_as_given": True} if current().caps.labels_as_given_on_pull else {}
    updated = ledger_service.update_transaction(row["id"], patch, **given) if patch else row
    return replace(u, row=updated or row, base=u.ynab, onw=canon(updated or row))
