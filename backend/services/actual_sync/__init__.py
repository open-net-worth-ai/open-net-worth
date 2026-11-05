"""Actual Budget import and sync. ``client`` is the only module that imports ``actual``.

Behind the ``actual_budget_sync`` flag (``services.actual_flag``)."""

from __future__ import annotations

from backend.core.errors import ConflictError


def list_budgets() -> list[dict]:
    """The budgets the saved sign-in can see: ``{"id", "name", "currency"}`` each."""
    raise ConflictError("Listing Actual Budget budgets is not available yet.")
