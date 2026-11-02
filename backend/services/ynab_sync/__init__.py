"""Scheduled two-way sync between the ledger and one YNAB plan — transactions and each month's
Assigned. See ADR ``2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines``.

``runner`` runs and schedules it, ``resolve`` settles the review list. The reconcile rule is
``units.decide``; the translation between the two sides is ``context`` (YNAB -> ledger, the
migration's own mapping) and ``payloads`` (ledger -> YNAB).

Behind the ``ynab_import_and_sync`` flag (``services.ynab_flag``): off, the scheduler's tick
never runs it, the connector fetches the plan as before, and every other entry point refuses."""

from backend.services import ynab_flag
from backend.services.ynab_sync import resolve, runner
from backend.services.ynab_sync.state import INTERVALS, SURFACE

start_now = ynab_flag.gated(runner.start_now)
status = ynab_flag.gated(runner.status)
update_settings = ynab_flag.gated(runner.update_settings)
resolve_item = ynab_flag.gated(resolve.resolve)


def run_if_due() -> bool:
    """The scheduler's tick: never runs while the flag is off."""
    return ynab_flag.on() and runner.run_if_due()


def plan_in_sync(plan_id: str | None) -> bool:
    """Whether the two-way sync is on for this YNAB plan (it then owns the plan's transactions)."""
    from backend.services.ynab_sync import state

    if not ynab_flag.on():
        return False
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
