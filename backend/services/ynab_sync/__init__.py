"""Scheduled two-way sync between the ledger and one YNAB plan — transactions and each month's
Assigned. See ADR ``2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines``.

``runner`` runs and schedules it, ``resolve`` settles the review list. The reconcile rule is
``units.decide``; the translation between the two sides is ``context`` (YNAB -> ledger, the
migration's own mapping) and ``payloads`` (ledger -> YNAB).

Behind the ``ynab_import_and_sync`` flag (``services.ynab_flag``): off, the scheduler's tick
never runs it, the connector fetches the plan as before, and every other entry point refuses."""

import logging

from backend.services import budget_app, ynab_flag
from backend.services.ynab_sync import resolve, runner
from backend.services.ynab_sync.state import INTERVALS, SURFACE

logger = logging.getLogger(__name__)

start_now = ynab_flag.gated(runner.start_now)
status = ynab_flag.gated(runner.status)
update_settings = ynab_flag.gated(runner.update_settings)
resolve_item = ynab_flag.gated(resolve.resolve)


def run_if_due() -> bool:
    """The scheduler's tick has no request to say which app it is for, so it names each one with
    a sync that can run here (``budget_app.syncing_apps``) and runs that app's own. One app failing
    doesn't stop the next, and one that ``defers`` doesn't hold the tick; the first failure is raised
    once all have had their turn, for the scheduler to log."""
    ran = False
    failures: list[Exception] = []
    for app in budget_app.syncing_apps(failures.append):
        try:
            with budget_app.using(app):
                ran = runner.run_if_due() or ran
        except Exception as exc:
            failures.append(exc)
    for later in failures[1:]:
        logger.error("sync tick failed", exc_info=later)
    if failures:
        raise failures[0]
    return ran


def plan_in_sync(plan_id: str | None) -> bool:
    """Whether the two-way sync is on for this YNAB plan (it then owns the plan's transactions)."""
    from backend.services.ynab_sync import state

    if not ynab_flag.on():
        return False
    with budget_app.using(budget_app.YNAB):
        st = state.load()
    return bool(plan_id) and st["enabled"] and st["plan_id"] == plan_id


__all__ = [
    "INTERVALS",
    "SURFACE",
    "plan_in_sync",
    "resolve",
    "resolve_item",
    "run_if_due",
    "start_now",
    "status",
    "update_settings",
]
