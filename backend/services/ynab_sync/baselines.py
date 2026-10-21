"""The last-agreed state, held in memory for a run and written back once: per ledger transaction
its YNAB ids and canonical form, per month + category the Assigned both sides held."""

from __future__ import annotations

from backend.db.ynab_sync_store import YnabBaselineStore
from backend.services.ynab_sync.canon import Canon

FLUSH_BULK = 200  # past this many changes, rewrite the table in one transaction


class Baselines:
    def __init__(self) -> None:
        self._store = YnabBaselineStore()
        self.txns, self.assigned = self._store.split()
        self._dirty: set[str] = set()
        self._gone: set[str] = set()

    def set_txn(self, onw_id: str, refs: list[str], c: Canon) -> None:
        self.txns[onw_id] = {"id": f"t:{onw_id}", "refs": refs, "canon": c}
        self._dirty.add(f"t:{onw_id}")

    def drop_txn(self, onw_id: str) -> None:
        if self.txns.pop(onw_id, None) is not None:
            self._gone.add(f"t:{onw_id}")

    def set_assigned(self, month: str, cid: str, milli: int) -> None:
        key = (month, cid)
        if milli:
            self.assigned[key] = milli
        elif self.assigned.pop(key, None) is None:
            return
        self._dirty.add(f"a:{month}:{cid}")

    def seed_assigned(self, values: dict[tuple[str, str], int]) -> None:
        """First run: YNAB's figures are the agreed starting point; the ledger's differences
        then read as the person's own changes and flow out through the normal rule."""
        for month, cid in list(self.assigned):
            self.set_assigned(month, cid, 0)
        for (month, cid), milli in values.items():
            self.set_assigned(month, cid, milli)

    def _doc(self, doc_id: str) -> dict | None:
        if doc_id.startswith("t:"):
            return self.txns.get(doc_id[2:])
        _, month, cid = doc_id.split(":", 2)
        milli = self.assigned.get((month, cid))
        return {"id": doc_id, "month": month, "category": cid, "milli": milli} if milli else None

    def flush(self) -> None:
        changes = self._dirty | self._gone
        if len(changes) > FLUSH_BULK:
            docs = [d for i in self.txns if (d := self._doc(f"t:{i}"))]
            docs += [d for m, c in self.assigned if (d := self._doc(f"a:{m}:{c}"))]
            self._store.replace_all(docs)
        else:
            for doc_id in changes:
                doc = self._doc(doc_id)
                self._store.upsert(doc) if doc else self._store.delete(doc_id)
        self._dirty, self._gone = set(), set()
