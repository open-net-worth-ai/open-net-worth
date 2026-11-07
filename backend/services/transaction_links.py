"""Everything that points at a transaction, and what happens to it when the row goes away.

The rule: deleting a transaction never leaves something the user can't fix. A delete here is a soft
delete, an UPDATE, so no foreign key can do this work. Every delete choke calls ``release`` in its
own database transaction; a merge keeps the money, so it calls ``move`` instead. A new store that
points at a transaction joins ``_LINKS``, or ``NOT_RELEASED`` with the reason, and a guard test
fails until it does. ADR 2026-11-06-deleting-a-transaction-leaves-nothing-unfixable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy import Connection

from backend.core.user_time import user_today
from backend.db.commitments_store import CommitmentsStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.lot_store import LotStore
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.tables import commitments, income_events, lots, owned_assets, realized_events

# Every table a release, move or move-back writes. A caller holds these locks.
LOCK_TABLES = (commitments, income_events, lots, owned_assets, realized_events)


def is_gone(txn: Optional[dict]) -> bool:
    """A link to this row is no link: it doesn't exist, or it was deleted or merged away."""
    return not txn or bool(txn.get("deleted") or txn.get("merged_into"))


def _installment_unpaid(row: dict) -> dict:
    if row.get("status") != "paid":
        return {}
    past_due = (row.get("due_date") or "") <= user_today().isoformat()
    return {"status": "due" if past_due else "upcoming"}


def _funding_not_arrived(row: dict) -> dict:
    return {"status": "planned"} if row.get("status") == "done" else {}


def _no_changes(row: dict) -> dict:
    return {}


def _qty(value: Any) -> str:
    return f"{float(value or 0):g}"


@dataclass(frozen=True)
class _Link:
    kind: str
    store: Callable[[], Any]
    source_file: str
    field: str
    label: Callable[[dict, dict], str]
    what: str
    before: str
    after: str
    effect: str
    nested_in: Optional[str] = None
    on_release: Callable[[dict], dict] = _no_changes

    def rows(self, owner: dict) -> list[dict]:
        return (owner.get(self.nested_in) or []) if self.nested_in else [owner]

    def describe(self, owner: dict, row: dict, txn_id: str) -> dict:
        return {
            "kind": self.kind,
            "id": owner.get("id"),
            "row_id": row.get("id"),
            "txn_id": txn_id,
            "label": self.label(owner, row),
            "what": self.what,
            "before": self.before,
            "after": self.after,
            "effect": self.effect,
        }

    def write(self, owner: dict, changes: dict, *, conn: Connection) -> None:
        patch = {self.nested_in: self.rows(owner)} if self.nested_in else changes
        self.store().update_by_id(owner["id"], patch, conn=conn)


_LINKS = (
    _Link(
        kind="lot",
        store=LotStore,
        source_file="lot_store.py",
        field="txn_id",
        label=lambda _, r: f"{_qty(r.get('original_qty'))} shares bought {r.get('acquired_at')}",
        what="linked purchase",
        before="Linked",
        after="None",
        effect="The lot keeps its shares and cost, but no longer has a linked purchase.",
    ),
    _Link(
        kind="sale",
        store=RealizedEventStore,
        source_file="realized_event_store.py",
        field="txn_id",
        label=lambda _, r: f"{_qty(r.get('quantity'))} shares sold {r.get('disposed_at')}",
        what="cash deposit",
        before="Linked",
        after="None",
        effect="The sale and its gain stay on record, but it no longer has a cash deposit.",
        on_release=lambda _: {"proceeds_account_id": None},
    ),
    _Link(
        kind="installment",
        store=CommitmentsStore,
        source_file="commitments_store.py",
        field="paid_txn_id",
        nested_in="installments",
        label=lambda o, r: f"{o.get('name')}: {r.get('label') or r.get('due_date')}",
        what="status",
        before="Paid",
        after="Unpaid",
        effect="This installment goes back to unpaid. You can link another payment to it.",
        on_release=_installment_unpaid,
    ),
    _Link(
        kind="funding",
        store=CommitmentsStore,
        source_file="commitments_store.py",
        field="realized_txn_id",
        nested_in="funding_plan",
        label=lambda o, r: f"{o.get('name')}: {r.get('label') or 'funding'}",
        what="status",
        before="Arrived",
        after="Planned",
        effect="This funding goes back to planned. You can link another receipt to it.",
        on_release=_funding_not_arrived,
    ),
    _Link(
        kind="asset_sale",
        store=OwnedAssetsStore,
        source_file="owned_assets_store.py",
        field="disposal_txn_id",
        label=lambda _, r: f"{r.get('name')}, sold {r.get('disposed_on')}",
        what="proceeds in the ledger",
        before="Linked",
        after="None",
        effect=(
            "The asset stays sold with no proceeds in the ledger. Ask Lucius to record the sale "
            "again to rebook them."
        ),
    ),
    _Link(
        kind="income",
        store=IncomeEventStore,
        source_file="income_event_store.py",
        field="txn_id",
        label=lambda _, r: f"{r.get('income_kind') or 'income'} paid {r.get('paid_at')}",
        what="cash row",
        before="Linked",
        after="None",
        effect="The income record stays, but it no longer has a cash row.",
    ),
)

RELEASED_FIELDS = tuple((link.source_file, link.field) for link in _LINKS)

NOT_RELEASED = {
    ("account_store.py", "opening_txn_id"): "the delete chokes refuse a Starting Balance row",
    ("correction_store.py", "txn_id"): "history: what a correction was made against",
    ("rejected_identity_store.py", "txn_id"): "history: the memory of the delete itself",
    ("transaction_audit_store.py", "txn_id"): "history: the audit trail must outlive the row",
    ("transaction_receipt_store.py", "transaction_id"): (
        "the receipt stays attached so an undo brings both back; unlinking or deleting it never "
        "needs the row to be live"
    ),
    ("vest_event_store.py", "txn_id"): (
        "None since 2026-09-22; older events read their tax from the row, and undoing the vest "
        "works on a deleted row"
    ),
}


def linked_to(txn_ids: list[str], *, conn: Connection | None = None) -> list[dict]:
    """What deleting these rows would change. Writes nothing: the delete card and the register's
    confirm dialog show exactly what ``release`` will do."""
    ids = set(txn_ids)
    found: list[dict] = []
    for link in _LINKS:
        for owner in link.store().all(conn=conn):
            found += [
                link.describe(owner, row, row[link.field])
                for row in link.rows(owner)
                if row.get(link.field) in ids
            ]
    return found


def release(txn_ids: list[str], *, conn: Connection) -> list[dict]:
    """Unlink everything pointing at these rows and return what changed, for the caller's result.
    Runs in the delete's own transaction, so a failure here rolls the delete back too."""
    return _rewrite({txn_id: None for txn_id in txn_ids}, releasing=True, conn=conn)


def move(moves: dict[str, str], *, conn: Connection) -> list[dict]:
    """A merge keeps the money, so its links follow the row that survives."""
    return _rewrite(moves, releasing=False, conn=conn)


def move_back(moved: list[dict], *, conn: Connection) -> None:
    """Undo ``move``: return exactly the rows it moved, never ones already on the survivor."""
    by_kind = {link.kind: link for link in _LINKS}
    for record in moved:
        link = by_kind[record["kind"]]
        owner = link.store().find_by_id(record["id"], conn=conn)
        if owner is None:
            continue
        row = next((r for r in link.rows(owner) if r.get("id") == record["row_id"]), None)
        if row is None or row.get(link.field) != record["moved_to"]:
            continue
        changes = {link.field: record["txn_id"]}
        row.update(changes)
        link.write(owner, changes, conn=conn)


def _rewrite(moves: dict[str, Optional[str]], *, releasing: bool, conn: Connection) -> list[dict]:
    if not moves:
        return []
    touched: list[dict] = []
    for link in _LINKS:
        for owner in link.store().all(conn=conn):
            hits = [r for r in link.rows(owner) if r.get(link.field) in moves]
            changes: dict = {}
            for row in hits:
                record = link.describe(owner, row, row[link.field])
                touched.append(
                    record if releasing else {**record, "moved_to": moves[record["txn_id"]]}
                )
                changes = {
                    link.field: moves[row[link.field]],
                    **(link.on_release(row) if releasing else {}),
                }
                row.update(changes)
            if hits:
                link.write(owner, changes, conn=conn)
    return touched
