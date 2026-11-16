"""Keeps what a budget app (YNAB, Actual) held, the way a bank connector keeps the row the bank
sent, so a booked transaction can be traced to the exact row behind it and the transaction drawer
can show it. The import and the two-way sync both read the whole budget; each reader builds one
row per app transaction (``ynab_migration.raw_rows``, ``actual_sync.raw_rows``) and ``stash`` keeps
them under the app's own source, keyed by the id the ledger row's ``external_refs`` carry.

Evidence only: it never decides anything. A failure is logged and the run goes on, and the rows
carry no ``provider_account_id``, so ``connector_snapshots.emitter_for`` still answers None for them
and a booking dedups exactly as it did before they were kept.
"""

from __future__ import annotations

import logging

from backend.core.untrusted import sanitize_untrusted
from backend.services import connector_snapshots
from backend.services import ledger as ledger_service
from backend.services.budget_app import current
from backend.services.connector_fetch import sanitize_raw

logger = logging.getLogger(__name__)


def snapshot_row(
    ref: str,
    raw: dict,
    *,
    date: str,
    amount: float,
    currency: str | None,
    description: str,
    account: str,
) -> dict:
    """One row for ``stash``. The five ``normalized`` fields are what ``connector_snapshots``
    hashes, built the way ``connector_fetch`` builds them, so a fetch of the same YNAB transaction
    finds the row unchanged instead of reporting a correction."""
    return {
        "ref": ref,
        "provider_account_id": None,
        "raw": raw,
        "normalized": {
            "date": date,
            "amount": amount,
            "currency": currency,
            "description": sanitize_untrusted(description),
            "account": account,
        },
    }


def stash(rows: list[dict]) -> None:
    """Keep ``rows`` for the current app, outside any transaction of the caller's. Never raises."""
    if not rows:
        return
    app = current()
    try:
        booked = {ref for _, ref in ledger_service.known_external_refs(app.source)}
        kept = [{**r, "raw": sanitize_raw(r["raw"])} for r in rows]
        connector_snapshots.stash_synced(app.source, kept, booked)
    except Exception:
        logger.warning("%s: the rows it sent were not kept", app.name, exc_info=True)
