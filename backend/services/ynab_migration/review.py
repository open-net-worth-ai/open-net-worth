"""What the review shows before an import, beyond the counts in ``mapping.preview_summary``:
which book accounts and categories each YNAB one goes with, and what an import would do to the
book itself. The job's preview run and its import both read it, so the import does exactly what
the review showed."""

from __future__ import annotations

from backend.services import ynab_pairs
from backend.services.ynab_migration import (
    apply,
    category_links,
    category_match,
    guards,
    links,
    mapping,
    preview,
)


def book_changes(plan: dict, state: str, kept: frozenset[str], kept_groups: set[str]) -> dict:
    """What an import would do to the book beyond adding the plan: a currency change and the
    removal of untouched starter budget items. Shown in the preview and used by the import.
    ``kept`` are the book's categories the plan is paired with, ``kept_groups`` the names (lower
    case) of its groups that are."""
    money = guards.money_data()
    base = apply.ledger_service.base_currency().upper()
    changes = {
        "base_currency": base,
        "currency_change": base != plan["currency"] and not money,
        "currency_blocked": base != plan["currency"] and bool(money),
        "money_data": money,
        "prune": {"categories": [], "groups": []},
    }
    if state == "fresh":
        wanted = {c["label"].lower() for c in plan["categories"]}
        prune = guards.starter_prune_plan(wanted, kept)
        kept_names = {c["group"].lower() for c in plan["categories"]} | kept_groups
        changes["prune"] = {
            "categories": [c["label"] for c in prune["categories"]],
            "groups": [g["name"] for g in guards.starter_groups_left_empty(prune, kept_names)],
        }
    return changes


def match_scope(plan: dict, existing: list[dict], chosen: dict[str, dict]) -> tuple:
    """(YNAB account id -> account id to match with, YNAB account id -> account row for each
    account whose rows are matched): every account linked before this run or chosen now. One
    the import is about to create has nothing to match, so a placeholder stands in for it."""
    mine = {pa["id"] for pa in plan["accounts"]}
    rows = {links.ynab_ref(a): a for a in existing if links.ynab_ref(a) in mine} | chosen
    ids = {
        pa["id"]: rows[pa["id"]]["id"] if pa["id"] in rows else f"new:{pa['id']}"
        for pa in plan["accounts"]
    }
    return ids, rows


def preview_accounts(plan: dict, existing: list[dict], chosen: dict, same_as: dict) -> list:
    """The summary's accounts, each with what the import would do with it."""
    ids, rows = match_scope(plan, existing, chosen)
    decisions = links.match(plan, ids, set(rows), same_as)
    matched = links.summary(plan, rows, decisions)
    options = links.candidates(plan, existing)
    out = []
    for pa, shown in zip(plan["accounts"], mapping.preview_summary(plan)["accounts"]):
        row = rows.get(pa["id"])
        out.append(
            {
                **shown,
                "id": pa["id"],
                "link": None if row is None else {"id": row["id"], "name": row["name"]},
                "chosen": pa["id"] in chosen,
                "candidates": options[pa["id"]],
                "match": matched.get(pa["id"]),
            }
        )
    return out


def category_scope(plan: dict, plan_id: str, choice: dict) -> tuple:
    """(the book as the categories see it, each YNAB category's decision, the pairs an earlier
    import wrote)."""
    book = category_match.Book()
    cats, groups = ynab_pairs.load(plan_id)
    decisions = category_links.decide(plan, choice.get("category_links"), book, cats)
    return book, decisions, groups


def preview_categories(plan: dict, book, decisions: dict, groups: dict) -> dict:
    matches = category_links.group_matches(plan, book, groups)
    kept = frozenset(
        d["category"]["id"] for d in decisions.values() if d["category"] is not None
    ) | frozenset(
        cid for d in decisions.values() if d["envelope"] for cid in d["envelope"]["category_ids"]
    )
    kept_groups = {g["name"].lower() for g in matches.values() if g}
    return {
        "category_groups": preview.category_groups(
            plan, category_links.rows(plan, decisions, book), matches
        ),
        "category_choices": category_links.choices(book),
        "kept": kept,
        "kept_groups": kept_groups,
    }
