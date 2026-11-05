"""A paused Actual connector stops all contact with its server. A pause is what a person does when a
server looks wrong, so it has to mean that on every path, not only on the card. The sync's own switch
stays as it was, and goes on holding YNAB off.

Most of these run the real engine over the made-up server (``actual_sync_stub``), the rest the real
client over an in-process transport that counts requests. Every name and number is made up."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.actual_budget_api import router
from backend.core.errors import ConflictError, ExternalServiceError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import budget_app, connectors, import_runs, ynab_sync
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync import client, writes
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import YNAB, using
from backend.services.ynab_sync import runner
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.client import Budget, Stopped
from tests.actual_sync_stub import BUDGET, PASSWORD, URL, seed
from tests.test_actual_sync_engine import KEY, PAST, TODAY, _free, _import, _prepared, enable

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


@pytest.fixture
def world(db_isolated, tmp_path, monkeypatch):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    _import()
    server.calls.clear()
    return server


def pause() -> None:
    ActualBudgetConfigStore().update({"enabled": False})


def resume() -> None:
    ActualBudgetConfigStore().update({"enabled": True})


def contact(server) -> tuple[int, list[str]]:
    return server.opens, list(server.calls)


# ── the store's word for it ──────────────────────────────────────────────────


def test_paused_means_set_up_and_switched_off(db_isolated):
    store = ActualBudgetConfigStore()
    assert store.paused() is False, "nothing saved yet isn't a pause"
    store.update({"server_url": URL})
    assert store.paused() is False, "no sign-in yet isn't a pause"
    store.update({"password": PASSWORD})
    assert store.paused() is True
    store.update({"enabled": True})
    assert store.paused() is False


def test_the_cards_own_pause_is_what_this_reads(world):
    connectors.set_connector_enabled("actual_budget", False)
    assert ActualBudgetConfigStore().paused() is True
    connectors.set_connector_enabled("actual_budget", True)
    assert ActualBudgetConfigStore().paused() is False


# ── the tick ─────────────────────────────────────────────────────────────────


def test_a_tick_makes_no_contact_while_paused_and_carries_on_after(world, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(runner, "_execute", lambda st, tally: ran.append(st["plan_id"]))
    enable()
    pause()
    before = contact(world)
    assert ynab_sync.run_if_due() is False
    assert ran == [] and contact(world) == before
    resume()
    assert ynab_sync.run_if_due() is True
    _free(ACTUAL)
    assert ran == [BUDGET]


def test_a_real_tick_opens_nothing_while_paused(world):
    enable()
    pause()
    before = contact(world)
    assert ynab_sync.run_if_due() is False
    assert contact(world) == before
    resume()
    assert ynab_sync.run_if_due() is True
    _free(ACTUAL)
    assert world.opens > before[0], "resuming brings the schedule back, with nothing to do first"


def test_only_the_paused_app_leaves_the_tick(world):
    enable()
    assert ACTUAL in budget_app.syncing_apps()
    pause()
    apps = budget_app.syncing_apps()
    assert ACTUAL not in apps and YNAB in apps


# ── what a person can press ──────────────────────────────────────────────────


def test_sync_now_is_refused_in_one_sentence_and_resuming_restores_it(world):
    enable()
    pause()
    before = contact(world)
    with pytest.raises(ConflictError) as refused:
        actual_app.start_sync()
    assert refused.value.message == client.PAUSED
    assert not ACTUAL.lock.locked() and contact(world) == before
    resume()
    assert actual_app.start_sync()["connector_paused"] is False
    _free(ACTUAL)
    assert world.opens > before[0]


@pytest.mark.parametrize("kind", ["preview", "import", "verify"])
def test_the_import_path_is_refused_and_resuming_restores_it(world, kind):
    pause()
    before = contact(world)
    with pytest.raises(ConflictError) as refused:
        actual_app.start_migration(kind, BUDGET)
    assert refused.value.message == client.PAUSED
    assert contact(world) == before
    resume()
    started = actual_app.start_migration("preview", BUDGET)
    assert started["kind"] == "preview"
    deadline = time.time() + 60
    while time.time() < deadline and import_runs.get(started["id"])["status"] == "running":
        time.sleep(0.02)
    assert import_runs.get(started["id"])["status"] == "done"
    assert world.opens > before[0]


def test_settling_a_review_item_is_refused(world):
    pause()
    before = contact(world)
    with pytest.raises(ConflictError) as refused:
        actual_app.resolve_sync_item("anything", "onw")
    assert refused.value.message == client.PAUSED
    assert contact(world) == before


def test_moving_the_sync_to_another_budget_is_refused(world, monkeypatch):
    enable()
    with using(ACTUAL):
        before = sync_state.load()
    pause()
    monkeypatch.setattr(client, "list_budgets", _refuses_as_the_real_one_does)
    with pytest.raises(ConflictError) as refused:
        actual_app.sync_settings(None, None, "another-budget")
    assert refused.value.message == client.PAUSED
    with using(ACTUAL):
        assert sync_state.load() == before


def _refuses_as_the_real_one_does(store=None):
    client.refuse_while_paused(store)
    raise AssertionError("not reached while paused")


def test_what_reads_only_this_book_still_answers(world):
    enable()
    pause()
    before = contact(world)
    status = actual_app.sync_status()
    assert status["connector_paused"] is True and status["enabled"] is True
    assert actual_app.migration_status(BUDGET)["token_saved"] is True
    assert contact(world) == before


def test_the_sync_can_still_be_turned_off_while_paused(world):
    enable()
    pause()
    before = contact(world)
    assert actual_app.sync_settings(False, None, None)["enabled"] is False
    assert contact(world) == before


def test_the_page_says_so_first_and_stops_saying_it(world):
    enable()
    pause()
    warnings = actual_app.sync_status()["warnings"]
    assert warnings[0]["kind"] == "connector_paused"
    assert "paused" in warnings[0]["text"] and "Resume" in warnings[0]["text"]
    resume()
    status = actual_app.sync_status()
    assert status["connector_paused"] is False
    assert "connector_paused" not in {w["kind"] for w in status["warnings"]}


# ── a run under way ──────────────────────────────────────────────────────────


def test_a_run_under_way_stops_at_its_next_batch(world):
    with writes.open_sync_client(BUDGET, Budget(10)) as made:
        made.read_plan(None)
        pause()
        calls_before = list(world.calls)
        with pytest.raises(Stopped) as stopped:
            made.save_transactions("POST", [])
        assert stopped.value.message == client.PAUSED
        assert world.calls == calls_before


# ── the one-app rule stays simple ────────────────────────────────────────────


def test_a_paused_sync_still_holds_ynab_off(world):
    enable()
    pause()
    with using(YNAB):
        assert budget_app.other_sync_on() is ACTUAL


# ── nothing leaves the process ───────────────────────────────────────────────


@pytest.fixture
def counted(monkeypatch):
    """Every request the real class would make, answered by nobody."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return requests


@pytest.fixture
def work(tmp_path, monkeypatch):
    root = tmp_path / "actual_sync"
    monkeypatch.setattr(client, "work_root", lambda: root)
    return root


def test_the_real_client_makes_no_request_while_paused(db_isolated, counted, work):
    store = ActualBudgetConfigStore()
    store.update({"server_url": URL, "password": PASSWORD})
    for call in (
        lambda: client.list_budgets(store),
        lambda: client.open_copy(store, "f1").__enter__(),
    ):
        with pytest.raises(ConflictError) as refused:
            call()
        assert refused.value.message == client.PAUSED
    assert counted == []
    assert not work.exists() or list(work.iterdir()) == []

    store.update({"enabled": True})
    with pytest.raises(ExternalServiceError):
        client.list_budgets(store)
    assert counted, "resumed, it asks the server again"


def test_the_budget_list_route_is_refused_in_the_same_sentence(db_isolated, counted):
    app = FastAPI()
    app.include_router(router)
    ActualBudgetConfigStore().update({"server_url": URL, "password": PASSWORD})
    answer = TestClient(app).get("/api/actual-budget/budgets")
    assert answer.status_code == 409 and answer.json()["detail"] == client.PAUSED
    assert counted == []
