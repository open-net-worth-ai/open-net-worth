"""Actual Budget as a ``BudgetApp``, and the way into the import for it.

The sync half is not here yet, so ``stores`` holds only what the import uses: the category pairs,
and a state that reads as "sync off" for ``budget_app.other_sync_on``.

Every entry point checks the flag and the gateway itself, whatever the connector's ``enabled``
says: the generic connector PATCH can set that with neither check.
"""

from __future__ import annotations

import threading

from backend.core.errors import ValidationError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.db.actual_sync_store import ActualBaselineStore
from backend.services import actual_flag
from backend.services.actual_sync.reader import ActualReader
from backend.services.budget_app import BudgetApp, Capabilities, SyncStores, using
from backend.services.ynab_migration import job


class _NoSyncState:
    def doc(self, doc_id: str) -> dict:
        return {"id": doc_id}


def _no_sync(*_args, **_kwargs):
    raise ValidationError("Keeping Actual in sync isn't available yet.")


ACTUAL = BudgetApp(
    key="actual_budget",
    name="Actual",
    source="actual_budget",
    surface="actual_sync",
    caps=Capabilities(
        future_dates=True,
        edit_splits=True,
        hide_categories=True,
        income_categories=True,
        card_payment_categories=False,
        compares_ready_to_assign=False,
        zero_based_on_fresh_book=True,
        request_cap=1_000_000,
        category_request_cap=1_000_000,
        flag_trail=False,
    ),
    config_store=ActualBudgetConfigStore,
    reader=ActualReader(),
    open_client=_no_sync,
    stores=SyncStores(
        state=_NoSyncState,
        mirror=_no_sync,
        baselines=ActualBaselineStore,
        conflicts=_no_sync,
    ),
    lock=threading.Lock(),
    is_on=actual_flag.on,
    asks_type_and_currency=True,
    syncs=False,
    plan_word="budget",
    sign_in="an Actual server address and sign-in",
    saved_sign_in="sign-in",
    run_busy="An Actual run is already in progress.",
)


def _open() -> None:
    actual_flag.require()
    ActualBudgetConfigStore().require_available()


def migration_status() -> dict:
    _open()
    with using(ACTUAL):
        return job.status()


def start_migration(kind: str, budget_id: str, **choices) -> dict:
    """``job.start`` for Actual. ``choices``: its keyword arguments."""
    _open()
    with using(ACTUAL):
        return job.start(kind, budget_id, **choices)
