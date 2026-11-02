"""Importing a YNAB plan's categories into a book that already has some: each YNAB category goes
with one of the book's categories (or an envelope, or is new), decided in the review, and the
pairing is kept for the two-way sync. Against an isolated database and the synthetic YNAB API
(tests/ynab_stub.py)."""

from __future__ import annotations

import pytest

from backend.db.ynab_config_store import YnabConfigStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning, ynab_pairs, ynab_sync
from backend.services.aggregation import ynab as ynab_service
from tests import ynab_stub
from tests.test_ynab_migration_service import run

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)


@pytest.fixture
def ynab(db_isolated, monkeypatch):
    server, base_url, _calls = ynab_stub.serve()
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN})
    yield server
    server.shutdown()


def cat(label):
    return next(c for c in categories_service.all_category_rows() if c["label"] == label)


def env(name):
    return next(e for e in envelopes_service.all_envelopes() if e["name"] == name)


def group(name):
    return next(g for g in planning.list_groups() if g["name"] == name)


def change(**kw):
    return planning.apply_budget_change(kw)


def rows(job):
    return {c["name"]: c for g in job["preview"]["category_groups"] for c in g["categories"]}


def ynab_id(ynab, key):
    return ynab.state["C"][key]


def pair_of(ynab, key):
    return ynab_pairs.load(ynab_stub.PLAN)[0][ynab_id(ynab, key)]


def test_a_category_matches_by_name_ignoring_emoji_and_nothing_is_moved_or_renamed(ynab):
    ynab_stub.ynab_rename_category(ynab.state, "groc", "🛒 Groceries")
    groceries, home = cat("Groceries"), env("Groceries")
    row = rows(run("preview"))["🛒 Groceries"]
    assert row["link"] == {"ref": f"cat:{groceries['id']}", "name": "Groceries", "kind": "category"}
    assert row["note"] == "Budget syncs with envelope Groceries" and not row["chosen"]
    assert run()["status"] == "done"
    assert cat("Groceries") == groceries and env("Groceries") == home  # same names, same place
    assert not any(c["label"] == "🛒 Groceries" for c in categories_service.all_category_rows())
    assert pair_of(ynab, "groc")["category"] == groceries["id"]
    assert pair_of(ynab, "groc")["env"] == home["id"]


def test_a_matched_envelope_gets_the_ynab_assigned_and_the_pairing_survives_the_sync_turning_on(
    ynab,
):
    job = run(sync_direction="both")
    assert job["status"] == "done", job["error"]
    assert pair_of(ynab, "rent")["category"] == cat("Rent")["id"]
    assert ynab_pairs.load(ynab_stub.PLAN)[1]  # groups too, kept across turning the sync on
    assert ynab_sync.status()["enabled"]


def test_the_groups_are_matched_by_name_too(ynab):
    start = group("Needs")
    row = run("preview")["preview"]["category_groups"]
    assert next(g for g in row if g["name"] == "Needs")["group"] == {
        "id": start["id"],
        "name": "Needs",
    }
    assert next(g for g in row if g["name"] == "Bills")["group"]["name"] == "Bills"
    run()
    assert env("Groceries")["group_id"] == start["id"]


def test_a_chosen_new_category_is_created_instead_of_matching(ynab):
    old = cat("Groceries")
    job = run(category_links={ynab_id(ynab, "groc"): ""})
    assert job["status"] == "done", job["error"]
    assert cat("Groceries") == old
    made = pair_of(ynab, "groc")
    assert made["category"] != old["id"]
    assert categories_service.all_category_rows() and any(
        c["id"] == made["category"] and c["label"] == "Groceries (YNAB)"
        for c in categories_service.all_category_rows()
    )


def test_an_empty_envelope_of_the_same_name_gets_the_category_inside_it(ynab):
    e = change(type="envelope_create", name="Fun Money", group_id=group("Wants")["id"])["envelope"]
    row = rows(run("preview"))["Fun Money"]
    assert row["link"]["ref"] == f"env:{e['id']}"
    assert row["note"] == "Budget syncs with envelope Fun Money"
    run()
    assert env("Fun Money")["id"] == e["id"] and len(env("Fun Money")["category_ids"]) == 1
    assert pair_of(ynab, "fun")["env"] == e["id"]


def test_a_busy_envelope_is_offered_but_not_chosen_and_choosing_it_says_what_it_means(ynab):
    e = change(type="envelope_create", name="Fun Money", group_id=group("Wants")["id"])["envelope"]
    other = change(type="category_create", label="Games", kind="expense")
    change(type="envelope_add_category", envelope_id=e["id"], category_id=other["id"])
    row = rows(run("preview"))["Fun Money"]
    assert row["link"] is None and "holds other categories" in row["note"]
    choice = {ynab_id(ynab, "fun"): f"env:{e['id']}"}
    chosen = rows(run("preview", category_links=choice))["Fun Money"]
    assert (
        chosen["note"] == "Envelope Fun Money holds other categories too — its Assigned won't sync"
    )
    job = run(category_links=choice)
    assert job["status"] == "done", job["error"]
    assert len(env("Fun Money")["category_ids"]) == 2 and pair_of(ynab, "fun")["env"] is None
    assert "Fun Money holds several categories, so its Assigned isn't synced with YNAB." in [
        line["line"] for line in job["log"]
    ]
    month = ynab.state["d"](0, 1)[:7]
    assert planning.assignments_store.month_map(month).get(e["id"]) in (None, 0)


def test_a_category_that_matches_two_is_never_guessed_and_the_import_waits_for_a_pick(ynab):
    first = cat("Dining out")
    second = change(type="category_create", label="🍽 Dining out", kind="expense")
    row = rows(run("preview"))["Dining out"]
    assert row["ask"] and row["link"] is None
    assert [o["ref"] for o in row["options"]] == [f"cat:{first['id']}", f"cat:{second['id']}"]
    failed = run()
    assert failed["status"] == "failed"
    assert (
        failed["error"] == "YNAB's Dining out matches more than one of your categories. Pick one."
    )
    job = run(category_links={ynab_id(ynab, "dine"): f"cat:{second['id']}"})
    assert job["status"] == "done", job["error"]
    assert pair_of(ynab, "dine")["category"] == second["id"]


def test_a_category_in_no_envelope_is_not_budgeted_unless_ynab_budgets_it(ynab):
    loose = change(type="category_create", label="Fun Money", kind="expense")  # no envelope
    row = rows(run("preview"))["Fun Money"]
    assert row["note"] == "Gets an envelope here, since YNAB has money assigned to it"
    run()
    assert pair_of(ynab, "fun")["category"] == loose["id"]
    assert env("Fun Money")["category_ids"] == [loose["id"]]


def test_a_choice_that_is_not_available_is_refused(ynab):
    mine = cat("Groceries")["id"]
    both = {ynab_id(ynab, "groc"): f"cat:{mine}", ynab_id(ynab, "dine"): f"cat:{mine}"}
    assert run(category_links=both)["status"] == "failed"
    stray = run("preview", category_links={"nope": ""})
    assert stray["status"] == "failed" and "not in the plan" in stray["error"]


def test_importing_again_keeps_the_pairing(ynab):
    run()
    before = ynab_pairs.load(ynab_stub.PLAN)
    again = run()
    assert again["status"] == "done" and ynab_pairs.load(ynab_stub.PLAN) == before
