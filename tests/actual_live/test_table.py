"""The rows of the design's test table, each through this app's own client on a real server. A row
about ``actualpy`` alone is read here through the code that depends on it."""

from __future__ import annotations

import importlib.metadata
import re
import time
from pathlib import Path

import pytest
from actual import Actual
from actual.queries import get_transactions
from packaging.requirements import Requirement

from backend.services.actual_sync import client as actual_client
from backend.services.actual_sync import writes
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_sync.client import Budget
from tests.actual_live.harness import PASSWORD
from tests.actual_live.world import (
    LAST_MONTH,
    THIS_MONTH,
    TODAY,
    balances_here,
    balances_there,
    category_here,
    edit_here,
    new_row,
    quiet,
    row_here,
    sync,
    there,
    warnings,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

ROOT = Path(__file__).resolve().parents[2]


def same_as_the_official_client(plan: dict, view: dict, month: str) -> None:
    """A copy read by this app holds what a fresh official download holds: accounts, every row and
    line, and Assigned."""
    assert {a["name"]: a["balance"] for a in plan["accounts"]} == {
        a["name"]: a["balance"] * 10 for a in view["accounts"]
    }
    live = [t for t in plan["transactions"] if not t.get("deleted")]
    assert {t["id"]: t["amount"] for t in live} == {r["id"]: r["amount"] * 10 for r in view["rows"]}
    assert {t["id"]: t["date"] for t in live} == {r["id"]: r["date"] for r in view["rows"]}
    assert {t["id"]: t.get("memo") or None for t in live} == {
        r["id"]: r["notes"] for r in view["rows"]
    }
    lines = {s["id"]: s["amount"] for s in plan["subtransactions"] if not s.get("deleted")}
    assert lines == {s["id"]: s["amount"] * 10 for r in view["rows"] for s in r["subs"]}
    theirs = view["budget"][month]["categories"]
    ours = next(m for m in plan["months"] if m["month"] == f"{month}-01")["categories"]
    assigned = {c["id"]: c["budgeted"] for c in ours if c["budgeted"]}
    assert assigned == {cid: c["budgeted"] * 10 for cid, c in theirs.items() if c.get("budgeted")}


def test_reads_a_budget_the_official_client_made(world):
    budget = world.seed()
    with using(ACTUAL), actual_client.open_copy(None, budget.file) as copy:
        plan = copy.plan()
    same_as_the_official_client(plan, budget.view(THIS_MONTH), THIS_MONTH)


def test_writes_through_the_client_read_back_and_a_copy_stays_the_same(world):
    budget = world.seed()
    ids = budget.ids
    with using(ACTUAL), writes.open_sync_client(budget.file, Budget(1000)) as client:
        client.read_plan(None)
        (plain, future, split) = client.save_transactions(
            "POST",
            [
                {"account_id": ids["checking"], "date": TODAY, "amount": -12340, "memo": "py plain",
                 "payee_name": "ACME Py", "category_id": ids["food"], "cleared": "cleared"},
                {"account_id": ids["checking"], "date": "2030-01-15", "amount": -5000, "memo": "py future",
                 "payee_name": "ACME Py", "category_id": ids["fun"], "cleared": "uncleared"},
                {"account_id": ids["checking"], "date": TODAY, "amount": -10000, "memo": "py split",
                 "payee_name": "ACME Py", "cleared": "cleared", "subtransactions": [
                     {"amount": -7000, "category_id": ids["food"], "memo": "py a"},
                     {"amount": -3000, "category_id": ids["fun"], "memo": "py b"},
                 ]},
            ],
        )  # fmt: skip
        view = budget.view()
        payees = {p["transfer"]: p["id"] for p in view["payees"] if p["transfer"]}
        on_budget, off_budget = client.save_transactions(
            "POST",
            [
                {"account_id": ids["checking"], "date": TODAY, "amount": -1000, "memo": "py to card",
                 "payee_id": payees[ids["card"]], "cleared": "cleared"},
                {"account_id": ids["checking"], "date": TODAY, "amount": -2000, "memo": "py to savings",
                 "payee_id": payees[ids["savings"]], "category_id": ids["fun"], "cleared": "cleared"},
            ],
        )  # fmt: skip
        client.save_transactions(
            "PATCH", [{"id": plain["id"], "memo": "py edited", "amount": -13000}]
        )
        client.delete_transaction(future["id"])
        client.set_assigned(THIS_MONTH, ids["food"], 123450)
        group = client.create_group("ACME Py Group")
        category = client.create_category(group["id"], "ACME Py Cat")
        client.update_category(category["id"], {"name": "ACME Py Renamed"})
        client.update_group(group["id"], "ACME Py Group Renamed")
        client.update_category(category["id"], {"hidden": True})
        client.set_note(ids["food"], "ONW made up trail line")
        plan = client.copy.plan()

    view = budget.view(THIS_MONTH)
    assert [r["amount"] for r in there(view, "py edited")] == [-1300]
    assert there(view, "py future") == []
    (written,) = there(view, "py split")
    assert [(s["amount"], s["notes"]) for s in written["subs"]] == [(-700, "py a"), (-300, "py b")]
    for memo, ours, theirs in (("py to card", -100, 100), ("py to savings", -200, 200)):
        legs = there(view, memo)
        assert sorted(leg["amount"] for leg in legs) == sorted([ours, theirs])
        assert all(leg["transfer_id"] for leg in legs)
    assert on_budget["transfer_transaction_id"] and off_budget["transfer_transaction_id"]
    assert split["amount"] == -10000
    assert [c for c in view["categories"] if c["name"] == "ACME Py Renamed"][0]["hidden"] is True
    assert [g for g in view["groups"] if g["name"] == "ACME Py Group Renamed"]
    assert view["budget"][THIS_MONTH]["categories"][ids["food"]]["budgeted"] == 12345
    same_as_the_official_client(plan, view, THIS_MONTH)


def test_a_row_with_an_imported_id_keeps_it(world):
    budget = world.seed()
    budget.call(
        "addTransactions",
        budget.ids["checking"],
        [{"date": TODAY, "amount": -777, "payee_name": "ACME Bank", "notes": "from a bank",
          "imported_id": "acme-bank-1"}],
    )  # fmt: skip
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="from a bank")["id"], memo="edited here")
    sync()
    assert [r["imported_id"] for r in there(budget.view(), "edited here")] == ["acme-bank-1"]


def test_the_librarys_own_read_is_never_used_and_a_repeat_changes_nothing(world, tmp_path):
    """The design's first test saw ``actualpy``'s ``sync`` hand back rows it had already given, ours
    included, so the engine compares a whole read with its mirror and never uses that list. Measured
    here and printed, not asserted, since nothing depends on the count."""
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="once")
    sync()
    with Actual(
        world.server.url, password=PASSWORD, file=budget.file, data_dir=tmp_path
    ) as library:
        fresh = len(library.sync()), len(library.sync())
        get_transactions(library.session)[0].notes = "written by the library"
        library.commit()
        after_a_commit = len(library.sync()), len(library.sync())
    print(f"\n[library sync twice] fresh {fresh}, after a commit {after_a_commit}")
    sync()
    quiet()
    assert len(there(budget.view(), "once")) == 1


def test_what_this_app_leaves_alone_breaks_nothing_and_stays(world):
    budget = world.seed()
    done = budget.steps(["unmodelled", THIS_MONTH])[0]
    assert set(done.values()) == {"ok"}, done
    before = budget.steps(
        ["call", "getRules"],
        ["call", "getSchedules"],
        ["call", "getTags"],
        ["call", "getAccountGroups"],
    )
    record = world.imported(budget)
    assert record["status"] == "done"
    assert balances_here() == balances_there(budget.view())
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="still syncing")
    new_row(description="ACME Rule", memo="past a rule", category=category_here("ACME Fun")["id"])
    sync()
    assert [r["category"] for r in there(budget.view(), "past a rule")] == [budget.ids["fun"]]
    assert (
        budget.steps(
            ["call", "getRules"],
            ["call", "getSchedules"],
            ["call", "getTags"],
            ["call", "getAccountGroups"],
        )
        == before
    )
    assert there(budget.view(), "still syncing")
    quiet()
    assert warnings() == {}


def test_a_large_budget_reads_in_seconds(world):
    budget = world.seed()
    budget.steps(["bulk", "ACME Checking", 5000])
    started = time.perf_counter()
    with using(ACTUAL), actual_client.open_copy(None, budget.file) as copy:
        plan = copy.plan()
    seconds = time.perf_counter() - started
    print(f"\n[5000 rows] download, read and shape: {seconds:.2f}s")
    assert len(plan["transactions"]) >= 5000
    assert seconds < 60


def test_a_large_push_in_batches(world):
    """The worst case for a batch's read: 2,000 new rows, 100 a batch, each read replaying the run's
    own rows since the copy began."""
    budget = world.seed()
    ids = budget.ids
    bodies = [
        {"account_id": ids["checking"], "date": TODAY, "amount": -(1000 + i * 10), "memo": f"push {i}",
         "payee_name": "ACME Push", "category_id": ids["food"], "cleared": "cleared"}
        for i in range(2000)
    ]  # fmt: skip
    started = time.perf_counter()
    with using(ACTUAL), writes.open_sync_client(budget.file, Budget(1000)) as client:
        client.read_plan(None)
        for start in range(0, len(bodies), 100):
            client.save_transactions("POST", bodies[start : start + 100])
    seconds = time.perf_counter() - started
    print(f"\n[2000 rows pushed in 20 batches] {seconds:.2f}s")
    assert sum(1 for r in budget.view()["rows"] if (r["notes"] or "").startswith("push ")) == 2000
    assert seconds < 300


def test_a_month_before_this_one_reads_the_same(world):
    budget = world.seed()
    budget.call("setBudgetAmount", LAST_MONTH, budget.ids["food"], 1000)
    with using(ACTUAL), actual_client.open_copy(None, budget.file) as copy:
        plan = copy.plan()
    same_as_the_official_client(plan, budget.view(LAST_MONTH), LAST_MONTH)


def test_the_client_library_is_the_pinned_one_and_nothing_clashes():
    pinned = re.search(r"^actualpy==(\S+)", (ROOT / "requirements.txt").read_text(), re.M)
    assert pinned and importlib.metadata.version("actualpy") == pinned[1]
    seen, waiting = set(), ["actualpy"]
    while waiting:
        name = waiting.pop()
        if name in seen:
            continue
        seen.add(name)
        for text in importlib.metadata.requires(name) or []:
            need = Requirement(text)
            if need.marker and not need.marker.evaluate():
                continue
            found = importlib.metadata.version(need.name)
            assert need.specifier.contains(found, prereleases=True), (name, text, found)
            waiting.append(need.name)
    assert {"sqlmodel", "proto-plus"} <= seen
