"""Regression: creating/updating a transaction that BOTH matches an active Automation AND
resolves a payee must not self-deadlock now that AutomationStore/PayeeStore are DB-backed
(plan 4d task 2).

Before this fix, `record_committed_matches` (writes `AutomationStore.stats`) and
`payee_registry.note_usage`/`learn_category` (writes `PayeeStore`) ran INSIDE
`ledger.py`'s own `locked_unit_of_work(transactions_table, txn_audit_table)` transaction.
That was harmless while both stores were JsonStore-backed (an independent per-file lock);
once each became a real DB table, a write to a table NOT in that transaction's lock set,
issued from inside it, opens a SECOND transaction on the same SQLite connection pool while
the first is still open — SQLite's single-writer model blocks the second for
`PRAGMA busy_timeout` (5s) and then raises `OperationalError: database is locked`. The fix
(this same task) moves both calls to run AFTER the ledger transaction commits, mirroring the
identical fix already shipped for `corrections_service.record_correction` (plan 4a task 2).

This test proves the fix by actually exercising all three call sites end to end against a
real (isolated) SQLite database — if the self-deadlock regressed, this test would hang for
~5s per assertion and then fail with OperationalError, not silently pass.
"""

from __future__ import annotations

import pytest

from backend.db.automation_store import AutomationStore
from backend.db.payee_store import PayeeStore
from backend.services import ledger as svc

_CAT_FOOD = "cat_13095ba70000"  # a real BUILTIN_CATEGORIES id ("Food & Dining")


def _transform_automation(automation_id: str = "automation_test_transform", **overrides) -> dict:
    """A minimal, real transaction-transform Automation row — same shape as
    tests/test_automation_transaction_events.py's own `transform_automation` helper. Inserted
    via `AutomationStore.insert()` (the raw, unvalidated store primitive) rather than
    `.create()`: `.create()`'s `_make_automation` normalizer now requires
    `execution == {"kind": "lucius"}` (automations run Lucius exclusively since the
    2026-08-02 migration), so a structured `transaction_transform` execution — LEGACY data
    that `match_transaction_automation`/`apply_transaction_automations` still must read
    correctly (this file's whole subject) — can only exist as a raw row, exactly the shape a
    pre-migration automation still sitting in a real user's table has today."""
    return {
        "id": automation_id,
        "name": "Categorize coffee",
        "description": "",
        "instructions": "Categorize matching transactions.",
        "status": "active",
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": {"description_contains": "cafe"},
            "limit": "each",
        },
        "execution": {
            "kind": "transaction_transform",
            "set": {"category": _CAT_FOOD},
            "priority": 100,
            "stop": True,
            "override_existing": False,
        },
        "created_at": "2026-08-02T00:00:00+00:00",
        "updated_at": "2026-08-02T00:00:00+00:00",
        "provenance": {},
        "stats": {},
        **overrides,
    }


@pytest.fixture
def env(monkeypatch, db_isolated):
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    return db_isolated


def _expense(desc: str) -> dict:
    return {
        "kind": "expense",
        "date": "2026-05-31",
        "description": desc,
        "from_account_id": "acc_chk",
        "from_amount": 11.5,
        "from_currency": "SGD",
    }


def test_create_transaction_matches_automation_and_resolves_payee_without_deadlock(env):
    automation = AutomationStore().insert(_transform_automation())

    row = svc.create_transaction(_expense("Cafe on Cecil Street"), allow_duplicate=True)

    # Automation match applied AND its usage stats were durably written (record_committed_matches).
    assert row["category"] == _CAT_FOOD
    updated = AutomationStore().find_by_id(automation["id"])
    assert updated["stats"]["match_count"] == 1
    assert updated["stats"]["last_transaction_id"] == row["id"]

    # Payee resolved + usage/learning durably written (payee_registry.note_usage/learn_category).
    assert row["payee_id"] is not None
    payee = PayeeStore().find_by_id(row["payee_id"])
    assert payee["last_used"] == "2026-05-31"
    assert payee["default_categories"]["expense"] == _CAT_FOOD


def test_bulk_create_transactions_matches_automation_and_resolves_payee_without_deadlock(env):
    automation = AutomationStore().insert(_transform_automation())

    created, skipped = svc.bulk_create_transactions(
        [_expense("Cafe #1"), _expense("Cafe #2")], allow_duplicate=True
    )

    assert skipped == []
    assert all(t["category"] == _CAT_FOOD for t in created)
    updated = AutomationStore().find_by_id(automation["id"])
    assert updated["stats"]["match_count"] == 2

    payee_ids = {t["payee_id"] for t in created}
    assert None not in payee_ids
    for pid in payee_ids:
        assert PayeeStore().find_by_id(pid) is not None


def test_update_transaction_rebuild_matches_automation_without_deadlock(env):
    """update_transaction's category-changing path re-runs create_transaction internally
    (the rebuild) and then records automation usage in its OWN follow-up step — the third,
    previously-separate self-deadlock site."""
    automation = AutomationStore().insert(_transform_automation())
    row = svc.create_transaction(_expense("Corner store"), allow_duplicate=True)
    assert row["category"] is None  # doesn't match "cafe" yet

    updated = svc.update_transaction(row["id"], {"description": "Cafe on Cecil Street"})

    assert updated["category"] == _CAT_FOOD
    stats = AutomationStore().find_by_id(automation["id"])["stats"]
    assert stats["match_count"] == 1
