"""Actual's Reset sync, moving the sync to another budget and back, and budgets from older clients."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest
from actual import Actual

from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import import_runs
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync import client as actual_client
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_migration import job
from tests.actual_live.harness import PASSWORD, Budget
from tests.actual_live.world import (
    balances_here,
    balances_there,
    edit_here,
    live_rows,
    open_items,
    quiet,
    row_here,
    rows_here,
    sync,
    there,
    warnings,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

OLD_BUDGETS = Path(__file__).resolve().parents[1] / "fixtures" / "actual_old_budgets"


# ── Reset sync ──


def test_reset_sync_keeps_pairing_and_deletes_nothing(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="before the reset")
    sync()
    ids_there = {r["id"] for r in budget.view()["rows"]}
    rows = len(live_rows())
    old_group = budget.group

    budget.after_reset()
    assert budget.group != old_group, "a reset makes a new group"
    assert any(b["id"] == budget.file for b in actual_client.list_budgets()), "same file id"

    quiet()
    assert len(live_rows()) == rows, "no mass delete here"
    assert {r["id"] for r in budget.view()["rows"]} == ids_there, "none there"
    assert open_items() == []

    edit_here(row_here(memo="before the reset")["id"], memo="after the reset")
    sync()
    assert there(budget.view(), "after the reset"), "a write still reaches the same row"
    budget.steps(["edit", "after the reset", {"notes": "person after the reset"}])
    sync()
    assert rows_here(memo="person after the reset"), "a person's edit still comes here"
    assert balances_here() == balances_there(budget.view())
    quiet()


def test_a_reset_while_a_run_is_open_ends_that_run_and_the_next_carries_on(world):
    """The client's old group is refused mid-run, and that run stops with nothing lost."""
    from backend.services.actual_sync import writes
    from backend.services.ynab_sync.client import Budget as RequestBudget
    from backend.services.ynab_sync.client import Stopped

    budget = world.seed()
    world.imported(budget)
    quiet()
    with using(ACTUAL), writes.open_sync_client(budget.file, RequestBudget(100)) as client:
        client.read_plan(None)
        budget.after_reset()
        with pytest.raises(Stopped):
            client.set_assigned("2030-01", budget.ids["food"], 1000)
    quiet()


# ── moving the sync to another budget, and back ──


def _pick(budget: Budget) -> None:
    """What the connector form's Budget does."""
    ActualBudgetConfigStore().update(
        {"budget_id": budget.file, "budget_name": budget.name, "budget_currency": "USD"}
    )


def test_moving_to_another_budget_and_back(world):
    first, second = world.seed(), world.seed()
    world.imported(first, "from_ynab")
    actual_app.sync_settings(True, "daily", None, None)
    quiet()
    a_before, b_before = first.view(), second.view()
    rows_of_first = len(live_rows())

    _pick(second)
    assert "budget_differs" in warnings()
    actual_app.sync_settings(False, None, None, None)
    world.imported(second, "from_ynab", confirm_nonempty=True)

    status = actual_app.sync_status()
    assert status["plan_id"] == second.file
    assert status["direction"] == "from_ynab", "direction kept"
    assert status["interval"] == "daily", "schedule kept"
    assert status["enabled"] is True
    assert "budget_differs" not in warnings()
    assert ActualBudgetConfigStore().get()["budget_id"] == second.file, "the connector agrees"
    rows_of_both = len(live_rows())
    assert rows_of_both > rows_of_first

    sync()
    assert len(live_rows()) == rows_of_both, "nothing deleted here"
    assert first.view() == a_before and second.view() == b_before
    assert not [c for c in open_items() if c.get("onw_txn_id")]
    assert "not_in_this_budget" in warnings(), "the first budget's rows stay, and are told of"

    # While the sync follows the second budget, a row goes in the first.
    first.steps(["remove", "plain"])
    _pick(first)
    actual_app.sync_settings(False, None, None, None)
    world.imported(first, "from_ynab", confirm_nonempty=True)
    status = actual_app.sync_status()
    assert status["plan_id"] == first.file
    assert status["direction"] == "from_ynab" and status["interval"] == "daily"
    rows_back = len(live_rows())

    last = sync()
    assert len(live_rows()) == rows_back, f"a removal is held on the first run: {last['summary']}"
    gone_there = there(a_before, "plain")[0]["id"]
    (held,) = [
        t
        for t in rows_here(memo="plain")
        if any(ref.get("ref") == gone_there for ref in t.get("external_refs") or [])
    ]
    assert [c for c in open_items() if c.get("onw_txn_id") == held["id"]], "and asked about"
    assert second.view() == b_before, "the budget left behind isn't touched"
    sync()
    assert second.view() == b_before


def test_picking_a_budget_never_imported_holds_the_sync(world):
    first, second = world.seed(), world.seed()
    world.imported(first)
    quiet()
    rows = len(live_rows())
    a_before, b_before = first.view(), second.view()
    actual_app.sync_settings(True, "1h", second.file, "both")
    last = sync()
    assert "wasn't imported" in (last["summary"] or "")
    assert "import_first" in warnings()
    assert len(live_rows()) == rows
    assert first.view() == a_before and second.view() == b_before


# ── budgets from older clients ──


def _upload(world, path: Path) -> Budget:
    """A snapshot an older official client wrote, put on this server the way a client puts one."""
    with zipfile.ZipFile(path) as original:
        files = {name: original.read(name) for name in original.namelist()}
    file_id = str(uuid.uuid4())
    meta = {**json.loads(files["metadata.json"]), "cloudFileId": file_id, "resetClock": True}
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("db.sqlite", files["db.sqlite"])
        out.writestr("metadata.json", json.dumps(meta))
    name = f"{path.stem} {file_id[:6]}"
    uploader = Actual(world.server.url, password=PASSWORD)
    uploader.upload_user_file(packed.getvalue(), file_id, name)
    listed = next(f for f in uploader.list_user_files().data if f.file_id == file_id)
    return Budget(world.server, name, file_id, listed.group_id)


def _old_ids(budget: Budget) -> dict[str, str]:
    view = budget.view()
    return {a["name"]: a["id"] for a in view["accounts"]}


@pytest.mark.parametrize(
    "snapshot,mode",
    [
        ("6.8.2-envelope.zip", "envelope"),
        ("6.8.2-tracking.zip", "tracking"),
        ("25.12.0-envelope.zip", "envelope"),
        ("26.9.0-envelope.zip", "envelope"),
    ],
)
def test_a_budget_from_an_older_client_opens_imports_and_syncs(world, snapshot, mode):
    budget = _upload(world, OLD_BUDGETS / snapshot)
    with using(ACTUAL), actual_client.open_copy(None, budget.file) as copy:
        assert copy.mode == mode, "the mode survives an older snapshot"
    ids = _old_ids(budget)
    actual_app.start_migration(
        "import",
        budget.file,
        account_types={ids["ACME Checking"]: "cash", ids["ACME Savings"]: "investment"},
        currency="USD",
        sync_direction="both",
    )
    record = world.finish()
    assert record["status"] == "done"
    assert balances_here() == balances_there(budget.view())
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="written to an old budget")
    sync()
    assert there(budget.view(), "written to an old budget")
    quiet()
    assert import_runs.latest("actual_budget", job.KINDS)["status"] == "done"
