"""How YNAB's categories and category groups pair with ours (ADR
``2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines``, amendment 2026-11-03).

A YNAB category pairs with one of OUR CATEGORIES, by id, for good; a YNAB category group with one
of our ENVELOPE GROUPS. The import writes the pairs, the two-way sync reads and keeps them. They
live in the sync's own baseline table (``c:<ynab id>`` / ``g:<ynab id>`` documents) so they
survive turning the sync on, and each carries the state both sides last agreed on — per side,
because a matched pair legitimately differs ("🛒 Groceries" in YNAB, "Groceries" here).

A leaf of ``services``: the migration and the sync both import it, and it imports neither."""

from __future__ import annotations

import unicodedata

from backend.db.ynab_sync_store import YnabBaselineStore


def norm(name) -> str:
    """A name as people compare it: emoji and symbols dropped, case folded, spaces collapsed."""
    text = unicodedata.normalize("NFKC", str(name or "")).casefold()
    kept = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in text)
    return " ".join(kept.split())


# YNAB groups that only park categories the book has no group for; none of them ever pairs with
# (or is made into) a book group, and no category is followed into one.
NOT_BUDGETED = "Not budgeted"
HIDDEN_HERE = "Hidden in Open Net Worth"
PARKING = ("not budgeted", "hidden in open net worth", "hidden categories")


def is_parking_name(name) -> bool:
    return norm(name) in PARKING


def cat_doc(
    plan_id: str,
    ynab_id: str,
    category_id: str,
    env_id: str | None,
    onw: dict,
    ynab: dict,
    resume: bool = False,
) -> dict:
    """``env``: the envelope holding ONLY this category — the one whose Assigned syncs with YNAB —
    or ``None`` (in no envelope, or in one that holds other categories too). ``had_env``: it has
    been in one since it was paired. ``resume``: its Assigned has no agreed baseline yet (it was
    paired with a YNAB category that already existed), so the next comparison starts afresh and
    every difference goes to review."""
    return {
        "id": f"c:{ynab_id}",
        "plan": plan_id,
        "category": category_id,
        "env": env_id,
        "had_env": env_id is not None,
        "resume": resume,
        "gone": False,
        "onw": onw,
        "ynab": ynab,
    }


def group_doc(plan_id: str, ynab_id: str, group_id: str, onw_name: str, ynab_name: str) -> dict:
    return {
        "id": f"g:{ynab_id}",
        "plan": plan_id,
        "group": group_id,
        "gone": False,
        "onw": {"name": onw_name},
        "ynab": {"name": ynab_name},
    }


def load(plan_id: str | None) -> tuple[dict[str, dict], dict[str, dict]]:
    return YnabBaselineStore().pairs(plan_id)


def save(docs: list[dict]) -> None:
    store = YnabBaselineStore()
    for doc in docs:
        store.upsert(doc)


def declined(plan_id: str | None) -> set[str]:
    """What the person chose to leave out: ``y:<ynab category id>`` (not added here) and
    ``o:<category id>`` (not added to YNAB)."""
    return YnabBaselineStore().declined(plan_id)


def decline_doc(plan_id: str | None, key: str) -> dict:
    return {"id": f"x:{key}", "plan": plan_id}


def keep_for() -> list[dict]:
    """Every plan's pair and decision documents, to carry across a reset of the sync's other
    state: how a YNAB category pairs with a category here is knowledge about the book, so
    pointing the sync at another plan and back (or importing again) does not lose it."""
    return YnabBaselineStore().pair_documents()
