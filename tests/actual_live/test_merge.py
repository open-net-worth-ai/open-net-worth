"""A transaction changed on both sides, in a real Actual server: different fields both stand;
the same field changed two ways goes to review. Every name and number is invented."""

from __future__ import annotations

import pytest

from tests.actual_live.world import (
    category_here,
    edit_here,
    open_items,
    quiet,
    row_here,
    sync,
    there,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def test_a_memo_here_and_a_category_there_both_stand(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="plain, edited here")
    (theirs,) = there(budget.view(), "plain")
    budget.call("updateTransaction", theirs["id"], {"category": budget.ids["fun"]})
    sync()
    assert open_items() == []
    assert row_here(memo="plain, edited here")["category"] == category_here("ACME Fun")["id"]
    (now,) = there(budget.view(), "plain, edited here")
    assert now["category"] == budget.ids["fun"]
    quiet()


def test_the_same_note_changed_two_ways_waits_for_review(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="mine")
    (theirs,) = there(budget.view(), "plain")
    budget.call("updateTransaction", theirs["id"], {"notes": "theirs"})
    sync()
    assert [i["kind"] for i in open_items()] == ["conflict"]
    assert row_here(memo="mine") and there(budget.view(), "theirs")


def test_a_preview_against_a_real_server_writes_nothing(world):
    from backend.services.actual_sync import app as actual_app
    from backend.services.actual_sync.app import ACTUAL
    from backend.services.budget_app import using

    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="plain, edited here")
    (theirs,) = there(budget.view(), "other row")
    budget.call("updateTransaction", theirs["id"], {"notes": "other row, edited there"})
    before = budget.view()
    with using(ACTUAL):
        seen = actual_app.sync_preview()
    assert seen["transactions"]["counts"] == {"change_there": 1, "change_here": 1}
    assert budget.view()["rows"] == before["rows"], "nothing was sent"
    assert row_here(memo="other row"), "nothing was pulled"
    assert sync()["summary"].startswith("1 sent to Actual, 1 pulled")
    quiet()


def test_keep_all_answers_both_in_one_session_with_one_restore_point(world, tmp_path, monkeypatch):
    from backend.services.actual_sync import app as actual_app
    from backend.services.actual_sync import restore_points
    from backend.services.actual_sync.app import ACTUAL
    from backend.services.budget_app import using

    folder = tmp_path / "restore-points"
    folder.mkdir()
    monkeypatch.setattr(restore_points, "_folder", lambda: folder)
    budget = world.seed()
    world.imported(budget)
    quiet()
    for note in ("plain", "other row"):
        edit_here(row_here(memo=note)["id"], memo=f"{note}, mine")
        (theirs,) = there(budget.view(), note)
        budget.call("updateTransaction", theirs["id"], {"notes": f"{note}, theirs"})
    sync()
    items = open_items()
    assert [i["kind"] for i in items] == ["conflict", "conflict"]
    with using(ACTUAL):
        out = actual_app.resolve_sync_items([i["id"] for i in items], "onw")
    assert out["resolved"] == 2 and out["failed"] == [] and open_items() == []
    assert len(out["status"]["restore_points"]) == 1
    view = budget.view()
    assert there(view, "plain, mine") and there(view, "other row, mine")
    quiet()
