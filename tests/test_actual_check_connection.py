"""Test connection: says it works, or which step stopped it. Same harness as
``test_actual_sync_engine``; nothing real."""

from __future__ import annotations

import pytest

from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import actual_sync
from backend.services.actual_sync import client as actual_client
from tests import test_actual_sync_engine as engine
from tests.actual_sync_stub import down

world = engine.world

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _chosen() -> None:
    ActualBudgetConfigStore().update({"budget_id": engine.BUDGET})


def test_a_working_connection_opens_the_chosen_budget(world):
    _chosen()
    result = actual_sync.check_connection()
    assert result["ok"] is True and "downloads and opens" in result["text"]


def test_an_unreachable_server_says_so(world, monkeypatch):
    def refuse(store=None):
        raise actual_client._plain_error(down())

    monkeypatch.setattr(actual_client, "list_budgets", refuse)
    result = actual_sync.check_connection()
    assert result["ok"] is False and "reach" in result["text"]


def test_a_budget_no_longer_on_the_server_says_so(world):
    ActualBudgetConfigStore().update({"budget_id": "gone-budget"})
    result = actual_sync.check_connection()
    assert result["ok"] is False and "isn't among" in result["text"]


def test_a_budget_that_wont_open_says_why(world):
    _chosen()
    world.open_error = actual_client._plain_error(down())
    result = actual_sync.check_connection()
    assert result["ok"] is False and "didn't open" in result["text"]
