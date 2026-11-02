"""The categories half of the YNAB import: each YNAB category goes where ``category_links``
decided, and the pairing is written for the two-way sync (``ynab_pairs``). Idempotent: a category
an earlier run paired keeps its pairing and nothing is created for it again.

Nothing that already exists is renamed, moved or hidden to fit: a YNAB category paired with one
of the book's own categories changes none of its envelope, group or name. What is created is
new — a category with an envelope of its own in the book's group for the YNAB group (matched by
name, or created), or a category inside the envelope the person pointed it at."""

from __future__ import annotations

import logging
from collections import Counter

from backend.core.errors import ValidationError
from backend.services import ynab_pairs
from backend.services.ynab_migration import category_links
from backend.services.ynab_migration.category_make import (
    Groups,
    give_envelope,
    make_category,
    make_category_in,
)
from backend.services.ynab_migration.category_match import Book

logger = logging.getLogger(__name__)


def shared_note(envelope_name: str) -> str:
    return f"{envelope_name} holds several categories, so its Assigned isn't synced with YNAB."


def _category_for(c: dict, d: dict, book: Book, groups: Groups, made: Counter) -> dict:
    kind = d["kind"]
    if kind == "ambiguous":
        raise ValidationError(
            f"YNAB's {c['name']} matches more than one of your categories. Pick one."
        )
    if kind in ("linked", "category"):
        return d["category"]
    if kind == "envelope":
        made["categories"] += 1
        return make_category_in(book, d["envelope"], c["label"], c["hidden"])
    group_id = groups.ensure(book, c["group_id"], c["group_name"])
    cat, _ = make_category(book, c["label"], group_id, c["hidden"])
    made["categories"] += 1
    made["envelopes"] += 1
    return cat


def _pair(
    plan_id: str, c: dict, cat: dict, env: dict | None, held: dict | None, old: dict | None
) -> dict:
    env_id = env["id"] if env else None
    if old is not None and not old.get("gone") and old["category"] == cat["id"]:
        return {**old, "env": env_id}
    # The book side as the sync reads it (``category_base.ostate``): the group of whichever
    # envelope holds the category, shared or not. Both sides are recorded as they stand.
    onw = {
        "name": cat["label"],
        "hidden": bool(cat.get("hidden")),
        "group": held["group_id"] if held else None,
    }
    ynab = {"name": c["name"], "hidden": c["hidden"], "group": c["group_id"]}
    return ynab_pairs.cat_doc(plan_id, c["id"], cat["id"], env_id, onw, ynab)


def apply(plan: dict, decisions: dict[str, dict], plan_id: str, book: Book):
    """Returns ``(YNAB category id -> {category, envelope}, what was created, notes for the
    log, the ids of every category the plan uses)``. ``envelope`` is the one holding only that
    category — the one whose Assigned the plan fills — else ``None``."""
    old_cats, old_groups = ynab_pairs.load(plan_id)
    # Each pair is saved as soon as it exists, so a run that stops half way leaves nothing created
    # and unpaired for the next one to create again.
    groups = Groups(plan_id, old_groups, lambda doc: ynab_pairs.save([doc]))
    for c in plan["categories"]:
        groups.match(book, c["group_id"], c["group_name"])
    out: dict[str, dict] = {}
    made: Counter = Counter()
    notes: dict[str, str] = {}
    for c in plan["categories"]:
        cat = _category_for(c, decisions[c["id"]], book, groups, made)
        cat = book.rows[cat["id"]]
        env, held = book.solo_envelope(cat["id"]), book.env_of.get(cat["id"])
        if held is None and category_links.has_assigned(plan, c["id"]):
            group_id = groups.ensure(book, c["group_id"], c["group_name"])
            env = give_envelope(book, cat, group_id)
            made["envelopes"] += 1
        elif env is None and held is not None:
            notes[held["id"]] = shared_note(held["name"])
        held = book.env_of.get(cat["id"])
        ynab_pairs.save([_pair(plan_id, c, book.rows[cat["id"]], env, held, old_cats.get(c["id"]))])
        out[c["id"]] = {"category": cat["id"], "envelope": env["id"] if env else None}
    # What the log says now is not said again by the first sync.
    ynab_pairs.save([ynab_pairs.decline_doc(plan_id, f"n:{env_id}") for env_id in notes])
    return out, made, list(notes.values()), {v["category"] for v in out.values()}


def lookup(plan: dict, plan_id: str, book: Book) -> dict[str, dict]:
    """The same map for a plan that is already imported (verify): read from the pairing, never
    written."""
    cats, _ = ynab_pairs.load(plan_id)
    out = {}
    for c in plan["categories"]:
        pair = cats.get(c["id"])
        if pair is None or pair["category"] not in book.rows:
            out[c["id"]] = {"category": None, "envelope": None}
            continue
        env = book.solo_envelope(pair["category"])
        out[c["id"]] = {"category": pair["category"], "envelope": env["id"] if env else None}
    return out
