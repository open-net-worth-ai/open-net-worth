"""Everything the reconcile needs to translate between the two sides, built once per run: how
YNAB accounts and categories map to ledger accounts, categories and envelopes (the migration's
own resolution, never creating anything), and YNAB's transactions turned into the ledger rows
the migration would have booked (``mapping.plan_transactions`` + ``render_piece``)."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services.ynab_migration import apply, mapping


@dataclass
class Context:
    meta: dict
    mirror: dict[str, dict]
    currency: str
    cat_plan: dict
    acct_ids: dict[str, str]  # YNAB account id -> ledger account id
    ynab_acct: dict[str, str]  # ledger account id -> YNAB account id
    cat_ids: dict[str, str | None]  # YNAB category id -> ledger category id
    env_of: dict[str, str]  # YNAB category id -> envelope id (plain categories)
    card_env: dict[str, str]  # YNAB card-payment category id -> payment envelope id
    names: dict
    sub_parent: dict[str, str] = field(default_factory=dict)
    link_of: dict[str, str] = field(
        default_factory=dict
    )  # link target id -> the txn pointing at it
    openings: dict[str, str] = field(
        default_factory=dict
    )  # YNAB account id -> Starting Balance txn

    @property
    def live_accounts(self) -> list[dict]:
        return [a for a in self.meta["accounts"] if not a.get("deleted")]


def _groups(meta: dict) -> list[dict]:
    cats: dict[str, list] = {}
    for c in meta["categories"]:
        cats.setdefault(c["category_group_id"], []).append(c)
    return [{**g, "categories": cats.get(g["id"], [])} for g in meta["groups"]]


def _openings(mirror: dict[str, dict]) -> dict[str, str]:
    found: dict[str, str] = {}
    for t in sorted(mirror.values(), key=lambda t: (t["date"], t["id"])):
        is_sb = (t.get("payee_name") or "").strip().lower() == mapping.SB_PAYEE
        if is_sb and not t.get("transfer_account_id"):
            found.setdefault(t["account_id"], t["id"])
    return found


def build(state: dict, meta: dict, mirror: dict[str, dict], assigned: dict) -> Context:
    used_months = [{"categories": [{"id": cid, "budgeted": 1} for (_, cid) in assigned]}]
    cat_plan = mapping.plan_categories(_groups(meta), list(mirror.values()), used_months)
    envs, _ = apply.resolve_envelopes({"categories": cat_plan["categories"]}, create=False)
    card = apply.card_envelopes(cat_plan)
    accounts = ledger_service.accounts.query_accounts(include_archived=True)
    acct_ids = {
        e["ref"]: a["id"]
        for a in accounts
        for e in a.get("external_ids") or []
        if e.get("source") == mapping.SOURCE
    }
    sub_parent = {s["id"]: t["id"] for t in mirror.values() for s in t.get("subtransactions") or []}
    link_of = {
        t["transfer_transaction_id"]: t["id"]
        for t in mirror.values()
        if t.get("transfer_transaction_id")
    }
    return Context(
        meta=meta,
        mirror=mirror,
        currency=state["currency"],
        cat_plan=cat_plan,
        acct_ids=acct_ids,
        ynab_acct={v: k for k, v in acct_ids.items()},
        cat_ids={cid: v["category"] for cid, v in envs.items()},
        env_of={cid: v["envelope"] for cid, v in envs.items() if v["envelope"]},
        card_env={cid: v["envelope"] for cid, v in card.items()},
        names={
            "accounts": {a["id"]: a["name"] for a in accounts},
            "categories": {c["id"]: c["label"] for c in categories_service.all_category_rows()},
        },
        sub_parent=sub_parent,
        link_of=link_of,
        openings=_openings(mirror),
    )


def top_of(ctx: Context, ref: str) -> str | None:
    """The mirror transaction a YNAB id belongs to (itself, or the split a line belongs to)."""
    if ref in ctx.mirror:
        return ref
    return ctx.sub_parent.get(ref)


def _partners(ctx: Context, t: dict) -> set[str]:
    ids = {t["id"], *(s["id"] for s in t.get("subtransactions") or [])}
    targets = {
        x
        for x in [t.get("transfer_transaction_id")]
        + [s.get("transfer_transaction_id") for s in t.get("subtransactions") or []]
        if x
    }
    targets |= {ctx.link_of[i] for i in ids if i in ctx.link_of}
    return {top for x in targets if (top := top_of(ctx, x))}


def neighbourhood(ctx: Context, refs: set[str]) -> list[dict]:
    """The mirror transactions that decide how ``refs`` map to ledger rows: themselves, the
    other leg of every transfer, and the Starting Balance rows ``plan_transactions`` sets aside."""
    ids = {top for r in refs if (top := top_of(ctx, r))}
    for _ in range(2):
        for tid in list(ids):
            ids |= _partners(ctx, ctx.mirror[tid])
    ids |= {
        ctx.openings[ctx.mirror[i]["account_id"]]
        for i in ids
        if ctx.mirror[i]["account_id"] in ctx.openings
    }
    return [ctx.mirror[i] for i in ids]


def pieces(ctx: Context, refs: set[str]) -> list[dict]:
    """Each piece is one ledger transaction the migration would book for these YNAB ids."""
    rows = neighbourhood(ctx, refs)
    found, _, _ = mapping.plan_transactions(rows, ctx.live_accounts, ctx.cat_plan)
    return found


def render(ctx: Context, piece: dict) -> dict:
    """The ledger row for a piece; ``KeyError`` when it touches an account not in the ledger."""
    return mapping.render_piece(piece, ctx.acct_ids, ctx.cat_ids, ctx.currency)
