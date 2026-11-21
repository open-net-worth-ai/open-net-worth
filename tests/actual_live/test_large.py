"""A large budget: ten thousand transactions imported, verified, then kept in step. Prints the
timings it saw. Every name and number is invented."""

from __future__ import annotations

import time

import pytest

from tests.actual_live import world as world_module
from tests.actual_live.world import edit_here, quiet, row_here, rows_here, sync, there

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

ROWS = 10_000


def test_ten_thousand_transactions_import_verify_and_sync(world, monkeypatch):
    monkeypatch.setattr(world_module, "IMPORT_SECONDS", 900)
    budget = world.seed()
    budget.steps(["bulk", "ACME Checking", ROWS])
    started = time.monotonic()
    record = world.imported(budget)
    took_import = time.monotonic() - started
    checks = record["verification"]
    assert checks["ok"], checks["mismatches"][:5]
    assert len(rows_here()) >= ROWS
    started = time.monotonic()
    quiet()
    took_quiet = time.monotonic() - started

    edit_here(row_here(memo="bulk 17")["id"], memo="bulk 17, edited here")
    (theirs,) = there(budget.view(), "bulk 4242")
    budget.call("updateTransaction", theirs["id"], {"notes": "bulk 4242, edited there"})
    started = time.monotonic()
    run = sync()
    took_edit = time.monotonic() - started
    assert run["summary"].startswith("1 sent to Actual, 1 pulled"), run["summary"]
    assert there(budget.view(), "bulk 17, edited here")
    assert row_here(memo="bulk 4242, edited there")
    quiet()
    print(
        f"\n[large] {ROWS} rows: import+verify {took_import:.0f}s ({checks['checks_total']} checks), "
        f"quiet run {took_quiet:.1f}s, run with an edit each side {took_edit:.1f}s"
    )
