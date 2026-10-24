"""Ledger transaction -> the YNAB request body that makes YNAB say the same (the pure half of
the push). Everything YNAB's API cannot express raises ``Unsupported`` with the reason the review
list shows, instead of sending something close: it ignores a split's date and amount, cannot
edit split lines, and takes no future dates."""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.services.ynab_sync.canon import changed_fields
from backend.services.ynab_sync.context import Context, top_of

FLAG = "red"  # every transaction the sync creates or changes carries this flag in YNAB


class Unsupported(Exception):
    """A change YNAB's API cannot take; ``str(exc)`` is the reason for the person."""


def _milli(cents: int, outflow: bool) -> int:
    return -cents * 10 if outflow else cents * 10


def _category_id(ctx: Context, onw_category: str | None) -> str | None:
    if not onw_category:
        return None
    for cid, mine in ctx.cat_ids.items():
        if mine == onw_category:
            return cid
    label = ctx.names["categories"].get(onw_category, onw_category)
    raise Unsupported(f"the category '{label}' has no counterpart in YNAB")


def _signed(n: dict) -> tuple[list, bool]:
    leg = n["to"] or n["from"]
    return leg, n["to"] is None


def _check_plain_shape(ctx: Context, n: dict) -> None:
    if n["kind"] not in ("expense", "income", "transfer"):
        raise Unsupported(f"a {n['kind']} transaction has no YNAB equivalent")
    if n["date"] > user_today().isoformat():
        raise Unsupported("YNAB does not accept transactions dated in the future")
    for leg in (n["from"], n["to"]):
        if leg and leg[0] not in ctx.ynab_acct:
            raise Unsupported("the account is not linked to a YNAB account")


def classify(ctx: Context, refs: list[str]) -> tuple[str, list[str]]:
    """``plain`` / ``transfer`` / ``split`` / ``complex`` for a linked ledger row, with the YNAB
    transaction ids it is built from. ``complex`` is a piece of a larger YNAB transaction (a
    split's own transfer line or loose line) that cannot be edited on its own."""
    tops = list(dict.fromkeys(t for r in refs if (t := top_of(ctx, r))))
    rows = [ctx.mirror[t] for t in tops]
    if len(tops) == 1 and rows[0].get("subtransactions") and rows[0]["id"] in refs:
        return "split", tops
    if (
        len(tops) == 1
        and not rows[0].get("subtransactions")
        and not rows[0].get("transfer_account_id")
    ):
        return "plain", tops
    if len(tops) == 2 and not any(r.get("subtransactions") for r in rows):
        if rows[0].get("transfer_transaction_id") == rows[1]["id"]:
            return "transfer", sorted(tops, key=lambda t: ctx.mirror[t]["amount"])
    return "complex", tops


def create_payload(ctx: Context, n: dict, currencies: set[str]) -> dict:
    _check_plain_shape(ctx, n)
    if currencies - {ctx.currency}:
        raise Unsupported(
            f"YNAB only holds {ctx.currency}; this transaction is in {', '.join(sorted(currencies))}"
        )
    leg, outflow = _signed(n)
    body = {
        "date": n["date"],
        "memo": n["memo"][:500] or None,
        "cleared": "cleared" if n["cleared"] else "uncleared",
        "approved": True,
        "flag_color": FLAG,
    }
    if n["kind"] == "transfer":
        dest = next(
            (a for a in ctx.meta["accounts"] if a["id"] == ctx.ynab_acct.get(n["to"][0])), None
        )
        if n["from"][1] != n["to"][1] or not dest or not dest.get("transfer_payee_id"):
            raise Unsupported("this transfer cannot be expressed as a YNAB transfer")
        return {
            **body,
            "account_id": ctx.ynab_acct[n["from"][0]],
            "payee_id": dest["transfer_payee_id"],
            "amount": _milli(n["from"][1], True),
        }
    body |= {
        "account_id": ctx.ynab_acct[leg[0]],
        "amount": _milli(leg[1], outflow),
        "payee_name": n["description"][:200] or None,
    }
    if n["splits"]:
        body["category_id"] = None
        body["subtransactions"] = [
            {
                "amount": _milli(c, outflow),
                "category_id": _category_id(ctx, cat),
                "memo": memo[:500] or None,
            }
            for cat, c, memo in n["splits"]
        ]
    else:
        body["category_id"] = _category_id(ctx, n["category"])
    return body


def _cleared(top: dict, now: bool) -> str | None:
    if now and top.get("cleared") == "reconciled":
        return None
    return "cleared" if now else "uncleared"


def _plain_fields(ctx: Context, top: dict, b: dict, n: dict, changed: list[str]) -> dict:
    op: dict = {}
    if "kind" in changed and "transfer" in (b["kind"], n["kind"]):
        raise Unsupported("turning a transaction into a transfer (or back) cannot be sent to YNAB")
    if n["splits"]:
        raise Unsupported("YNAB's API cannot split an existing transaction")
    leg, outflow = _signed(n)
    if {"from", "to", "kind"} & set(changed):
        op["amount"] = _milli(leg[1], outflow)
        if ctx.ynab_acct[leg[0]] != top["account_id"]:
            op["account_id"] = ctx.ynab_acct[leg[0]]
    if "description" in changed and n["description"]:
        op["payee_name"] = n["description"][:200]
    if {"category", "splits"} & set(changed):
        op["category_id"] = _category_id(ctx, n["category"])
    return op


def _transfer_ops(
    ctx: Context, tops: list[str], b: dict, n: dict, changed: list[str]
) -> list[dict]:
    if {"kind", "category", "splits"} & set(changed) or any(
        b[s] and n[s] and b[s][0] != n[s][0] for s in ("from", "to")
    ):
        raise Unsupported("changing a transfer's accounts or category cannot be sent to YNAB")
    ops = []
    for tid, outflow in zip(tops, (True, False)):
        op = {"id": tid}
        if {"from", "to"} & set(changed):
            op["amount"] = _milli(n["from"][1], outflow)
        ops.append(op)
    return ops


def update_payloads(ctx: Context, refs: list[str], b: dict, n: dict) -> list[dict]:
    """Request bodies (one per YNAB transaction) that bring YNAB from ``b`` to ``n``."""
    _check_plain_shape(ctx, n)
    changed = changed_fields(b, n)
    shape, tops = classify(ctx, refs)
    if shape == "complex":
        raise Unsupported("this is one part of a larger YNAB split; edit it in YNAB")
    top = ctx.mirror[tops[0]]
    if shape == "split":
        extra = set(changed) - {"description", "memo", "cleared"}
        if extra:
            raise Unsupported(
                "YNAB's API cannot change a split's date, amount, category or lines; "
                "change it in YNAB, or keep YNAB's version"
            )
        ops = [
            {
                "id": top["id"],
                **(
                    {"payee_name": n["description"][:200]}
                    if "description" in changed and n["description"]
                    else {}
                ),
            }
        ]
    elif shape == "plain":
        ops = [{"id": top["id"], **_plain_fields(ctx, top, b, n, changed)}]
    else:
        ops = _transfer_ops(ctx, tops, b, n, changed)
    for op in ops:
        row = ctx.mirror[op["id"]]
        if "date" in changed and shape != "split":
            op["date"] = n["date"]
        if "memo" in changed:
            op["memo"] = n["memo"][:500] or None
        if "cleared" in changed and (state := _cleared(row, n["cleared"])):
            op["cleared"] = state
        op.update(flag_color=FLAG, approved=row.get("approved", True))
    return ops
