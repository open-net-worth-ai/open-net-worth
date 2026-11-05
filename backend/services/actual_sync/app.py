"""Actual Budget as a ``BudgetApp``, and the way into its import and sync.

Every entry point checks the flag and the gateway itself, whatever the connector's ``enabled``
says: the generic connector PATCH can set that with neither check. The tick reads the same through
``is_on``.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.db.actual_sync_store import (
    ActualBaselineStore,
    ActualConflictStore,
    ActualMirrorStore,
    ActualSyncStateStore,
)
from backend.services import actual_flag
from backend.services.actual_sync import writes
from backend.services.actual_sync.reader import ActualReader
from backend.services.budget_app import (
    BudgetApp,
    Capabilities,
    SyncStores,
    other_sync_on,
    using,
)
from backend.services.ynab_migration import job
from backend.services.ynab_sync import budget_mode, resolve, runner
from backend.services.ynab_sync import state as sync_state


def _usable() -> bool:
    return actual_flag.on() and ActualBudgetConfigStore().available()


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
        resolve_request_cap=1_000_000,
        categorized_transfers=True,
    ),
    config_store=ActualBudgetConfigStore,
    reader=ActualReader(),
    open_client=writes.open_sync_client,
    stores=SyncStores(
        state=ActualSyncStateStore,
        mirror=ActualMirrorStore,
        baselines=ActualBaselineStore,
        conflicts=ActualConflictStore,
    ),
    lock=threading.Lock(),
    pool=ThreadPoolExecutor(max_workers=1, thread_name_prefix="actual-sync"),
    is_on=_usable,
    defers=True,
    asks_type_and_currency=True,
    article="An",
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


# ── the sync ──


def _notes(meta: dict) -> list[dict]:
    """What the page shows for as long as it holds. Each is ``{"item", "reason"}``, as a run's."""
    notes = []
    if meta.get("budget_mode") == budget_mode.TRACKING:
        notes.append(
            {"item": budget_mode.MODE_ITEM, "reason": budget_mode.tracking_line(ACTUAL.name)}
        )
    if other := other_sync_on():
        notes.append(
            {
                "item": f"{other.name} sync",
                "reason": (
                    f"The {other.name} sync is on, so this one waits. A book syncs with one app at "
                    "a time: turn one of them off."
                ),
            }
        )
    return notes


def _warnings(meta: dict) -> list[dict]:
    """Where money isn't landing where it should. Each is ``{"kind", "text"}``."""
    pairs = ACTUAL.stores.baselines().pairs(sync_state.load()["plan_id"])[0]
    return [
        {"kind": "income_in_envelope", "text": found["reason"]}
        for found in budget_mode.income_in_envelopes(meta, pairs)
    ]


def _status() -> dict:
    status = runner.status()
    meta = sync_state.load_meta()
    return {
        **status,
        "available": True,
        "budget_mode": meta.get("budget_mode"),
        "notes": _notes(meta),
        "warnings": _warnings(meta),
        "conflicts_count": len(status["conflicts"]),
    }


def sync_status() -> dict:
    """``runner.status`` for Actual, plus what its page says beside it."""
    _open()
    with using(ACTUAL):
        return _status()


def sync_settings(
    enabled: bool | None,
    interval: str | None,
    plan_id: str | None,
    direction: str | None = None,
) -> dict:
    _open()
    with using(ACTUAL):
        runner.update_settings(enabled, interval, plan_id, direction)
        return _status()


def start_sync() -> dict:
    _open()
    with using(ACTUAL):
        runner.start_now()
        return _status()


def resolve_sync_item(conflict_id: str, keep: str, choice: str | None = None) -> dict:
    _open()
    with using(ACTUAL):
        resolve.resolve(conflict_id, keep, choice)
        return _status()
