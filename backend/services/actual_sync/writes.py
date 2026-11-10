"""``YnabClient``'s methods for an Actual budget, on one fresh copy (``client.open_copy``).

Every write is one batch: sync, re-check what it will touch against what this run read (a row that
moved is refused as ``Moved`` and nothing is written), apply, commit. ``apply_changes`` has no
timestamp check, so nothing here ever syncs over an uncommitted edit, and a commit that fails ends
the run: the copy is discarded and the next run works it out afresh. If the server took a commit
whose answer was lost, ``units._claim_unowned`` links the row on that next run; row ids stay random,
so nothing can bring back a row the person deleted. This shrinks the same-field race to
milliseconds; it can't remove it (the server has no compare-and-set).

Rules never run. What a write answers with is read back from the copy, in the shape a read has, so
"the mirror holds what the app answered" stays true. The run has a deadline, checked between
batches, as each request's own timeout says nothing of a run of many."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services.actual_sync import apply_rows, client, shape, shape_delta
from backend.services.actual_sync.shape_values import text
from backend.services.budget_app import current
from backend.services.ynab_migration import source
from backend.services.ynab_sync import delta
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.client import Budget, Moved, NotThere, Refused, Stopped

logger = logging.getLogger(__name__)

KNOWLEDGE = 1  # Actual has no delta read: a later read reports what differs from the mirror
RUN_SECONDS = 20 * 60.0

_MOVED = "It changed in Actual while the sync was running, so it wasn't written."
_TOO_LONG = "This sync ran for too long and stopped. The next run carries on from here."
_STOPPED = "This sync stopped after an error. The next run works out what is left."
_PAUSED_PARTWAY = "The Actual connection was paused, so this run stopped. The next run carries on."
_CATEGORY_FIELDS = ("name", "hidden", "category_group_id", "deleted")


def _nested(plan: dict) -> dict[str, dict]:
    return {t["id"]: t for t in source.nest_transactions(plan)}


def _by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items}


class ActualClient:
    def __init__(self, copy, budget: Budget) -> None:
        self.copy, self.budget = copy, budget
        self._writer = apply_rows.Writer(copy.session, copy.db)
        self._deadline = time.monotonic() + RUN_SECONDS
        self._broken = False
        self._seen: dict[str, dict] | None = None
        self._seen_cats: dict[str, dict] = {}
        self._seen_groups: dict[str, dict] = {}
        self._seen_assigned: dict[tuple[str, str], int] = {}

    # ── what this run read ──
    def _note_read(self, plan: dict, rows_seen: dict[str, dict] | None = None) -> None:
        self._seen = rows_seen if rows_seen is not None else _nested(plan)
        self._seen_cats = _by_id(plan.get("categories") or [])
        self._seen_groups = _by_id(plan.get("category_groups") or [])
        self._seen_assigned = delta.assigned_of(plan)

    def _ensure_read(self) -> None:
        """A resolution reads nothing first: what the person looked at is the stored mirror."""
        if self._seen is None:
            self._note_read(self.copy.plan(), current().stores.mirror().by_id())

    def read_plan(self, knowledge: int | None) -> dict:
        """The whole plan, or what differs from the stored mirror, in YNAB's flat export shape."""
        self._ready()
        self.budget.spend()
        plan = self.copy.plan()
        if self._seen is None:
            self._note_read(plan)
        if knowledge is None:
            return {"plan": plan, "server_knowledge": KNOWLEDGE}
        mirror, meta = current().stores.mirror().by_id(), sync_state.load_meta()
        return {"plan": shape.delta_against(plan, mirror, meta), "server_knowledge": KNOWLEDGE}

    # ── one batch ──
    def _ready(self) -> None:
        if self._broken:
            raise Stopped(_STOPPED)
        if ActualBudgetConfigStore().paused():
            self._broken = True
            raise Stopped(_PAUSED_PARTWAY)
        if time.monotonic() > self._deadline:
            self._broken = True
            raise Stopped(_TOO_LONG)
        session = self.copy.session
        if session.new or session.dirty or session.info.get("messages"):
            self._broken = True
            logger.error("Actual sync found uncommitted edits before a sync")
            raise Stopped(_STOPPED)

    @contextmanager
    def _batch(self) -> Iterator[None]:
        """``_ready``, sync, then the caller's re-check and apply, then commit. A refusal leaves
        the copy as it was; anything else ends the run."""
        self._ready()
        self._ensure_read()
        self.budget.spend()
        session = self.copy.session
        try:
            self.copy.plainly(session.rollback)
            self.copy.pull()
            session.expire_all()
            self._writer = apply_rows.Writer(session, self.copy.db)
        except Exception:
            self._broken = True
            raise
        try:
            yield
            self.copy.plainly(session.flush)
        except Refused:
            self._rolled_back(session)
            raise
        except Exception:
            self._broken = True
            raise
        try:
            self.copy.commit()
        except Exception:
            self._broken = True
            raise

    def _rolled_back(self, session) -> None:
        try:
            self.copy.plainly(session.rollback)
        except Exception:
            self._broken = True
            raise

    def _apply(self, call: Callable, *args):
        """A ``Writer`` call, with whatever the library or database raises made plain."""
        return self.copy.plainly(call, *args)

    # ── transactions ──
    def _unmoved(self, now: dict[str, dict], ids: list[str]) -> None:
        for tid in ids:
            seen, row = (self._seen or {}).get(tid), now.get(tid)
            if seen is None or row is None or shape_delta.differs(seen, row):
                raise Moved(_MOVED)

    def save_transactions(self, method: str, rows: list[dict]) -> list[dict]:
        """POST (create) or PATCH (update by id) a batch, parents and lines together; returns the
        saved transactions as a read would show them."""
        for op in rows:
            apply_rows.check_lines(op)
            if "amount" in op:
                apply_rows.to_cents(op["amount"])
        made: list[str] = []
        with self._batch():
            before = _nested(self.copy.plan())
            if method == "PATCH":
                self._unmoved(before, [op["id"] for op in rows])
            for op in rows:
                if method == "POST":
                    made.append(self._apply(self._writer.create, op))
                else:
                    self._apply(self._writer.update, op)
        return self._saved(made if method == "POST" else [op["id"] for op in rows])

    def _saved(self, ids: list[str]) -> list[dict]:
        now = _nested(self.copy.plan())
        saved = []
        for tid in ids:
            if tid not in now:
                self._broken = True
                raise Stopped(_STOPPED)
            saved.append(now[tid])
            self._seen[tid] = now[tid]  # type: ignore[index]
            twin = now[tid].get("transfer_transaction_id")
            if twin in now:
                self._seen[twin] = now[twin]  # type: ignore[index]
        return saved

    def delete_transaction(self, txn_id: str) -> None:
        with self._batch():
            before = _nested(self.copy.plan())
            if txn_id not in before:
                raise NotThere("This transaction is no longer in Actual.")
            twin = before[txn_id].get("transfer_transaction_id")
            self._unmoved(before, [txn_id, *([twin] if twin in before else [])])
            self._apply(self._writer.delete, txn_id)
        for gone in (txn_id, twin):
            (self._seen or {}).pop(gone, None)

    # ── Assigned ──
    def set_assigned(self, month: str, category_id: str, milli: int) -> None:
        if self.copy.mode != "envelope":
            raise Refused("This budget is in tracking mode, so its Assigned isn't sent.")
        cents = apply_rows.to_cents(milli)
        key, number = (month, category_id), int(month.replace("-", ""))
        with self._batch():
            cells = self._apply(self._writer.budget_cell, number, category_id)
            held = (cells[-1].amount or 0) * 10 if cells else 0
            if held != self._seen_assigned.get(key, 0):
                raise Moved(_MOVED)
            self._apply(self._writer.set_budget, number, category_id, cents)
        self._seen_assigned[key] = milli

    # ── categories and groups ──
    def _read_cats(self) -> tuple[dict[str, dict], dict[str, dict]]:
        plan = self.copy.plan()
        return _by_id(plan["categories"]), _by_id(plan["category_groups"])

    def _category_unmoved(self, category_id: str) -> None:
        now = self._read_cats()[0].get(category_id)
        seen = self._seen_cats.get(category_id)
        if now is None or seen is None:
            raise NotThere("That category is no longer in Actual.")
        if any(now.get(f) != seen.get(f) for f in _CATEGORY_FIELDS):
            raise Moved(_MOVED)

    def _category_saved(self, category_id: str) -> dict:
        saved = self._read_cats()[0][category_id]
        self._seen_cats[category_id] = saved
        return saved

    def create_category(self, group_id: str, name: str) -> dict:
        """In ``group_id``, and of its kind: a new income category goes in an income group."""
        with self._batch():
            cid = self._apply(self._writer.create_category, group_id, name)
        return self._category_saved(cid)

    def update_category(self, category_id: str, fields: dict) -> dict:
        """Rename, move to another group, or hide. A category is never deleted from here."""
        with self._batch():
            self._category_unmoved(category_id)
            self._apply(self._writer.update_category, category_id, fields)
        return self._category_saved(category_id)

    def _group_saved(self, group_id: str) -> dict:
        saved = self._read_cats()[1][group_id]
        self._seen_groups[group_id] = saved
        return saved

    def create_group(self, name: str) -> dict:
        with self._batch():
            gid = self._apply(self._writer.create_group, name)
        return self._group_saved(gid)

    def update_group(self, group_id: str, name: str) -> dict:
        with self._batch():
            now = self._read_cats()[1].get(group_id)
            seen = self._seen_groups.get(group_id)
            if now is None or seen is None:
                raise NotThere("That category group is no longer in Actual.")
            if now.get("name") != seen.get("name"):
                raise Moved(_MOVED)
            self._apply(self._writer.update_group, group_id, name)
        return self._group_saved(group_id)

    def set_note(self, category_id: str, note: str) -> dict:
        """The Assigned trail. A note the person changed, or one too long to have been read whole,
        is left as it stands: the trail isn't worth losing their words for."""
        with self._batch():
            held = self._apply(self._writer.note, category_id)
            shown = text(held, 500) or None
            seen = self._seen_cats.get(category_id, {}).get("note")
            if held != shown or shown != seen:
                logger.info("Actual category note left as it stands")
                return {"id": category_id, "note": shown}
            self._apply(self._writer.set_note, category_id, note)
        self._seen_cats.setdefault(category_id, {})["note"] = note
        return {"id": category_id, "note": note}


@contextmanager
def open_sync_client(plan_id: str, budget: Budget) -> Iterator[ActualClient]:
    """``BudgetApp.open_client``: one fresh copy for one run, gone when it ends."""
    with client.open_copy(None, plan_id) as copy:
        yield ActualClient(copy, budget)
