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
    ``a:<month>:<ynab category id>`` -> the Assigned milliunits both sides held, and the pairing
    of YNAB's categories (``c:<ynab category id>``) and category groups (``g:<ynab group id>``)
    with ours, each with the name/group/hidden state both sides last agreed on."""

    TABLE = ynab_sync_baselines

    def __init__(self, engine=None):
        super().__init__(self.TABLE, engine=engine, checkpointed=False)

    def split(self) -> tuple[dict[str, dict], dict[tuple[str, str], int]]:
        txns, assigned = {}, {}
        for row in self.all():
            if row["id"].startswith("t:"):
                txns[row["id"][2:]] = row
            elif row["id"].startswith("a:"):
                assigned[(row["month"], row["category"])] = row["milli"]
        return txns, assigned

    def declined(self, plan_id: str | None) -> set[str]:
        """``y:<ynab category id>`` / ``o:<category id>`` the person chose to leave out."""
        return {
            r["id"][2:] for r in self.all() if r["id"].startswith("x:") and r.get("plan") == plan_id
        }

    def pair_documents(self) -> list[dict]:
        return [r for r in self.all() if r["id"][:2] in ("c:", "g:", "x:")]

    def of_other_plans(self, plan_id: str | None) -> list[dict]:
        """Pair and decision documents that are not this plan's (a rewrite keeps them)."""
        return [
            r for r in self.all() if r["id"][:2] in ("c:", "g:", "x:") and r.get("plan") != plan_id
        ]

    def pairs(self, plan_id: str | None) -> tuple[dict[str, dict], dict[str, dict]]:
        """The category and group pairs of one YNAB plan, by YNAB id."""
        cats, groups = {}, {}
        for row in self.all():
            if row.get("plan") != plan_id:
                continue
            if row["id"].startswith("c:"):
                cats[row["id"][2:]] = row
            elif row["id"].startswith("g:"):
                groups[row["id"][2:]] = row
        return cats, groups


class YnabConflictStore(BookPayloadTableStore):
    """The review list: items the sync would not apply on its own."""

    def __init__(self, engine=None):
        super().__init__(ynab_sync_conflicts, engine=engine, checkpointed=False)

    def open_items(self) -> list[dict]:
        return [c for c in self.all() if not c.get("resolved_at")]
