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


# ── a Starting Balance changed before openings synced both ways ──


def _forget_openings() -> None:
    """The state a book upgraded from a version that never compared openings is in."""
    from backend.services.actual_sync.app import ACTUAL
    from backend.services.budget_app import using
    from backend.services.ynab_sync import state as sync_state

    with using(ACTUAL):
        st = sync_state.load()
        st.pop("openings_seen", None)
        st.pop("openings_ours", None)
        sync_state.save(st)


def _opening_item() -> dict:
    (item,) = [i for i in open_items() if i.get("entity") == "opening"]
    return item


@pytest.mark.parametrize("keep", ["ynab", "onw"])
def test_an_opening_changed_before_the_upgrade_is_asked_about_then_settled(world, keep):
    from backend.services.actual_sync import app as actual_app

    enable()
    sync()
    _forget_openings()
    world.edit("UPDATE transactions SET amount = 130000 WHERE id = 't-open'")
    world.queue.clear()
    sync()
    item = _opening_item()
    assert item["onw"]["amount"] == 1000.0 and item["ynab"]["amount"] == 1300.0
    assert _opening() == (1000.0, PAST), "nothing moves before the person answers"
    sync()
    assert len([i for i in open_items() if i.get("entity") == "opening"]) == 1
    actual_app.resolve_sync_item(item["id"], keep)
    if keep == "ynab":
        assert _opening()[0] == 1300.0
        assert world.row("transactions", "t-open")["amount"] == 130_000
    else:
        assert _opening()[0] == 1000.0
        assert world.row("transactions", "t-open")["amount"] == 100_000
    assert open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_an_opening_that_already_agrees_after_the_upgrade_asks_nothing(world):
    enable()
    sync()
    _forget_openings()
    sync()
    assert open_items() == []
    world.edit("UPDATE transactions SET amount = 150000 WHERE id = 't-open'")
    world.queue.clear()
    sync()
    assert _opening() == (1500.0, PAST)
