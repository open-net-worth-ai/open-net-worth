"""A restore point from a run that wrote to a real Actual server imports back into Actual as the
budget was before that run. Every name and number is invented."""

from __future__ import annotations

import pytest

from backend.services.actual_sync import restore_points
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from tests.actual_live.world import edit_here, quiet, row_here, sync, there

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def test_a_restore_point_imports_back_as_actual_was_before_the_run(world, tmp_path, monkeypatch):
    folder = tmp_path / "points"
    folder.mkdir()
    monkeypatch.setattr(restore_points, "_folder", lambda: folder)
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="plain, written by the sync")
    assert sync()["summary"].startswith("1 sent")
    assert there(budget.view(), "plain, written by the sync")
    with using(ACTUAL):
        (point,) = restore_points.listing()
    path = restore_points.path_of(point["id"])
    imported = budget.server.run_job([["call", "importBudget", str(path)], ["view", {}]])
    rows = imported[1]["rows"]
    notes = {r["notes"] for r in rows}
    assert "plain" in notes and "plain, written by the sync" not in notes
    assert len(rows) == len(budget.view()["rows"])
