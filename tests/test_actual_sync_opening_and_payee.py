"""Two edits the Actual sync used to drop: a Starting Balance changed in Actual, and a payee
cleared here. Same harness as ``test_actual_sync_engine``; every name and number is invented."""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from tests import test_actual_sync_engine as engine
from tests.test_actual_sync_engine import (
    PAST,
    account_here,
    as_person,
    by_ref,
    category_here,
    edit_here,
    enable,
    new_row,
    open_items,
    sync,
    writes_seen,
)

world = engine.world  # the engine tests' fixture: an Actual budget, imported

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _opening() -> tuple[float, str | None]:
    acct = account_here("ACME Checking")
    return ledger_service.opening_amount(acct), ledger_service.opening_date(acct)


# ── a Starting Balance changed in Actual ──


def test_a_starting_balance_amount_changed_in_actual_comes_across(world):
    enable()
    sync()
    assert _opening() == (1000.0, PAST)
    world.edit("UPDATE transactions SET amount = 150000 WHERE id = 't-open'")
    world.queue.clear()
    world.calls.clear()
    run = sync()
    assert run["summary"] != "Nothing changed"
    assert _opening() == (1500.0, PAST)
    assert writes_seen(world) == [] and open_items() == []
    assert sync()["summary"] == "Nothing changed"
    assert _opening() == (1500.0, PAST)


def test_a_starting_balance_date_changed_in_actual_comes_across(world):
    enable()
    sync()
    world.edit("UPDATE transactions SET date = 20191215 WHERE id = 't-open'")
    world.queue.clear()
    sync()
    assert _opening() == (1000.0, "2019-12-15")
    assert sync()["summary"] == "Nothing changed"


def test_an_opening_edited_here_reaches_actual(world):
    enable()
    sync()
    acct = account_here("ACME Checking")
    as_person(ledger_service.set_account_opening, acct["id"], 1200.0, "2019-11-30")
    world.calls.clear()
    sync()
    sent = world.row("transactions", "t-open")
    assert sent["amount"] == 120_000
    assert sent["date"] == 20200101, "a date here is this book's own: never sent"
    assert _opening() == (1200.0, "2019-11-30")
    assert open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_an_opening_edited_here_waits_in_from_actual_mode(world):
    enable("from_ynab")
    sync()
    acct = account_here("ACME Checking")
    as_person(ledger_service.set_account_opening, acct["id"], 1200.0, PAST)
    world.calls.clear()
    sync()
    assert world.row("transactions", "t-open")["amount"] == 100_000 and writes_seen(world) == []
    enable("both")
    sync()
    assert world.row("transactions", "t-open")["amount"] == 120_000


def test_both_sides_moved_each_takes_the_others_difference(world):
    """Actual's change is applied here as a difference, and ours there: neither is lost."""
    enable()
    sync()
    acct = account_here("ACME Checking")
    as_person(ledger_service.set_account_opening, acct["id"], 1200.0, PAST)
    world.edit("UPDATE transactions SET amount = 150000 WHERE id = 't-open'")
    world.queue.clear()
    sync()
    assert _opening() == (1700.0, PAST)
    assert world.row("transactions", "t-open")["amount"] == 170_000
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


# ── a payee cleared here ──


def test_a_payee_cleared_here_is_cleared_in_actual(world):
    enable()
    sync()
    market = by_ref("t-market")
    edit_here(market["id"], description="")
    world.calls.clear()
    sync()
    assert world.row("transactions", "t-market")["description"] is None
    assert open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    assert by_ref("t-market")["description"] in ("", None)


# ── a category Actual can't hold ──


def test_a_category_on_an_off_budget_row_is_not_wiped_by_actuals_blank(world):
    enable()
    sync()
    food = category_here("Food")["id"]
    made = new_row(from_account_id=account_here("ACME Savings")["id"], memo="off", category=food)
    sync()
    (ref,) = [e["ref"] for e in ledger_service.transactions.find_by_id(made["id"])["external_refs"]]
    world.edit("UPDATE transactions SET notes = 'off there' WHERE id = ?", ref)
    world.queue.clear()
    sync()
    assert ledger_service.transactions.find_by_id(made["id"])["category"] == food
    assert sync()["summary"] == "Nothing changed"
