"""Ledger transaction -> the YNAB request body that makes YNAB say the same (the pure half of
the push). Everything YNAB's API cannot express raises ``Unsupported`` with the reason the review
list shows, instead of sending something close: it ignores a split's date and amount, cannot
edit split lines, and takes no future dates. An app that can (``Capabilities``) is sent them."""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.services.budget_app import current
from backend.services.ynab_sync import recorder
from backend.services.ynab_sync.canon import changed_fields
from backend.services.ynab_sync.context import Context, top_of

FLAG = "red"  # every transaction the sync creates or changes carries this flag in YNAB


class Unsupported(Exception):
    """A change YNAB's API cannot take; ``str(exc)`` is the reason for the person."""


class NotYet(Unsupported):
    """A change YNAB can take once its category exists there: it waits for a later run."""


def _milli(cents: int, outflow: bool) -> int:
    return -cents * 10 if outflow else cents * 10


def _category_id(ctx: Context, onw_category: str | None) -> str | None:
    """The YNAB category paired with ours. An income category has none, as YNAB has no income
    categories: its rows go in as inflows, which YNAB books as Ready to Assign."""
    if not onw_category or (onw_category in ctx.income and not current().caps.income_categories):
        return None
    if onw_category in ctx.ynab_cat:
        return ctx.ynab_cat[onw_category]
    label, app = ctx.names["categories"].get(onw_category, onw_category), current().name
    if onw_category in ctx.unsent:
        if onw_category not in ctx.said:
            ctx.said.add(onw_category)
            recorder.note(
                label,
                f"Transactions in {label} go to {app} uncategorized, as {label} isn't in {app}.",
            )
        return None
    raise NotYet(f"The category {label} isn't in {app} yet.")


def _signed(n: dict) -> tuple[list, bool]:
    leg = n["to"] or n["from"]
    return leg, n["to"] is None


def _check_plain_shape(ctx: Context, n: dict) -> None:
    app = current().name
    if n["kind"] not in ("expense", "income", "transfer"):
        raise Unsupported(f"a {n['kind']} transaction has no {app} equivalent")
    if not current().caps.future_dates and n["date"] > user_today().isoformat():
        raise Unsupported(f"{app} does not accept transactions dated in the future")
    for leg in (n["from"], n["to"]):
        if leg and leg[0] not in ctx.ynab_acct:
            raise Unsupported(f"the account is not linked to a {app} account")


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


def _flag() -> dict:
    return {"flag_color": FLAG} if current().caps.flag_trail else {}


def _say_blank_inflow(ctx: Context, n: dict) -> None:
    """Where income has real categories, an inflow with none stays out of the budget there."""
    payee = n["description"]
    if (
        not current().caps.income_categories
        or n["kind"] != "income"
        or n["category"]
        or n["splits"]
    ):
        return
    if payee not in ctx.said:
        ctx.said.add(payee)
        app = current().name
        recorder.note(
            payee or "(no payee)",
            f"This inflow has no category here, so it goes to {app} with none, and doesn't count "
            f"towards {app}'s To Budget. Give it an income category here or in {app}.",
        )


def _categorized_transfer(ctx: Context, n: dict, dest: dict, body: dict) -> dict | None:
    """A transfer with a category, between a budget account and another. The category sits on the
    budget leg, so that leg is the row sent."""
    origin = next((a for a in ctx.meta["accounts"] if a["id"] == ctx.ynab_acct[n["from"][0]]), None)
    if not (current().caps.categorized_transfers and n["category"] and origin):
        return None
    category = _category_id(ctx, n["category"])
    if origin.get("on_budget") and not dest.get("on_budget"):
        return {
            **body,
            "account_id": origin["id"],
            "payee_id": dest["transfer_payee_id"],
            "amount": _milli(n["from"][1], True),
            "category_id": category,
        }
    if dest.get("on_budget") and not origin.get("on_budget") and origin.get("transfer_payee_id"):
        return {
            **body,
            "account_id": dest["id"],
            "payee_id": origin["transfer_payee_id"],
            "amount": _milli(n["to"][1], False),
            "category_id": category,
        }
    return None


def create_payload(ctx: Context, n: dict, currencies: set[str]) -> dict:
    _check_plain_shape(ctx, n)
    _say_blank_inflow(ctx, n)
    if currencies - {ctx.currency}:
        raise Unsupported(
            f"{current().name} only holds {ctx.currency}; this transaction is in {', '.join(sorted(currencies))}"
        )
    leg, outflow = _signed(n)
    body = {
        "date": n["date"],
        "memo": n["memo"][:500] or None,
        "cleared": "cleared" if n["cleared"] else "uncleared",
        "approved": True,
        **_flag(),
    }
    if n["kind"] == "transfer":
        dest = next(
            (a for a in ctx.meta["accounts"] if a["id"] == ctx.ynab_acct.get(n["to"][0])), None
        )
        if n["from"][1] != n["to"][1] or not dest or not dest.get("transfer_payee_id"):
            raise Unsupported(f"this transfer cannot be expressed as a {current().name} transfer")
        if categorized := _categorized_transfer(ctx, n, dest, body):
            return categorized
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


def split_edit(ctx: Context, top: dict, n: dict) -> dict:
    """The whole of a split, for an app that rewrites all its lines together: its account, date,
    amount, payee, and every line. ``subtransactions`` empty makes it a plain row again."""
    leg, outflow = _signed(n)
    op = {
        "id": top["id"],
        "date": n["date"],
        "amount": _milli(leg[1], outflow),
        "category_id": None if n["splits"] else _category_id(ctx, n["category"]),
        "subtransactions": [
            {
                "amount": _milli(c, outflow),
                "category_id": _category_id(ctx, cat),
                "memo": memo[:500] or None,
            }
            for cat, c, memo in n["splits"] or []
        ],
    }
    if ctx.ynab_acct[leg[0]] != top["account_id"]:
        op["account_id"] = ctx.ynab_acct[leg[0]]
    if n["description"]:
        op["payee_name"] = n["description"][:200]
    return op


def whole_split(top: dict, b: dict | None) -> bool:
    """A rewrite replaces every line and a delete takes them all, so either can only stand for a
    row that is all of them: no transfer line, and as many lines as the row agreed on. A ledger
    row's refs can't say: one made here holds only the parent's, and a line that became a transfer
    in the app is still among them."""
    lines = [s for s in top.get("subtransactions") or [] if not s.get("deleted")]
    if any(s.get("transfer_account_id") or s.get("transfer_transaction_id") for s in lines):
        return False
    return sum(1 for s in lines if s.get("amount")) == len((b or {}).get("splits") or [None])


def _plain_fields(ctx: Context, top: dict, b: dict, n: dict, changed: list[str]) -> dict:
    op: dict = {}
    if "kind" in changed and "transfer" in (b["kind"], n["kind"]):
        raise Unsupported(
            f"turning a transaction into a transfer (or back) cannot be sent to {current().name}"
        )
    if n["splits"]:
        raise Unsupported(f"{current().name}'s API cannot split an existing transaction")
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
        raise Unsupported(
            f"changing a transfer's accounts or category cannot be sent to {current().name}"
        )
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
    app = current().name
    changed = changed_fields(b, n)
    shape, tops = classify(ctx, refs)
    if shape == "complex":
        raise Unsupported(f"this is one part of a larger {app} split; edit it in {app}")
    top = ctx.mirror[tops[0]]
    edits_splits = current().caps.edit_splits
    if shape == "split":
        extra = set(changed) - {"description", "memo", "cleared"}
        if extra and not edits_splits:
            raise Unsupported(
                f"{app}'s API cannot change a split's date, amount, category or lines; "
                f"change it in {app}, or keep {app}'s version"
            )
        if extra:
            if not whole_split(top, b):
                raise Unsupported(f"this is one part of a larger {app} split; edit it in {app}")
            ops = [split_edit(ctx, top, n)]
        else:
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
    elif shape == "plain" and n["splits"] and edits_splits:
        ops = [split_edit(ctx, top, n)]
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
        op.update(**_flag(), approved=row.get("approved", True))
    return ops
