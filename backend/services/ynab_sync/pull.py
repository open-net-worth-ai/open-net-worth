"""YNAB -> ledger: apply YNAB's version of a transaction through the ledger's own chokes
(create / update / delete), so it is validated and audited like a hand edit — under the sync's
audit surface, which is how the next scan knows not to send it straight back."""

from __future__ import annotations

import logging

from backend.core.errors import ConflictError, OpenNetWorthError
from backend.core.txn_sources import canonical_writer
from backend.services import ledger as ledger_service
from backend.services.ynab_migration.mapping import SOURCE
from backend.services.ynab_sync.canon import Canon, canon, changed_fields

logger = logging.getLogger(__name__)


class LedgerChanged(ConflictError):
    """The ledger row changed between the sync reading it and writing YNAB's version over it."""


_PLAIN = ("date", "description", "memo", "cleared", "kind")
_LEGS = ("account_id", "amount", "currency")


def row_changes(rendered: dict, row: dict, changed: list[str]) -> dict:
    """The ``update_transaction`` patch that makes ``row`` read like ``rendered``."""
    patch = {f: rendered.get(f) for f in _PLAIN if f in changed}
    if {"category", "splits"} & set(changed):
        patch["category"], patch["splits"] = rendered.get("category"), rendered.get("splits")
    if {"from", "to", "kind"} & set(changed):
        for side in ("from", "to"):
            for field in _LEGS:
                patch[f"{side}_{field}"] = rendered.get(f"{side}_{field}")
    have = {(e.get("source"), e.get("ref")) for e in row.get("external_refs") or []}
    if any((e["source"], e["ref"]) not in have for e in rendered["external_refs"]):
        patch["external_refs"] = rendered["external_refs"]
    return patch


def create(rendered: dict, *, resurrect: bool = False) -> dict:
    row = {**rendered, "source": canonical_writer(SOURCE) or SOURCE}
    pairs = {(e["source"], e["ref"]) for e in rendered["external_refs"]} if resurrect else None
    created, skipped = ledger_service.bulk_create_transactions(
        [row],
        force=True,
        dedup_by_external_ref=True,
        labels_as_given=True,
        resurrect_refs=pairs,
    )
    if not created:
        reason = (
            skipped[0].get("_duplicate_reason") if skipped else None
        ) or "already in the ledger"
        raise OpenNetWorthError(f"the ledger did not book it ({reason})")
    return created[0]


def _unchanged(row: dict) -> dict:
    """Re-read the row right before writing over it; the snapshot this run took may be stale."""
    now = ledger_service.transactions.find_by_id(row["id"])
    if (
        now is None
        or bool(now.get("deleted")) != bool(row.get("deleted"))
        or canon(now) != canon(row)
    ):
        raise LedgerChanged("this transaction changed here while the sync was running")
    return now


def apply(row: dict | None, rendered: dict | None) -> tuple[Canon, str | None]:
    """Make the ledger match YNAB's version (``rendered`` is None when YNAB removed it) and
    return ``(canonical form now held, ledger id)``. Raises ``OpenNetWorthError`` when the
    ledger refuses."""
    if rendered is None:
        if row and not row.get("deleted"):
            _unchanged(row)
            ledger_service.bulk_delete_transactions([row["id"]])
        return None, row["id"] if row else None
    if row is None or row.get("deleted"):
        created = create(rendered, resurrect=row is not None)
        return canon(created), created["id"]
    row = _unchanged(row)
    patch = row_changes(rendered, row, changed_fields(canon(row), canon(rendered)))
    updated = ledger_service.update_transaction(row["id"], patch) if patch else row
    return canon(updated or row), row["id"]
