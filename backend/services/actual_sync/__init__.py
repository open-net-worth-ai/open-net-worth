"""Actual Budget import and sync. ``client`` is the only module that imports ``actual``.

Behind the ``actual_budget_sync`` flag (``services.actual_flag``): ``client`` checks it, and the
gateway, itself, so nothing here relies on the connector being ``enabled``."""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services.actual_sync import client
from backend.services.actual_sync.client import open_copy, sweep_leftovers

__all__ = ["list_budgets", "open_copy", "resolve_budget", "sweep_leftovers"]


def list_budgets(store: ActualBudgetConfigStore | None = None) -> list[dict]:
    """The budgets the saved sign-in can see: ``{"id", "name", "currency", "encrypted"}`` each."""
    return client.list_budgets(store)


def _currency_of(budget_id: str, store: ActualBudgetConfigStore | None) -> str | None:
    """The budget's own default currency, which only its database holds. Best effort: a budget
    that cannot be opened now reports the reason when it is next read, not when it is picked."""
    from backend.core.errors import OpenNetWorthError

    try:
        with client.open_copy(store, budget_id) as copy:
            return copy.currency
    except OpenNetWorthError:
        return None


def resolve_budget(budget_id: str, store: ActualBudgetConfigStore | None = None) -> dict:
    """Validate ``budget_id`` (a cloud file id) against the sign-in's real budget list and return
    its ``{"id", "name", "currency", "encrypted"}``, the currency read from the budget itself."""
    budgets = client.list_budgets(store)
    found = next((b for b in budgets if b["id"] == budget_id), None)
    if found is None:
        available = ", ".join(f"'{b['name']}' ({b['id']})" for b in budgets) or "none"
        raise ValidationError(
            f"That budget wasn't found on this Actual server. Available: {available}"
        )
    return {**found, "currency": _currency_of(found["id"], store)}
