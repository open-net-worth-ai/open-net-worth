"""The writes that give a YNAB category a home in the book, for the import and the two-way sync
alike: a new category with an envelope of its own, a category inside an existing empty (or
offered) envelope, an envelope for a category that had none, hiding a category. Every write goes
through ``planning.apply_budget_change`` or the categories service, and none of them renames or
moves anything that already exists."""

from __future__ import annotations

from backend.services import categories as categories_service
from backend.services import planning, ynab_pairs
from backend.services.ynab_migration.category_match import Book
from backend.services.ynab_migration.mapping import RESERVED_GROUPS
from backend.services.ynab_pairs import is_parking_name, norm


def _change(**change) -> dict:
    return planning.apply_budget_change(change)


def make_category(
    book: Book, label: str, group_id: str | None, hidden: bool = False, budgeted: bool = True
):
    """A new expense category and (``budgeted``) an envelope of its own, named alike, in
    ``group_id``. Returns ``(category, envelope or None)``."""
    label = book.free_label(label)
    cat = _change(type="category_create", label=label, kind="expense")
    env = None
    if budgeted:
        env = _change(type="envelope_create", name=label, group_id=group_id or "")["envelope"]
        _change(type="envelope_add_category", envelope_id=env["id"], category_id=cat["id"])
    if hidden:
        categories_service.set_category_hidden(cat["id"], True)
    book.refresh()
    return cat, env


def make_category_in(book: Book, envelope: dict, label: str, hidden: bool = False) -> dict:
    """A new category inside an existing envelope (its only one when it was empty)."""
    cat = _change(
        type="category_create", label=book.free_label(label, envelopes=False), kind="expense"
    )
    _change(type="envelope_add_category", envelope_id=envelope["id"], category_id=cat["id"])
    if hidden:
        categories_service.set_category_hidden(cat["id"], True)
    book.refresh()
    return cat


def give_envelope(book: Book, category: dict, group_id: str | None) -> dict:
    """An envelope of its own for a category that sits in none."""
    name = book.free_envelope_name(category["label"])
    env = _change(type="envelope_create", name=name, group_id=group_id or "")["envelope"]
    _change(type="envelope_add_category", envelope_id=env["id"], category_id=category["id"])
    book.refresh()
    return env


def set_hidden(category_ids: list[str], hidden: bool) -> None:
    for cid in category_ids:
        categories_service.set_category_hidden(cid, hidden)


def our_group_name(ynab_name: str) -> str:
    """The app keeps Commitments and Credit Card Payments for itself."""
    name = ynab_name.strip()
    return f"{name} (YNAB)" if norm(name) in RESERVED_GROUPS else name


class Groups:
    """How YNAB's category groups pair with the book's envelope groups during one run. ``docs``
    holds the pair documents by YNAB id; ``save`` is called with each one created or changed."""

    def __init__(self, plan_id: str, docs: dict[str, dict], save) -> None:
        self.plan_id, self.docs, self._save = plan_id, docs, save

    def our_id(self, ynab_id: str) -> str | None:
        doc = self.docs.get(ynab_id)
        return doc["group"] if doc and not doc.get("gone") else None

    def ynab_id(self, group_id: str | None) -> str | None:
        return next(
            (y for y, d in self.docs.items() if d["group"] == group_id and not d.get("gone")), None
        )

    def pair(self, ynab_id: str, ynab_name: str, group: dict) -> str:
        doc = ynab_pairs.group_doc(self.plan_id, ynab_id, group["id"], group["name"], ynab_name)
        self.docs[ynab_id] = doc
        self._save(doc)
        return group["id"]

    def match(self, book: Book, ynab_id: str, ynab_name: str) -> str | None:
        """The paired group, or the one group of the same name not paired yet (now paired). A
        YNAB parking group is never paired with a book group."""
        if is_parking_name(ynab_name):
            return None
        if (found := self.our_id(ynab_id)) is not None:
            return found
        taken = {d["group"] for d in self.docs.values() if not d.get("gone")}
        same = [
            g for g in book.groups if g["id"] not in taken and norm(g["name"]) == norm(ynab_name)
        ]
        return self.pair(ynab_id, ynab_name, same[0]) if len(same) == 1 else None

    def ensure(self, book: Book, ynab_id: str, ynab_name: str) -> str:
        found = self.match(book, ynab_id, ynab_name)
        if found is not None:
            return found
        group = _change(type="group_create", name=our_group_name(ynab_name))["group"]
        book.refresh()
        return self.pair(ynab_id, ynab_name, group)
