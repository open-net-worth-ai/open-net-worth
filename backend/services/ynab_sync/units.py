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
from backend.services.budget_app import current
from backend.services.ynab_migration.mapping import clean
from backend.services.ynab_sync import context
from backend.services.ynab_sync.canon import Canon, canon, reconstruct_original
from backend.services.ynab_sync.context import Context

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
    # Linked, with no baseline, and the app no longer holds it: only the person can say which side
    # is right (``links_without_baseline``).
    missing: bool = False

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


def comparable(c: Canon) -> Canon:
    """A canonical form as an app that stores text its own way can be expected to hold it: a payee
    whatever its case, and text only as long as the request carried it. A row here and its copy
    there that differ by no more than this are the same, and neither side has changed it."""
    if c is None or not current().caps.normalises_text:
        return c
    splits = c.get("splits")
    return {
        **c,
        "description": clean(c["description"][:200], 200).casefold(),
        "memo": clean(c["memo"][:500], 500),
        "splits": [[s[0], s[1], clean(s[2][:500], 500)] for s in splits] if splits else splits,
    }


def decide_unit(u: "Unit") -> str:
    return decide(comparable(u.base), comparable(u.onw), comparable(u.ynab))


def ynab_refs(row: dict) -> list[str]:
    """A ledger row's YNAB transaction ids ("<account>:balance" adjustments are not YNAB's)."""
    source = current().source
    return [
        e["ref"]
        for e in row.get("external_refs") or []
        if e.get("source") == source and ":" not in e["ref"]
    ]


def _eligible(ctx: Context, row: dict, openings: frozenset[str]) -> bool:
    """Never an account's Starting Balance, and nothing outside the accounts YNAB knows."""
    if row["id"] in openings:
        return False
    legs = [row.get("from_account_id"), row.get("to_account_id")]
    return all(a in ctx.ynab_acct for a in legs if a)


def audit_touched(since: int, upto: int) -> set[str]:
    return {
        e["txn_id"]
        for e in TransactionAuditStore().since(since, upto=upto, not_surface=current().surface)
    }


def first_run_bases(rows: list[dict]) -> dict[str, Canon]:
    """What each linked row looked like when the migration booked it: the audited edits since
    are undone field by field. Edits are the ``update``/``delete``/``restore`` events of the row
    itself — a migrated row's own ``create`` event is its cut-off by construction, so no
    timestamp is compared. (The migration's single follow-up write, ``mark_cleared``, shows up
    as an edit too; harmless, because the comparison is of values, not of whether an event exists.)"""
    edits = TransactionAuditStore().edits_of({r["id"] for r in rows}, not_surface=current().surface)
    return {r["id"]: canon(reconstruct_original(r, edits.get(r["id"], []))) for r in rows}


def _render(ctx: Context, piece: dict) -> tuple[dict | None, str | None]:
    try:
        rendered = context.render(ctx, piece)
    except KeyError:
        app = current()
        return (
            None,
            f"the transaction is in {app.article.lower()} {app.name} account that is not in this book",
        )
    wanted = [piece["category"]] if piece["category"] else []
    wanted += [s["category"] for s in piece["splits"] or []]
    for cid in wanted:
        if not ctx.cat_ids.get(cid):
            name = next((c["name"] for c in ctx.meta["categories"] if c["id"] == cid), cid)
            return None, f"The {current().name} category {name} isn't in this book yet."
    return rendered, None


def _income_view(ctx: Context, rendered: dict | None, ref: Canon) -> dict | None:
    """YNAB books an inflow without a category as Ready to Assign, and a row in a category that
    is not in YNAB goes there uncategorized, so it cannot say which category the ledger row has:
    the ledger's own stands, and is not read as a change. An app with income categories says."""
    mine = ref.get("category") if ref else None
    says_income = current().caps.income_categories
    is_income = rendered and rendered.get("kind") == "income" and mine in ctx.income
    if (
        rendered
        and not rendered.get("category")
        and ((is_income and not says_income) or mine in ctx.unsent)
    ):
        return {**rendered, "category": ref["category"]}
    return rendered


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
    held = lambda r: r in ctx.mirror or r in ctx.sub_parent  # noqa: E731
    keeps_links = current().caps.links_without_baseline
    for onw_id, row in rows.items():
        # Refs YNAB never held (a restored row's stale ones) are not links.
        refs = [r for r in ynab_refs(row) if known(r)]
        if not _eligible(ctx, row, openings) or (not refs and row["kind"] not in CREATABLE):
            continue
        onw = None if row.get("deleted") else canon(row)
        base = bases.get(onw_id)
        if onw_id in claimed:
            rendered, blocked = claimed[onw_id]
            rendered = _income_view(ctx, rendered, onw or base)
            ynab = canon(rendered) if rendered else base
            units.append(Unit(onw_id, row, refs, base, onw, ynab, rendered, blocked))
        elif (
            keeps_links
            and base is None
            and onw is not None
            and (linked := ynab_refs(row))
            and not any(held(r) for r in linked)
        ):
            units.append(Unit(onw_id, row, linked, None, onw, None, missing=True))
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
    free = [
        (u, comparable(u.ynab))
        for u in units
        if u.onw_id is None and u.ynab is not None and not u.blocked
    ]
    for mine in [u for u in units if u.onw_id and not u.refs and u.base is None and u.onw]:
        wanted = comparable(mine.onw)
        found = next((f for f in free if f[1] == wanted), None)
        if found is None:
            continue
        free.remove(found)
        twin = found[0]
        units.remove(twin)
        mine.refs, mine.ynab, mine.rendered, mine.attach = (
            list(twin.refs),
            twin.ynab,
            twin.rendered,
            True,
        )
    return units
