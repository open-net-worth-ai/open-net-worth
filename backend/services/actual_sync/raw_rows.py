"""Actual's own transaction rows, as ``budget_app_snapshots`` keeps them: the row before
``shape`` reshapes it into YNAB's (so ``financial_id``, ``reconciled``, ``starting_balance_flag``,
the raw ``notes`` and the payee and category ids stay visible), a split's lines under ``children``.
The ref is Actual's transaction id, the one the ledger row's ``external_refs`` carry."""

from __future__ import annotations

from backend.services import budget_app_snapshots as snapshots
from backend.services.actual_sync import shape


def from_rows(rows: shape.Rows, currency: str | None) -> list[dict]:
    return [
        snapshots.snapshot_row(
            t["id"],
            t["raw"],
            date=t["date"],
            amount=t["amount"] / 1000,
            currency=currency,
            description=t["payee"],
            account=t["account"],
        )
        for t in shape.raw_transactions(rows)
    ]
