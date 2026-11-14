"""Bank data sync — experience fixes after the 2026-11-15 reviews. Synthetic ACME data only."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import budget

pytestmark = pytest.mark.flags(bank_data_sync=True)
GC = PROVIDERS["gocardless"]


# ── Renewing a bank's access wakes a sync that was waiting a day for it ─────────────────────


def test_renewing_access_makes_a_waiting_sync_due_now(db_isolated):
    budget.record_outcome(GC, "reconnect_needed", "ACME Bank: access ended")
    assert not budget.is_due(GC)
    budget.access_renewed("gocardless")
    assert budget.is_due(GC)
    assert budget.state("gocardless")["failures"] == 0


def test_renewing_access_leaves_a_healthy_schedule_alone(db_isolated):
    budget.record_outcome(GC, "ok", None)
    due = budget.state("gocardless")["next_due_at"]
    budget.access_renewed("gocardless")
    assert budget.state("gocardless")["next_due_at"] == due
    assert not budget.is_due(GC, budget.now() + timedelta(minutes=1))
