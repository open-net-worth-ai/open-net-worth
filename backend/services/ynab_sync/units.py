"""Pairs each ledger transaction with YNAB's version of it, and the last state both agreed on.

A ``Unit`` is the sync's atom: one ledger transaction (or one YNAB transaction with no ledger
row yet). ``decide`` is the whole reconcile rule — three states, compared by equality:

  * nobody changed it               -> nothing
  * only the ledger changed it      -> push to YNAB
  * only YNAB changed it            -> pull into the ledger
  * both changed it, same result    -> adopt (record it as agreed)
  * both changed it, different      -> a conflict for the review list
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.db.transaction_audit_store import TransactionAuditStore
from backend.services import ledger as ledger_service
from backend.services.ynab_migration.mapping import SOURCE
from backend.services.ynab_sync import context
from backend.services.ynab_sync.canon import Canon, canon, reconstruct_original
from backend.services.ynab_sync.context import Context
from backend.services.ynab_sync.state import SURFACE

CREATABLE = ("expense", "income", "transfer")


@dataclass
class Unit:
    onw_id: str | None
    row: dict | None
    refs: list[str]
    base: Canon
    onw: Canon
    ynab: Canon
    rendered: dict | None = None
    blocked: str | None = None
    attach: bool = False  # the ledger row must take ``refs`` (it claimed an unowned YNAB row)

    @property
    def key(self) -> str:
        return self.onw_id or f"y:{self.refs[0]}"


def decide(base: Canon, onw: Canon, ynab: Canon) -> str:
    onw_changed, ynab_changed = onw != base, ynab != base
    if onw_changed and ynab_changed:
        return "adopt" if onw == ynab else "conflict"
    if onw_changed:
        return "push"
    return "pull" if ynab_changed else "noop"


def ynab_refs(row: dict) -> list[str]:
    """A ledger row's YNAB transaction ids ("<account>:balance" adjustments are not YNAB's)."""
    return [
        e["ref"]
        for e in row.get("external_refs") or []
        if e.get("source") == SOURCE and ":" not in e["ref"]
    ]


def _eligible(ctx: Context, row: dict, openings: frozenset[str]) -> bool:
    """Never an account's Starting Balance, and nothing outside the accounts YNAB knows."""
    if row["id"] in openings:
        return False
    legs = [row.get("from_account_id"), row.get("to_account_id")]
    return all(a in ctx.ynab_acct for a in legs if a)


def audit_touched(since: int, upto: int) -> set[str]:
    return {
        e["txn_id"] for e in TransactionAuditStore().since(since, upto=upto, not_surface=SURFACE)
    }


def first_run_bases(rows: list[dict]) -> dict[str, Canon]:
    """What each linked row looked like when the migration booked it: the audited edits since
    are undone field by field. Edits are the ``update``/``delete``/``restore`` events of the row
    itself — a migrated row's own ``create`` event is its cut-off by construction, so no
    timestamp is compared. (The migration's single follow-up write, ``mark_cleared``, shows up
    as an edit too; harmless, because the comparison is of values, not of whether an event exists.)"""
    edits = TransactionAuditStore().edits_of({r["id"] for r in rows}, not_surface=SURFACE)
    return {r["id"]: canon(reconstruct_original(r, edits.get(r["id"], []))) for r in rows}


def _render(ctx: Context, piece: dict) -> tuple[dict | None, str | None]:
    try:
        rendered = context.render(ctx, piece)
    except KeyError:
        return None, "the transaction is in a YNAB account that is not in this book"
    wanted = [piece["category"]] if piece["category"] else []
    wanted += [s["category"] for s in piece["splits"] or []]
    for cid in wanted:
        if not ctx.cat_ids.get(cid):
            name = next((c["name"] for c in ctx.meta["categories"] if c["id"] == cid), cid)
            return None, f"the YNAB category '{name}' has no counterpart in this book"
    return rendered, None


def _with_lines(ctx: Context, ids: set[str]) -> set[str]:
    lines = {
        s["id"] for t in ids if t in ctx.mirror for s in ctx.mirror[t].get("subtransactions") or []
    }
    return ids | lines


def build(
    ctx: Context,
    rows: dict[str, dict],
    bases: dict[str, Canon],
    index: dict[str, str],
    touched: set[str],
    removed_ok: set[str],
) -> list[Unit]:
    """Units for every ledger row in ``rows`` plus every YNAB piece YNAB touched that no row
    claims. ``removed_ok`` is the YNAB ids whose absence means "deleted in YNAB", not "unknown"."""
    openings = ledger_service.opening_txn_ids()
    fresh = _with_lines(ctx, touched)
    pieces = context.pieces(ctx, fresh | {r for row in rows.values() for r in ynab_refs(row)})
    claimed: dict[str, tuple[dict | None, str | None]] = {}
    units: list[Unit] = []
    for piece in pieces:
        owner = next((index[r] for r in piece["refs"] if r in index), None)
        rendered, blocked = _render(ctx, piece)
        if owner is not None and owner not in claimed:
            claimed[owner] = (rendered, blocked)
        elif owner is None and fresh & set(piece["refs"]):
            ynab = canon(rendered) if rendered else None
            units.append(Unit(None, None, list(piece["refs"]), None, None, ynab, rendered, blocked))
    known = lambda r: r in ctx.mirror or r in ctx.sub_parent or r in removed_ok  # noqa: E731
    for onw_id, row in rows.items():
        # Refs YNAB never held (a restored row's stale ones) are not links.
        refs = [r for r in ynab_refs(row) if known(r)]
        if not _eligible(ctx, row, openings) or (not refs and row["kind"] not in CREATABLE):
            continue
        onw = None if row.get("deleted") else canon(row)
        base = bases.get(onw_id)
        if onw_id in claimed:
            rendered, blocked = claimed[onw_id]
            ynab = canon(rendered) if rendered else base
            units.append(Unit(onw_id, row, refs, base, onw, ynab, rendered, blocked))
        elif refs and any(r in removed_ok for r in refs):
            units.append(Unit(onw_id, row, refs, base, onw, None))
        elif refs and base is not None:
            units.append(Unit(onw_id, row, refs, base, onw, base))
        elif not refs and base is None and onw is not None:
            units.append(Unit(onw_id, row, [], None, onw, None))
    return _claim_unowned(units)


def _claim_unowned(units: list[Unit]) -> list[Unit]:
    """A ledger row with no YNAB link that is identical to a YNAB row nobody owns IS that row:
    a create whose answer was lost (a timeout after YNAB saved it). It takes the YNAB ids instead
    of being sent a second time, and the YNAB row is not pulled in as a duplicate."""
    free = [u for u in units if u.onw_id is None and u.ynab is not None and not u.blocked]
    for mine in [u for u in units if u.onw_id and not u.refs and u.base is None and u.onw]:
        twin = next((f for f in free if f.ynab == mine.onw), None)
        if twin is None:
            continue
        free.remove(twin)
        units.remove(twin)
        mine.refs, mine.ynab, mine.rendered, mine.attach = (
            list(twin.refs),
            twin.ynab,
            twin.rendered,
            True,
        )
    return units
