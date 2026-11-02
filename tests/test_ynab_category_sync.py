"""The two-way sync of categories and category groups: a YNAB category pairs with one of the
book's categories by id, its budget with the envelope that holds only it, and a YNAB category
group with an envelope group. Each test migrates the stub plan with the real migration, then
changes one side or both. Against an isolated database and the synthetic YNAB API."""

from __future__ import annotations

import pytest

from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import import_runs, planning, ynab_pairs, ynab_sync
from tests import ynab_stub
from tests.test_ynab_sync_service import enable, row, sync, world  # noqa: F401

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)


def cat(label):
    return next(c for c in categories_service.all_category_rows() if c["label"] == label)


def env(name):
    return next(e for e in envelopes_service.all_envelopes() if e["name"] == name)


def group(name):
    return next(g for g in planning.list_groups() if g["name"] == name)


def change(**kw):
    return planning.apply_budget_change(kw)


def make(name, group_name="Needs", *, envelope=True):
    """A category (and, by default, an envelope of its own) made in the book by hand."""
    made = change(type="category_create", label=name, kind="expense")
    if envelope:
        e = change(type="envelope_create", name=name, group_id=group(group_name)["id"])["envelope"]
        change(type="envelope_add_category", envelope_id=e["id"], category_id=made["id"])
    return made


def ynab_group_of(world, name):  # noqa: F811
    c = ynab_stub.category_named(world.state, name)
    return next(g["name"] for g in world.state["groups"] if g["id"] == c["category_group_id"])


def record(run):
    return import_runs.get(run["run_id"])


def sent(world, method, suffix):  # noqa: F811
    return [w for w in world.writes if w[0] == method and w[1].endswith(suffix)]


def test_the_import_leaves_every_category_and_group_paired(world):  # noqa: F811
    cats, groups = ynab_pairs.load(ynab_stub.PLAN)
    assert len(cats) == 6 and len(groups) == 4
    assert cats[world.state["C"]["groc"]]["category"] == cat("Groceries")["id"]
    enable()
    assert sync()["summary"] == "Nothing changed" and world.writes == []
    assert ynab_pairs.load(ynab_stub.PLAN)[0].keys() == cats.keys()  # turning the sync on kept them


# ── from YNAB ──


def test_a_rename_in_ynab_renames_the_category_and_its_envelope_and_the_pairing_survives(world):  # noqa: F811
    enable()
    sync()
    before = cat("Groceries")["id"]
    ynab_stub.ynab_rename_category(world.state, "groc", "Food")
    assert sync()["summary"] == "1 category change" and world.writes == []
    assert cat("Food")["id"] == before and env("Food")
    assert not any(c["label"] == "Groceries" for c in categories_service.all_category_rows())
    ynab_stub.ynab_add(world.state, "chk", world.state["d"](0, 1), -30_000, "Corner shop", "groc")
    assert sync()["summary"] == "1 pulled"
    assert row("Corner shop")["category"] == before  # the rename did not unpair it


def test_a_new_ynab_category_is_created_here_and_its_transactions_book(world):  # noqa: F811
    enable()
    sync()
    ynab_stub.ynab_add_category(world.state, "needs", "Gifts", key="gifts")
    ynab_stub.ynab_add(world.state, "chk", world.state["d"](0, 1), -20_000, "Gift shop", "gifts")
    run = sync()
    assert run["summary"] == "1 pulled, 1 category change" and not ynab_sync.status()["conflicts"]
    assert env("Gifts")["category_ids"] == [cat("Gifts")["id"]]
    assert env("Gifts")["group_id"] == env("Groceries")["group_id"]  # YNAB's Needs, paired
    assert row("Gift shop")["category"] == cat("Gifts")["id"]
    assert world.writes == []


def test_hidden_or_deleted_in_ynab_hides_here_and_deletes_nothing(world):  # noqa: F811
    enable()
    sync()
    ynab_stub.ynab_hide_category(world.state, "dine")
    ynab_stub.ynab_delete_category(world.state, "util")
    assert sync()["summary"] == "2 category changes"
    assert cat("Dining out")["hidden"] and cat("Utilities")["hidden"]
    assert env("Utilities")  # the envelope and its money stay
    ynab_stub.ynab_hide_category(world.state, "dine", False)
    sync()
    assert not cat("Dining out").get("hidden")


def test_a_category_moved_to_another_group_in_ynab_moves_its_envelope(world):  # noqa: F811
    enable()
    sync()
    ynab_stub.ynab_move_category(world.state, "groc", "wants")
    assert sync()["summary"] == "1 category change"
    assert env("Groceries")["group_id"] == group("Wants")["id"]


def test_a_group_renamed_in_ynab_renames_the_envelope_group(world):  # noqa: F811
    enable()
    sync()
    ynab_stub.ynab_rename_group(world.state, "needs", "Essentials")
    sync()
    assert group("Essentials") and env("Groceries")["group_id"] == group("Essentials")["id"]


# ── from here ──


def test_a_new_category_here_is_created_in_ynab_in_the_group_paired_with_its_envelopes(world):  # noqa: F811
    enable()
    sync()
    made = make("Pets")
    run = sync()
    assert run["summary"] == "1 category change"
    assert ynab_group_of(world, "Pets") == "Needs"
    assert (
        ynab_pairs.load(ynab_stub.PLAN)[0][ynab_stub.category_named(world.state, "Pets")["id"]][
            "category"
        ]
        == made["id"]
    )
    assert sync()["summary"] == "Nothing changed" and len(sent(world, "POST", "/categories")) == 1


def test_rename_move_and_hide_here_reach_ynab(world):  # noqa: F811
    enable()
    sync()
    change(type="category_rename", key=cat("Groceries")["id"], new_label="Food")
    change(type="move_category", category=env("Food")["id"], group_id=group("Wants")["id"])
    categories_service.set_category_hidden(cat("Dining out")["id"], True)
    assert sync()["summary"] == "3 category changes"
    assert ynab_group_of(world, "Food") == "Wants"
    assert ynab_stub.category_named(world.state, "Dining out")["hidden"]
    categories_service.set_category_hidden(cat("Dining out")["id"], False)
    sync()
    assert not ynab_stub.category_named(world.state, "Dining out")["hidden"]


def test_a_category_deleted_or_merged_here_is_hidden_in_ynab(world):  # noqa: F811
    enable()
    sync()
    change(type="category_delete", key=cat("Fun Money")["id"], reassign=True)
    change(type="category_rename", key=cat("Dining out")["id"], merge_into=cat("Groceries")["id"])
    run = sync()
    assert record(run)["totals"]["categories"] == 2
    assert ynab_stub.category_named(world.state, "Fun Money")["hidden"]
    assert ynab_stub.category_named(world.state, "Dining out")["hidden"]
    assert not ynab_stub.category_named(world.state, "Groceries")["hidden"]
    assert row("Pizza")["category"] == cat("Groceries")["id"]  # relabelled, and still synced
    assert (
        ynab_stub.find(world.state, payee_name="Pizza")["category_id"] == world.state["C"]["groc"]
    )


def test_a_new_group_here_is_created_in_ynab_with_its_first_envelope(world):  # noqa: F811
    enable()
    sync()
    change(type="group_create", name="Treats")
    make("Cake", "Treats")
    sync()
    assert ynab_group_of(world, "Cake") == "Treats"
    change(type="group_rename", group_id=group("Treats")["id"], name="Sweets")
    sync()
    assert ynab_group_of(world, "Cake") == "Sweets"


def test_an_income_category_never_goes_to_ynab_and_its_rows_go_as_inflows(world):  # noqa: F811
    enable()
    sync()
    salary = change(type="category_create", label="Side gigs", kind="income")
    from backend.services import ledger as ledger_service

    chk = next(a for a in ledger_service.accounts.query_accounts() if a["name"] == "Checking")
    ledger_service.create_transaction(
        {
            "kind": "income", "date": world.state["d"](0, 1), "description": "Gig",
            "to_account_id": chk["id"], "to_amount": 50, "to_currency": "USD",
            "category": salary["id"],
        }
    )  # fmt: skip
    assert sync()["summary"] == "1 sent to YNAB"
    assert not sent(world, "POST", "/categories")
    ((_, _, body),) = sent(world, "POST", "/transactions")
    assert body["transactions"][0]["category_id"] is None
    assert row("Gig")["category"] == salary["id"] and sync()["summary"] == "Nothing changed"


# ── not budgeted ──


def test_a_category_in_no_envelope_goes_to_not_budgeted_and_money_from_ynab_gives_it_one(world):  # noqa: F811
    enable()
    sync()
    loose = make("Loose change", envelope=False)
    make_two = make("Odd jobs", envelope=False)
    sync()
    assert ynab_group_of(world, "Loose change") == "Not budgeted"
    assert ynab_group_of(world, "Odd jobs") == "Not budgeted"
    assert sum(1 for g in world.state["groups"] if g["name"] == "Not budgeted") == 1
    key = ynab_stub.category_named(world.state, "Loose change")["id"]
    ynab_stub.ynab_budget(world.state, 0, key, 25_000)
    run = sync()
    assert run["summary"].startswith("1 pulled") or "category change" in run["summary"]
    e = env("Loose change")
    assert e["category_ids"] == [loose["id"]]
    month = world.state["d"](0, 1)[:7]
    assert planning.assignments_store.month_map(month).get(e["id"]) == 25.0
    assert cat("Odd jobs") and make_two


def test_putting_an_unbudgeted_category_in_an_envelope_makes_it_follow_the_envelope(world):  # noqa: F811
    enable()
    sync()
    loose = make("Loose change", envelope=False)
    sync()
    e = change(type="envelope_create", name="Pocket", group_id=group("Wants")["id"])["envelope"]
    change(type="envelope_add_category", envelope_id=e["id"], category_id=loose["id"])
    sync()
    assert ynab_group_of(world, "Loose change") == "Wants"


# ── shared envelopes ──


def test_an_envelope_with_several_categories_syncs_labels_but_not_assigned_and_says_so_once(world):  # noqa: F811
    enable()
    sync()
    change(
        type="envelope_split_category",
        envelope_id=env("Dining out")["id"],
        category_id=cat("Dining out")["id"],
    )
    change(type="envelope_delete", envelope_id=env("Dining out")["id"])
    change(
        type="envelope_add_category",
        envelope_id=env("Groceries")["id"],
        category_id=cat("Dining out")["id"],
    )
    run = sync()
    lines = [n["reason"] for n in record(run)["notes"]]
    assert lines == ["Groceries holds several categories, so its Assigned isn't synced with YNAB."]
    assert not any("Assigned" in c["text"] for c in record(run).get("category_changes", []))
    assert not [n for n in record(sync()).get("notes", [])]  # said once, not every run
    planning.apply_budget_change(
        {"type": "assign", "month": max(planning.assignments_store.months()), "envelope_id": env("Groceries")["id"], "amount": 123.0}
    )  # fmt: skip
    sync()
    assert not [w for w in world.writes if "/months/" in w[1]]  # no longer sent


def test_a_transaction_in_a_category_inside_a_shared_envelope_round_trips_unchanged(world):  # noqa: F811
    enable()
    sync()
    change(
        type="envelope_split_category",
        envelope_id=env("Dining out")["id"],
        category_id=cat("Dining out")["id"],
    )
    change(type="envelope_delete", envelope_id=env("Dining out")["id"])
    change(
        type="envelope_add_category",
        envelope_id=env("Groceries")["id"],
        category_id=cat("Dining out")["id"],
    )
    sync()
    dining = cat("Dining out")["id"]
    assert row("Bistro")["category"] == dining
    from tests.test_ynab_sync_service import edit_onw

    edit_onw(row("Bistro")["id"], memo="team dinner")
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Pizza")["id"], memo="theirs"
    )
    assert sync()["summary"] == "1 sent to YNAB, 1 pulled"
    bistro = ynab_stub.find(world.state, payee_name="Bistro")
    assert bistro["category_id"] == world.state["C"]["dine"]
    assert row("Bistro")["category"] == dining and row("Pizza")["category"] == dining
    assert sync()["summary"] == "Nothing changed"


# ── both sides, and the direction ──


def test_the_same_rename_on_both_sides_goes_to_review_and_either_side_can_win(world):  # noqa: F811
    enable()
    sync()
    change(type="category_rename", key=cat("Groceries")["id"], new_label="Food")
    ynab_stub.ynab_rename_category(world.state, "groc", "Meals")
    assert sync()["summary"] == "1 needs review" and not world.writes
    (item,) = ynab_sync.status()["conflicts"]
    assert item["entity"] == "category" and item["kind"] == "conflict"
    assert item["fields"] == [{"label": "Name", "onw": "Food", "ynab": "Meals"}]
    assert "changed in both" in item["reason"]
    ynab_sync.resolve.resolve(item["id"], "onw")
    assert ynab_stub.category_named(world.state, "Food")["id"] == world.state["C"]["groc"]
    assert ynab_sync.status()["conflicts"] == [] and sync()["summary"] == "Nothing changed"


def test_keeping_ynabs_name_renames_here(world):  # noqa: F811
    enable()
    sync()
    change(type="category_rename", key=cat("Groceries")["id"], new_label="Food")
    ynab_stub.ynab_rename_category(world.state, "groc", "Meals")
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    ynab_sync.resolve.resolve(item["id"], "ynab")
    assert cat("Meals") and not world.writes


def test_from_ynab_never_writes_categories_to_ynab(world):  # noqa: F811
    ynab_sync.update_settings(True, "1h", ynab_stub.PLAN, "from_ynab")
    sync()
    make("Pets")
    change(type="category_rename", key=cat("Rent")["id"], new_label="Housing")
    ynab_stub.ynab_rename_category(world.state, "groc", "Food")
    run = sync()
    assert run["summary"] == "1 category change" and world.writes == []
    assert cat("Food") and cat("Housing")
    ynab_sync.update_settings(None, None, None, "both")
    sync()
    assert ynab_group_of(world, "Pets") == "Needs"  # held, then sent once both ways


def test_to_ynab_never_changes_the_book(world):  # noqa: F811
    ynab_sync.update_settings(True, "1h", ynab_stub.PLAN, "to_ynab")
    sync()
    make("Pets")
    ynab_stub.ynab_rename_category(world.state, "groc", "Food")
    ynab_stub.ynab_add_category(world.state, "needs", "Gifts", key="gifts")
    run = sync()
    assert run["summary"] == "1 category change"
    assert cat("Groceries") and not any(
        c["label"] in ("Food", "Gifts") for c in categories_service.all_category_rows()
    )
    assert ynab_stub.category_named(world.state, "Pets")
    ynab_sync.update_settings(None, None, None, "both")
    sync()
    assert cat("Food") and cat("Gifts")  # pulled once both ways


# ── a category new in YNAB that could be several things here ──


def test_a_new_ynab_category_pairs_with_a_category_of_the_same_name_without_creating_anything(
    world,  # noqa: F811
):
    enable()
    sync()
    mine = make("Gifts", envelope=False)
    ynab_stub.ynab_add_category(world.state, "needs", "🎁 Gifts", key="gifts")
    before = len(categories_service.all_category_rows())
    run = sync()
    assert len(categories_service.all_category_rows()) == before
    assert not sent(world, "POST", "/categories") and run["summary"] == "1 category change"
    pair = ynab_pairs.load(ynab_stub.PLAN)[0][world.state["C"]["gifts"]]
    assert pair["category"] == mine["id"] and pair["env"] is None  # not budgeted here
    assert [c["text"] for c in record(run)["category_changes"]] == [
        "Matched YNAB's 🎁 Gifts with your Gifts."
    ]


def test_a_ynab_category_matching_an_empty_envelope_gets_its_category_inside_it(world):  # noqa: F811
    enable()
    sync()
    e = change(type="envelope_create", name="Gifts", group_id=group("Needs")["id"])["envelope"]
    ynab_stub.ynab_add_category(world.state, "needs", "🎁 Gifts", key="gifts")
    sync()
    assert env("Gifts")["id"] == e["id"] and len(env("Gifts")["category_ids"]) == 1
    assert not ynab_sync.status()["conflicts"]


def test_a_ynab_category_matching_a_busy_envelope_asks_and_the_answer_is_applied(world):  # noqa: F811
    enable()
    sync()
    e = change(type="envelope_create", name="Gifts", group_id=group("Needs")["id"])["envelope"]
    other = change(type="category_create", label="Cards", kind="expense")
    change(type="envelope_add_category", envelope_id=e["id"], category_id=other["id"])
    ynab_stub.ynab_add_category(world.state, "needs", "Gifts", key="gifts")
    assert sync()["summary"].endswith("1 needs review")
    (item,) = ynab_sync.status()["conflicts"]
    assert item["reason"].startswith("YNAB's Gifts matches your envelope Gifts")
    assert [o["ref"] for o in item["options"]] == [f"env:{e['id']}"]
    ynab_sync.resolve.resolve(item["id"], "onw", f"env:{e['id']}")
    assert len(env("Gifts")["category_ids"]) == 2 and not ynab_sync.status()["conflicts"]


def test_a_ynab_category_matching_two_of_ours_asks_and_is_not_guessed(world):  # noqa: F811
    enable()
    sync()
    a, b = make("Gifts", envelope=False), make("🎁 Gifts", envelope=False)
    ynab_stub.ynab_add_category(world.state, "needs", "gifts", key="gifts3")
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    assert item["reason"] == "YNAB's gifts matches more than one of your categories. Pick one."
    assert not sent(world, "POST", "/categories")  # neither of ours is sent as a duplicate
    ynab_sync.resolve.resolve(item["id"], "onw", f"cat:{a['id']}")
    pairs = ynab_pairs.load(ynab_stub.PLAN)[0]
    assert pairs[world.state["C"]["gifts3"]]["category"] == a["id"]
    sync()
    assert ynab_stub.category_named(world.state, "🎁 Gifts") and b  # the other one goes on its own


def test_an_envelope_that_holds_only_one_category_again_resumes_assigned_and_differences_go_to_review(
    world,  # noqa: F811
):
    enable()
    sync()
    month = world.state["d"](0, 1)[:7]
    split = {"type": "envelope_split_category", "envelope_id": env("Fun Money")["id"]}
    odd = make("Odd", "Wants", envelope=False)
    change(type="envelope_add_category", envelope_id=env("Fun Money")["id"], category_id=odd["id"])
    sync()  # shared now: its Assigned stops syncing
    change(type="assign", month=month, envelope_id=env("Fun Money")["id"], amount=70.0)
    ynab_stub.ynab_budget(world.state, 0, "fun", 60_000)
    sync()
    assert not [w for w in world.writes if "/months/" in w[1]]
    assert planning.assignments_store.month_map(month)[env("Fun Money")["id"]] == 70.0
    change(**split, category_id=odd["id"])  # alone again
    run = sync()
    assert run["summary"].endswith("1 needs review")
    (item,) = ynab_sync.status()["conflicts"]
    assert item["entity"] == "assigned" and item["onw"] == {"amount": 70.0}


def test_the_rest_of_the_categories_wait_for_the_next_run_when_the_allowance_is_used(world):  # noqa: F811
    enable()
    sync()
    for n in range(25):
        make(f"Extra {n:02d}")
    first = sync()
    made = len(sent(world, "POST", "/categories"))
    assert (
        0 < made < 25
        and first["summary"].endswith(f"{25 - made} waiting for the next run (request limit)")
        or first["deferred"]
    )
    sync()
    assert len(sent(world, "POST", "/categories")) == 25
    assert sync()["summary"] == "Nothing changed"


def test_a_category_ynab_refuses_is_reported_in_plain_words_and_not_retried(world):  # noqa: F811
    enable()
    sync()
    make("Pets")
    world.state["reject_writes"] = "name already used"
    run = sync()
    assert run["summary"] == "1 needs review"
    (item,) = ynab_sync.status()["conflicts"]
    assert item["reason"] == (
        "YNAB did not accept the category Pets. Rename it here and sync again, "
        "or keep YNAB's side to leave it out of YNAB."
    )
    world.state.pop("reject_writes")
    before = len(world.writes)
    sync()
    assert len(world.writes) == before  # it waits for the person
    ynab_sync.resolve.resolve(item["id"], "onw")  # try again
    assert ynab_group_of(world, "Pets") == "Needs"
