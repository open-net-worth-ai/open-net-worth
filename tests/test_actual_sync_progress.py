"""A long sync says how far it has got: the step it is on, with a count, in the status the page
polls. Same harness as ``test_actual_sync_engine``; nothing real."""

from __future__ import annotations

import pytest

from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_sync import recorder, runner
from backend.services.ynab_sync import settings as sync_settings
from backend.services.ynab_sync import state as sync_state
from tests import test_actual_sync_engine as engine
from tests.test_actual_sync_engine import enable, new_row, sync

world = engine.world

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _run_watching(monkeypatch) -> list[str | None]:
    """One run the way the page sees it: holding the lock, the status read at every step."""
    seen: list[str | None] = []
    said = recorder.phase

    def phase(name: str) -> None:
        said(name)
        seen.append(sync_settings.status()["step"])

    monkeypatch.setattr(recorder, "phase", phase)
    with using(ACTUAL):
        assert ACTUAL.lock.acquire(blocking=False)
        try:
            runner._locked_run("manual")
            assert sync_settings.status()["step"] is None  # the run ended: no step left
        finally:
            ACTUAL.lock.release()
        assert sync_state.load()["last_run"]["ok"]
    return seen


def test_a_long_sync_counts_its_steps_and_the_status_shows_them(world, monkeypatch):
    enable()
    sync()
    for n in range(5):
        new_row(description=f"ACME Shop {n}", from_amount=10 + n)
    monkeypatch.setattr(recorder, "PROGRESS_EVERY", 2)
    seen = _run_watching(monkeypatch)
    assert "reading Actual" in seen
    assert [s for s in seen if s and s.startswith("sending")] == [
        "sending transactions to Actual: 2 of 5",
        "sending transactions to Actual: 4 of 5",
        "sending transactions to Actual: 5 of 5",
    ]
    assert any(s and s.startswith("comparing transactions: ") for s in seen)


def test_a_short_sync_names_its_steps_without_counts(world, monkeypatch):
    enable()
    sync()
    new_row(description="ACME Shop")
    seen = _run_watching(monkeypatch)
    assert "reconciling transactions" in seen
    assert not [s for s in seen if s and " of " in s]
