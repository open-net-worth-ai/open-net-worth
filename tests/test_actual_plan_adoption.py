"""Pointing the Actual sync at a budget: a first pick that turns out to be a copy holds what it lacks,
and a refusal leaves the old budget's state standing. The real engine over the made-up server
(``actual_sync_stub``). Every name and number is invented."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.errors import ConflictError, ValidationError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.db.ynab_sync_store import YnabSyncStateStore
from backend.services import budget_app
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync.app import ACTUAL
from backend.services.ynab_sync import runner
from backend.services.ynab_sync import settings as ynab_settings
from backend.services.ynab_sync import state as sync_state
from tests.actual_sync_stub import BUDGET, BUDGET_B, seed
from tests.test_actual_sync_engine import (
    KEY,
    PAST,
    TODAY,
    _copy_of_the_budget,
    _gone_there,
    _import,
    _live_here,
    _prepared,
    _to_other_budget,
    by_ref,
    edit_here,
    enable,
    open_items,
    sync,
    sync_state_of,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


@pytest.fixture
def world(db_isolated, tmp_path, monkeypatch):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    _import()
    server.calls.clear()
    return server


def _held() -> dict:
    stores = ACTUAL.stores
    return {
        "state": sync_state_of(),
        "mirror": stores.mirror().by_id(),
        "baselines": stores.baselines().split(),
        "conflicts": actual_app.sync_status()["conflicts"],
    }


# ── a first pick ─────────────────────────────────────────────────────────────


def test_a_first_pick_of_a_copy_holds_a_row_it_lacks_and_deletes_nothing(world):
    """Imported, never synced, then pointed at a duplicate that lacks a row."""
    assert sync_state_of()["plan_id"] is None
    _copy_of_the_budget(world)
    with world.on(BUDGET_B):
        _gone_there(world, "t-market")
    rows_here = _live_here()
    _to_other_budget()
    assert sync_state_of()["switched"] is True
    sync()
    assert _live_here() == rows_here, "nothing goes silently"
    (item,) = open_items()
    assert item["onw_txn_id"] == by_ref("t-market")["id"]


def test_a_first_pick_with_nothing_linked_has_nothing_to_hold(db_isolated, tmp_path, monkeypatch):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    with budget_app.using(ACTUAL):
        adopted = ynab_settings._adopt_plan(BUDGET)
    assert adopted["switched"] is False


# ── a refusal leaves the old budget alone ────────────────────────────────────


@pytest.fixture
def synced(world):
    enable()
    sync()
    edit_here(by_ref("t-market")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    sync()
    assert open_items(), "a review item to lose, if one were lost"
    _copy_of_the_budget(world)
    return world


def test_refused_as_it_turns_on_keeps_the_old_budgets_state(synced, monkeypatch):
    before = _held()
    monkeypatch.setattr(ActualBudgetConfigStore, "is_configured", lambda self: False)
    with pytest.raises(ValidationError):
        actual_app.sync_settings(True, None, BUDGET_B)
    monkeypatch.undo()
    assert before["mirror"] and before["baselines"] and before["conflicts"]
    assert _held() == before
    assert sync_state_of()["plan_id"] == BUDGET


def test_refused_at_the_last_check_keeps_the_old_budgets_state(synced, monkeypatch):
    before = _held()
    calls = []
    real = ynab_settings._refuse_beside_others

    def second_time_refuses() -> None:
        calls.append(1)
        if len(calls) == 2:
            raise ConflictError("The other sync came on just now.")
        real()

    monkeypatch.setattr(ynab_settings, "_refuse_beside_others", second_time_refuses)
    with pytest.raises(ConflictError):
        actual_app.sync_settings(True, None, BUDGET_B)
    assert len(calls) == 2
    assert _held() == before


def test_a_setting_refused_with_a_new_budget_keeps_the_old_budgets_state(synced):
    before = _held()
    with pytest.raises(ValidationError):
        actual_app.sync_settings(None, "not-an-interval", BUDGET_B)
    assert _held() == before


def test_a_move_that_goes_through_still_forgets_the_old_budget(synced):
    actual_app.sync_settings(True, None, BUDGET_B)
    after = _held()
    assert after["state"]["plan_id"] == BUDGET_B and after["state"]["switched"] is True
    assert after["mirror"] == {} and after["conflicts"] == []


# ── YNAB ─────────────────────────────────────────────────────────────────────


def test_ynabs_plan_switch_forgets_at_the_same_point_it_always_did(db_isolated, monkeypatch):
    """Nothing YNAB does changes in this work: a refusal after picking a new plan still finds the
    old state gone, and its first pick is never held. Both go to the owner, not into this diff."""
    YnabSyncStateStore().save(
        {**sync_state.blank(), "plan_id": "plan-y", "plan_name": "ACME", "currency": "USD"}
    )
    plans = [{"id": "plan-z", "name": "ACME Z", "currency": "USD"}]
    monkeypatch.setattr(
        budget_app, "_ynab_source", lambda: SimpleNamespace(list_plans=lambda _store: plans)
    )
    with pytest.raises(ValidationError):
        runner.update_settings(True, None, "plan-z")
    assert YnabSyncStateStore().doc("state").get("plan_id") is None


def test_the_budget_it_was_imported_from_is_no_copy(world):
    """A row removed in Actual between the import and the first sync goes as usual."""
    enable()
    assert sync_state_of()["switched"] is False
    assert sync_state_of()["plan_id"] == BUDGET
