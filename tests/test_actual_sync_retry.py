"""A run that failed for a reason that may pass comes back sooner than the schedule; a refused
sign-in waits for the person. Same harness as ``test_actual_sync_engine``; nothing real."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_sync import state as sync_state
from tests import test_actual_sync_engine as engine
from tests.actual_sync_stub import PASSWORD, URL, down
from tests.test_actual_sync_engine import edit_here, enable, row, sync

world = engine.world

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _gap() -> timedelta:
    with using(ACTUAL):
        st = sync_state.load()
    started = sync_state.parse(st["last_run"]["started_at"])
    return sync_state.parse(st["next_run_at"]) - started


def test_a_failed_run_retries_sooner_then_backs_off_and_a_success_resets(world):
    enable(interval="6h")
    sync()
    assert _gap() == timedelta(hours=6)
    world.open_error = down()
    for minutes in (5, 15, 60, 60):
        assert engine._run()["ok"] is False
        assert _gap() == timedelta(minutes=minutes)
    world.open_error = None
    sync()
    assert _gap() == timedelta(hours=6)


def test_never_later_than_the_schedule(world):
    enable(interval="15m")
    sync()
    world.open_error = down()
    for _ in range(3):
        engine._run()
    assert _gap() == timedelta(minutes=15)


def test_a_refused_sign_in_waits_for_the_schedule(world):
    enable(interval="6h")
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.sync_error = httpx.HTTPStatusError(
        f"{URL} refused {PASSWORD}",
        request=httpx.Request("POST", URL),
        response=httpx.Response(401, request=httpx.Request("POST", URL)),
    )
    run = engine._run()
    assert run["ok"] is False and "sign-in" in run["error"]
    assert _gap() == timedelta(hours=6)


def test_a_sync_that_keeps_failing_says_so_in_worth_checking(world):
    from backend.services.actual_sync import app as actual_app

    enable(interval="6h")
    sync()
    world.open_error = down()
    for _ in range(2):
        engine._run()
    assert "failing" not in {w["kind"] for w in actual_app.sync_status()["warnings"]}
    engine._run()
    (line,) = [w["text"] for w in actual_app.sync_status()["warnings"] if w["kind"] == "failing"]
    assert line.startswith("The last 3 syncs failed") and "since 20" in line
    world.open_error = None
    sync()
    assert "failing" not in {w["kind"] for w in actual_app.sync_status()["warnings"]}
