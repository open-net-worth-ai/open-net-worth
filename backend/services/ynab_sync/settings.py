"""The sync's controls for ``budget_app.current()``: what the page reads and what it can change.
Split from ``runner``, which runs the sync. The credentials stay in the config store, and appear in
no state, status or log line."""

from __future__ import annotations

from typing import Iterator

from backend.core.errors import ConflictError, ValidationError
from backend.services import ledger as ledger_service
from backend.services.budget_app import current, refuse_beside_others, starting
from backend.services.ynab_sync import conflicts
from backend.services.ynab_sync import state as sync_state

manual_runs: dict[str, str] = {}  # app key -> the record of its manual run in progress


def status() -> dict:
    app = current()
    st = sync_state.load()
    return {
        "token_saved": app.config_store().is_configured(),
        "enabled": st["enabled"],
        "interval": st["interval"],
        "intervals": list(sync_state.INTERVALS),
        "direction": st["direction"],
        "directions": list(sync_state.DIRECTIONS),
        "plan_id": st["plan_id"],
        "plan_name": st["plan_name"],
        "running": app.lock.locked(),
        "run_id": manual_runs.get(app.key),
        "last_run": st["last_run"],
        "next_run_at": st["next_run_at"] if st["enabled"] else None,
        "conflicts": conflicts.listing(),
    }


def _links() -> Iterator[str]:
    """The app's account ids that ledger accounts are linked to."""
    source = current().source
    return (
        e["ref"]
        for a in ledger_service.accounts.query_accounts(include_archived=True)
        for e in a.get("external_ids") or []
        if e.get("source") == source
    )


def _linked_book() -> bool:
    return any(_links())


def links_to(accounts: set[str]) -> bool:
    """Whether a ledger account is linked to one of these accounts of the app."""
    return any(ref in accounts for ref in _links())


def import_finished(plan_id: str) -> None:
    """A run that stopped to ask for this import has its answer. A run in progress works it out
    for itself."""
    app = current()
    if not app.caps.budget_scoped_accounts or not app.lock.acquire(blocking=False):
        return
    try:
        st = sync_state.load()
        if st["awaiting_import"] and st["plan_id"] == plan_id:
            st["awaiting_import"] = False
            sync_state.save(st)
    finally:
        app.lock.release()


def _adopt_plan(plan_id: str) -> dict:
    app = current()
    plans = app.reader.list_plans(app.config_store())
    plan = next((p for p in plans if p["id"] == plan_id), None)
    if plan is None:
        raise ValidationError(
            f"That {app.name} {app.plan_word} is not visible to the saved {app.saved_sign_in}."
        )
    currency = plan.get("currency") or app.reader.currency_of(app.config_store(), plan_id)
    currency = (currency or "").upper()
    if len(currency) != 3:
        raise ValidationError(
            f"That {app.name} {app.plan_word} has no currency set; open it in {app.name} once first."
        )
    switched = app.caps.copies_keep_ids and bool(sync_state.load()["plan_id"])
    sync_state.reset()
    return {
        **sync_state.blank(),
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "currency": currency,
        "switched": switched,
    }


def _refuse_beside_others() -> None:
    name = current().name
    refuse_beside_others(f"turning on the {name} sync", f"turn on the {name} sync")


def update_settings(
    enabled: bool | None,
    interval: str | None,
    plan_id: str | None,
    direction: str | None = None,
) -> dict:
    app = current()
    if not app.lock.acquire(blocking=False):
        raise ConflictError(
            f"{app.article} {app.name} sync is running; change the settings when it finishes."
        )
    try:
        if enabled:  # before a new plan forgets the old one's state, and again as it turns on
            _refuse_beside_others()
        st = sync_state.load()
        if plan_id and plan_id.strip() != st["plan_id"]:
            st = _adopt_plan(plan_id.strip())
        if interval is not None:
            st["interval"] = sync_state.validate_interval(interval)
        if direction is not None:
            st["direction"] = sync_state.validate_direction(direction)
        if enabled:
            _require_ready(st)
        if enabled is not None:
            st["enabled"] = enabled
        last = st["last_run"]
        if enabled or not last:
            st["next_run_at"] = None  # due on the scheduler's next tick
        else:
            sync_state.schedule_next(st, sync_state.parse(last["started_at"]))
        with starting():
            if enabled:
                _refuse_beside_others()
            sync_state.save(st)
    finally:
        app.lock.release()
    return status()


def _require_ready(st: dict) -> None:
    app = current()
    if not app.config_store().is_configured():
        raise ValidationError(f"save {app.sign_in} first")
    if not st["plan_id"]:
        raise ValidationError(f"pick the {app.name} {app.plan_word} to sync")
    if not _linked_book():
        raise ValidationError(
            f"migrate the {app.name} {app.plan_word} into this book first; "
            "the sync pairs with what it imported"
        )
