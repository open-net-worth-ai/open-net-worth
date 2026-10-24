"""First-run category and budget-group template from the owner screenshots."""

from __future__ import annotations

import pytest

from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.category_store import CategoryStore
from backend.db.envelope_groups_store import EnvelopeGroupsStore
from backend.db.envelope_store import EnvelopeStore
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import STARTER_CATEGORIES
from backend.services.categories import ensure_builtins_seeded
from backend.services.envelopes import ensure_envelopes_migrated

pytestmark = pytest.mark.usefixtures("db_isolated")


def test_fresh_book_has_the_starter_categories_in_their_budget_groups():
    assert ensure_builtins_seeded() == len(STARTER_CATEGORIES)
    assert ensure_envelopes_migrated() == {"migrated": True, "envelope_count": 11}

    categories = CategoryStore().all()
    assert categories == STARTER_CATEGORIES

    groups = [g for g in EnvelopeGroupsStore().list_ordered() if not g.get("is_others")]
    assert [g["name"] for g in groups] == ["Bills", "Needs", "Wants"]
    assert [g["order"] for g in groups] == [0, 1, 2]
    assert all("categories" not in g for g in groups)

    by_id = {c["id"]: c["label"] for c in categories}
    envelopes = EnvelopeStore().list_ordered()
    by_group = {g["id"]: g["name"] for g in groups}
    assert [(by_group[e["group_id"]], e["name"]) for e in envelopes] == [
        ("Bills", "Rent/Mortgage"),
        ("Bills", "Phone & Internet"),
        ("Bills", "Utilities"),
        ("Needs", "Groceries"),
        ("Needs", "Medical expenses"),
        ("Needs", "Emergency fund"),
        ("Wants", "Subscriptions"),
        ("Wants", "Dining out"),
        ("Wants", "Entertainment"),
        ("Wants", "Vacation"),
        ("Wants", "Stuff I forgot to plan for"),
    ]
    assert all(len(e["category_ids"]) == 1 for e in envelopes)
    assert {e["category_ids"][0] for e in envelopes} == set(by_id) - {"split"}
    assert BudgetAssignmentsStore().all_map() == {}
    assert not ProfileStore().get_raw().get("category_targets")


def test_seed_is_one_time_and_does_not_change_an_existing_book():
    assert ensure_builtins_seeded() == len(STARTER_CATEGORIES)
    store = CategoryStore()
    first = store.all()[0]
    store.update_by_id(first["id"], {"label": "My rent"})

    assert ensure_builtins_seeded() == 0
    assert store.get(first["id"])["label"] == "My rent"
    assert len(EnvelopeGroupsStore().list_ordered()) == 3


def test_a_book_with_its_own_categories_does_not_get_the_template():
    own = {"id": "cat_abcdef123456", "label": "My category", "kind": "expense", "color": "#123456"}
    CategoryStore().insert(own)

    assert ensure_builtins_seeded() == 0
    assert [c["id"] for c in CategoryStore().all()] == [own["id"], "split"]  # sentinel only
    assert EnvelopeGroupsStore().all() == []


def test_starter_labels_are_plain_words():
    # The owner removed the emoji prefixes (2026-10-10): a label starts with a letter.
    assert all(c["label"][0].isalpha() for c in STARTER_CATEGORIES)


def test_seed_restores_a_missing_split_sentinel_without_touching_the_rest():
    ensure_builtins_seeded()
    store = CategoryStore()
    store.delete("split")
    before = store.all()

    assert ensure_builtins_seeded() == 0
    assert store.get("split")["label"] == "Split"
    assert [c for c in store.all() if c["id"] != "split"] == before
