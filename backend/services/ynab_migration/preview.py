"""What the review shows before an import, beyond the counts in ``mapping.preview_summary``:
each category named under its group, and the transactions as they would be booked. Read from
the mapped plan, so it is exactly what an import writes — never a second reading of YNAB."""

from __future__ import annotations

from backend.services.ynab_migration.mapping import amt

# A run record holds the preview; a long YNAB history is listed newest first, up to this many.
LIST_CAP = 500


def category_groups(plan: dict, rows: dict[str, dict], matches: dict) -> list[dict]:
    """Each YNAB group with the group here it matches (if any) and its categories, each with
    where it goes (``category_links.rows``)."""
    groups: dict[str, dict] = {}
    for c in plan["categories"]:
        match = matches.get(c["group_id"])
        group = groups.setdefault(
            c["group_id"],
            {
                "name": c["group"],
                "group": None if match is None else {"id": match["id"], "name": match["name"]},
                "categories": [],
            },
        )
        group["categories"].append(
            {
                "id": c["id"],
                "label": c["label"],
                "name": c["name"],
                "hidden": c["hidden"],
                **rows[c["id"]],
            }
        )
    return list(groups.values())


def _row(p: dict, accounts: dict[str, str], labels: dict[str, str]) -> dict:
    out, inn = p.get("from"), p.get("to")
    if p["kind"] == "transfer":
        account, to_account, amount = accounts.get(out[0]), accounts.get(inn[0]), amt(out[1])
    elif out:
        account, to_account, amount = accounts.get(out[0]), None, -amt(out[1])
    else:
        account, to_account, amount = accounts.get(inn[0]), None, amt(inn[1])
    if p["splits"]:
        category = f"Split ({len(p['splits'])})"
    else:
        category = labels.get(p["category"]) if p["category"] else None
    return {
        "date": p["date"],
        "kind": p["kind"],
        "account": account,
        "to_account": to_account,
        "payee": p["description"] or None,
        "category": category,
        "amount": amount,
        "memo": p["memo"] or None,
    }


def transaction_list(plan: dict) -> list[dict]:
    accounts = {a["id"]: a["payload"]["name"] for a in plan["accounts"]}
    labels = {c["id"]: c["label"] for c in plan["categories"]}
    newest = sorted(plan["pieces"], key=lambda p: p["date"], reverse=True)[:LIST_CAP]
    return [_row(p, accounts, labels) for p in newest]


def details(plan: dict) -> dict:
    return {"transaction_list": transaction_list(plan)}
