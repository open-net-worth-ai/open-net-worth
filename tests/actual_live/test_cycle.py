"""A full two-way cycle and one-way both ways, against a real server. The referee is a fresh
download by the official client, so it sees only what truly reached the server."""

from __future__ import annotations

import pytest

from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services import planning
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync import client as actual_client
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from tests.actual_live.world import (
    NEXT_YEAR,
    THIS_MONTH,
    TODAY,
    account_here,
    as_person,
    assigned_here,
    balances_here,
    balances_there,
    category_here,
    edit_here,
    envelope_here,
    new_row,
    open_items,
    quiet,
    row_here,
    rows_here,
    sync,
    there,
    warnings,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def test_the_first_run_after_an_import_writes_nothing(world):
    budget = world.seed()
    before = budget.view(THIS_MONTH)
    world.imported(budget)
    assert balances_here() == balances_there(before)
    quiet()
    assert budget.view(THIS_MONTH) == before
    quiet()


def test_a_row_each_way(world):
    budget = world.seed()
    world.imported(budget)
    quiet()

    edit_here(row_here(memo="plain")["id"], memo="edited here", from_amount=30.0)
    assert sync()["summary"].startswith("1 sent")
    view = budget.view()
    assert [r["amount"] for r in there(view, "edited here")] == [-3000]
    assert balances_here() == balances_there(view)
    quiet()

    budget.steps(
        ["edit", "other row", {"notes": "edited there", "amount": -1500}],
        ["call", "addTransactions", budget.ids["checking"], [
            {"date": TODAY, "amount": -999, "payee_name": "ACME New", "category": budget.ids["food"], "notes": "new there"}
        ]],
    )  # fmt: skip
    assert "pulled" in sync()["summary"]
    assert float(row_here(memo="edited there")["from_amount"]) == 15.0
    assert float(row_here(memo="new there")["from_amount"]) == 9.99
    assert balances_here() == balances_there(budget.view())
    quiet()


def test_creates_of_every_kind_here_reach_the_official_client(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    food, fun = category_here("ACME Food")["id"], category_here("ACME Fun")["id"]
    new_row(memo="plain here", category=food)
    new_row(memo="future here", date=NEXT_YEAR, category=fun, from_amount=5.0)
    new_row(
        memo="split here",
        from_amount=30.0,
        splits=[
            {"category": food, "amount": 20.0, "memo": "line a"},
            {"category": fun, "amount": 7.0, "memo": "line b"},
            {"category": food, "amount": 3.0, "memo": "line c"},
        ],
    )
    new_row(
        kind="transfer",
        description="",
        memo="transfer here",
        from_amount=40.0,
        to_account_id=account_here("ACME Card")["id"],
        to_amount=40.0,
        to_currency="USD",
    )
    assert "4 sent" in sync()["summary"]
    view = budget.view()

    assert there(view, "plain here")[0]["amount"] == -1234
    assert there(view, "future here")[0]["date"] == NEXT_YEAR
    (split,) = there(view, "split here")
    assert split["amount"] == -3000
    assert [(s["amount"], s["notes"]) for s in split["subs"]] == [
        (-2000, "line a"),
        (-700, "line b"),
        (-300, "line c"),
    ]
    legs = there(view, "transfer here")
    assert sorted((leg["account"], leg["amount"]) for leg in legs) == [
        ("ACME Card", 4000),
        ("ACME Checking", -4000),
    ]
    assert all(leg["transfer_id"] for leg in legs)
    assert balances_here() == balances_there(view)
    quiet()


def test_a_split_is_rewritten_whole(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    food, fun = category_here("ACME Food")["id"], category_here("ACME Fun")["id"]
    edit_here(
        row_here(memo="split parent")["id"],
        from_amount=33.0,
        date=TODAY,
        splits=[
            {"category": fun, "amount": 25.0, "memo": "x"},
            {"category": food, "amount": 8.0, "memo": "y"},
        ],
    )
    sync()
    (split,) = there(budget.view(), "split parent")
    assert split["amount"] == -3300
    assert [(s["amount"], s["notes"]) for s in split["subs"]] == [(-2500, "x"), (-800, "y")]
    quiet()


def test_a_resplit_in_actual_comes_here_and_the_old_lines_go(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    food = budget.ids["food"]
    budget.steps(["edit", "split parent", {"subtransactions": [
        {"amount": -6000, "category": food, "notes": "new a"},
        {"amount": -4000, "category": food, "notes": "new b"},
    ]}])  # fmt: skip
    sync()
    row = row_here(memo="split parent")
    assert [(float(s["amount"]), s.get("memo")) for s in row["splits"]] == [
        (60.0, "new a"),
        (40.0, "new b"),
    ]
    assert balances_here() == balances_there(budget.view())
    quiet()


def test_assigned_each_way(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": THIS_MONTH,
            "envelope_id": envelope_here("ACME Food")["id"],
            "amount": 777.0,
        }
    )
    sync()
    food = budget.ids["food"]
    assert budget.view(THIS_MONTH)["budget"][THIS_MONTH]["categories"][food]["budgeted"] == 77700
    with using(ACTUAL), actual_client.open_copy(None, budget.file) as copy:
        notes = {n["id"]: n["note"] for n in copy.rows().notes}
    assert f"ONW {THIS_MONTH}: assigned 777.00 (was 500.00)" in notes[food], "the Assigned trail"
    quiet()
    budget.call("setBudgetAmount", THIS_MONTH, food, 88800)
    sync()
    assert assigned_here(THIS_MONTH, "ACME Food") == 888.0
    quiet()


def test_categories_and_groups_each_way(world):
    budget = world.seed()
    world.imported(budget)
    quiet()

    fun = category_here("ACME Fun")["id"]
    categories_service.set_category_hidden(fun, True)
    sync()
    assert [c["hidden"] for c in budget.view()["categories"] if c["name"] == "ACME Fun"] == [True]
    quiet()
    categories_service.set_category_hidden(fun, False)
    sync()
    assert [c["hidden"] for c in budget.view()["categories"] if c["name"] == "ACME Fun"] == [False]
    quiet()

    budget.steps(
        ["call", "updateCategory", budget.ids["fun"], {"name": "ACME Fun Renamed"}],
        [
            "call",
            "createCategory",
            {"name": "ACME Added There", "group_id": budget.ids["everyday"]},
        ],
    )
    sync()
    labels = {c["label"] for c in categories_service.all_category_rows()}
    assert {"ACME Fun Renamed", "ACME Added There"} <= labels
    quiet()

    budget.call("deleteCategory", budget.ids["food"], budget.ids["fun"])
    sync()
    row = category_here("ACME Food")
    assert row["hidden"] is True, "a category deleted in Actual hides ours"
    assert [c["name"] for c in budget.view()["categories"] if c["name"] == "ACME Food"] == []


def test_a_change_on_both_sides_goes_to_review_and_each_answer_holds(world):
    budget = world.seed()
    world.imported(budget)
    quiet()

    edit_here(row_here(memo="plain")["id"], memo="conflict here")
    budget.steps(["edit", "plain", {"notes": "conflict there"}])
    sync()
    (item,) = open_items()
    assert there(budget.view(), "conflict there")
    assert rows_here(memo="conflict here")
    actual_app.resolve_sync_item(item["id"], "onw")
    assert there(budget.view(), "conflict here")
    assert open_items() == []
    quiet()

    edit_here(row_here(memo="other row")["id"], memo="second here")
    budget.steps(["edit", "other row", {"notes": "second there"}])
    sync()
    (item,) = open_items()
    actual_app.resolve_sync_item(item["id"], "ynab")
    assert rows_here(memo="second there")
    assert not rows_here(memo="second here")
    assert there(budget.view(), "second there")
    quiet()
    assert balances_here() == balances_there(budget.view())


def test_a_delete_each_way_stays_deleted(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    as_person(ledger_service.bulk_delete_transactions, [row_here(memo="plain")["id"]])
    budget.steps(["remove", "other row"])
    sync()
    view = budget.view()
    assert there(view, "plain") == []
    assert rows_here(memo="other row") == []
    quiet()
    view = budget.view()
    assert there(view, "plain") == [] and rows_here(memo="other row") == []
    assert balances_here() == balances_there(view)


def test_one_way_from_actual(world):
    budget = world.seed()
    world.imported(budget, "from_ynab")
    assert actual_app.sync_status()["direction"] == "from_ynab"
    quiet()
    view = budget.view(THIS_MONTH)

    edit_here(row_here(memo="plain")["id"], memo="here only")
    sync()
    assert budget.view(THIS_MONTH) == view, "From Actual writes nothing to Actual"
    assert "waiting_for_both_ways" in warnings()

    budget.steps(["edit", "other row", {"notes": "from there"}])
    assert "pulled" in sync()["summary"]
    assert rows_here(memo="from there")
    view = budget.view(THIS_MONTH)
    sync()
    assert budget.view(THIS_MONTH) == view

    actual_app.sync_settings(None, None, None, "both")
    sync()
    assert there(budget.view(), "here only"), "what waited goes once it is both ways"
    assert "waiting_for_both_ways" not in warnings()
    quiet()


def test_one_way_to_actual(world):
    budget = world.seed()
    world.imported(budget, "to_ynab")
    quiet()

    edit_here(row_here(memo="plain")["id"], memo="sent there")
    assert "1 sent" in sync()["summary"]
    assert there(budget.view(), "sent there")

    budget.steps(["edit", "other row", {"notes": "edited there"}])
    budget.steps(["remove", "card row"])
    book = balances_here()
    sync()
    assert rows_here(memo="other row"), "To Actual doesn't change this book"
    assert balances_here() == book
    assert "waiting_for_both_ways" in warnings()

    actual_app.sync_settings(None, None, None, "both")
    sync()
    assert rows_here(memo="edited there")
    assert rows_here(memo="card row") == []
    assert balances_here() == balances_there(budget.view())
    quiet()
