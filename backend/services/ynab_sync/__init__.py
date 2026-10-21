"""Scheduled two-way sync between the ledger and one YNAB plan — transactions and each month's
Assigned. See ADR ``2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines``.

``runner`` runs and schedules it, ``resolve`` settles the review list. The reconcile rule is
``units.decide``; the translation between the two sides is ``context`` (YNAB -> ledger, the
migration's own mapping) and ``payloads`` (ledger -> YNAB)."""

from backend.services.ynab_sync import resolve
from backend.services.ynab_sync.runner import run_if_due, start_now, status, update_settings
from backend.services.ynab_sync.state import INTERVALS, SURFACE


def plan_in_sync(plan_id: str | None) -> bool:
    """Whether the two-way sync is on for this YNAB plan (it then owns the plan's transactions)."""
    from backend.services.ynab_sync import state

    st = state.load()
    return bool(plan_id) and st["enabled"] and st["plan_id"] == plan_id


__all__ = [
    "INTERVALS",
    "SURFACE",
    "plan_in_sync",
    "resolve",
    "run_if_due",
    "start_now",
    "status",
    "update_settings",
]
