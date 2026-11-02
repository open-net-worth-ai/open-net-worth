"""Stores behind the YNAB two-way sync (tables ``ynab_sync_*`` — backend/db/tables.py).

Bookkeeping only, never a credential (the token stays in ``connector_secrets``). None of it is
checkpointed: a Lucius turn rolling back must not rewind a cursor and replay writes already made
to YNAB. Dumb storage — what a baseline MEANS lives in ``services/ynab_sync``.
"""

from __future__ import annotations

from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import (
    ynab_sync_baselines,
    ynab_sync_conflicts,
    ynab_sync_mirror,
    ynab_sync_state,
)


class YnabSyncStateStore(BookPayloadTableStore):
    """Two singleton documents: ``state`` (plan, schedule, cursors, last run) and ``meta``
    (YNAB's accounts, category groups, categories and payees as last seen)."""

    def __init__(self, engine=None):
        super().__init__(ynab_sync_state, engine=engine, checkpointed=False)

    def doc(self, doc_id: str) -> dict:
        return self.find_by_id(doc_id) or {"id": doc_id}

    def save(self, doc: dict) -> dict:
        return self.upsert(doc)


class YnabMirrorStore(BookPayloadTableStore):
    """YNAB's transactions (split lines nested) as of the last sync, keyed by YNAB id."""

    def __init__(self, engine=None):
        super().__init__(ynab_sync_mirror, engine=engine, checkpointed=False)

    def by_id(self) -> dict[str, dict]:
        return {row["id"]: row for row in self.all()}


class YnabBaselineStore(BookPayloadTableStore):
    """The last-agreed state: ``t:<ledger txn id>`` -> its canonical form and YNAB refs,
    ``a:<month>:<ynab category id>`` -> the Assigned milliunits both sides held."""

    def __init__(self, engine=None):
        super().__init__(ynab_sync_baselines, engine=engine, checkpointed=False)

    def split(self) -> tuple[dict[str, dict], dict[tuple[str, str], int]]:
        txns, assigned = {}, {}
        for row in self.all():
            if row["id"].startswith("t:"):
                txns[row["id"][2:]] = row
            else:
                assigned[(row["month"], row["category"])] = row["milli"]
        return txns, assigned


class YnabConflictStore(BookPayloadTableStore):
    """The review list: items the sync would not apply on its own."""

    def __init__(self, engine=None):
        super().__init__(ynab_sync_conflicts, engine=engine, checkpointed=False)

    def open_items(self) -> list[dict]:
        return [c for c in self.all() if not c.get("resolved_at")]
