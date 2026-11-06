"""One write on a copy's session, in the library's models: nothing here is sent or committed
(``writes`` does that). Takes the engine's request bodies, and refuses what can't be written, before
it touches a row, with ``Refused`` for the review list. No ``actual`` import: the models arrive from
``client``."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select, text

from backend.core.errors import ValidationError
from backend.services.actual_sync.shape_values import iso
from backend.services.ynab_sync.client import NotThere, Refused

_ORDER_STEP = 16384


def to_cents(milli: int) -> int:
    if not isinstance(milli, int) or isinstance(milli, bool) or milli % 10:
        raise Refused("This amount isn't a whole number of cents.")
    return milli // 10


def to_day(value: str) -> int:
    day = int(value.replace("-", ""))
    try:
        iso(day)
    except ValidationError:
        raise Refused("This date isn't a real calendar date.") from None
    return day


def check_lines(op: dict) -> None:
    """Actual doesn't enforce it, and shows a gap its own balances then have to explain."""
    lines = op.get("subtransactions")
    if not lines:
        return
    total = sum(s["amount"] for s in lines)
    if "amount" in op and total != op["amount"]:
        raise Refused("The lines of this split don't add up to its total.")


class Writer:
    def __init__(self, session, db) -> None:
        self.s, self.db = session, db
        self._payees: dict[str, str] | None = None

    # ── what a row points at ──
    def _account(self, account_id: str):
        account = self.s.get(self.db.Accounts, account_id)
        if account is None or account.tombstone:
            raise NotThere("That account is no longer in Actual.")
        return account

    def _category(self, category_id: str | None) -> str | None:
        if category_id is None:
            return None
        category = self.s.get(self.db.Categories, category_id)
        if category is None or category.tombstone:
            raise NotThere("That category is no longer in Actual.")
        return category_id

    def _named_payees(self) -> dict[str, str]:
        if self._payees is None:
            found = self.s.scalars(select(self.db.Payees).where(self.db.Payees.tombstone == 0))
            self._payees = {
                p.name.strip().casefold(): p.id for p in found if p.name and not p.transfer_acct
            }
        return self._payees

    def _payee(self, name: str) -> str:
        key = name.strip().casefold()
        known = self._named_payees()
        if key not in known:
            pid = str(uuid.uuid4())
            self.s.add(self.db.Payees(id=pid, name=name, tombstone=0, learn_categories=None))
            self.s.add(self.db.PayeeMapping(id=pid, target_id=pid))
            known[key] = pid
        return known[key]

    def _transfer_payee(self, account_id: str) -> str:
        found = self.s.scalars(
            select(self.db.Payees).where(
                self.db.Payees.transfer_acct == account_id, self.db.Payees.tombstone == 0
            )
        ).first()
        if found is None:
            raise NotThere("That account has no transfer payee in Actual.")
        return found.id

    # ── transactions ──
    def _row(self, *, acct: str, day: int, cents: int, **fields):
        row = self.db.Transactions(
            id=str(uuid.uuid4()),
            acct=acct,
            date=day,
            amount=cents,
            is_parent=0,
            is_child=0,
            tombstone=0,
            reconciled=0,
            starting_balance_flag=0,
            sort_order=time.time() * 1000,
            **fields,
        )
        self.s.add(row)
        return row

    def create(self, op: dict) -> str:
        """One new row; the id of the row the body describes (a transfer's other leg goes with it)."""
        account = self._account(op["account_id"])
        day, cents = to_day(op["date"]), to_cents(op["amount"])
        common = dict(notes=op.get("memo"), cleared=int(op.get("cleared") == "cleared"))
        if op.get("payee_id"):
            return self._create_transfer(account, day, cents, op, common)
        payee = self._payee(op["payee_name"]) if op.get("payee_name") else None
        lines = op.get("subtransactions") or []
        row = self._row(
            acct=account.id,
            day=day,
            cents=cents,
            payee_id=payee,
            category_id=None if lines else self._category(op.get("category_id")),
            **common,
        )
        self._write_lines(row, lines)
        return row.id

    def _create_transfer(self, account, day: int, cents: int, op: dict, common: dict) -> str:
        payee = self.s.get(self.db.Payees, op["payee_id"])
        if payee is None or payee.tombstone or not payee.transfer_acct:
            raise NotThere("That transfer's other account is no longer in Actual.")
        other = self._account(payee.transfer_acct)
        category = (
            self._category(op.get("category_id")) if account.offbudget != other.offbudget else None
        )
        mine = self._row(
            acct=account.id, day=day, cents=cents, payee_id=payee.id,
            category_id=category if not account.offbudget else None, **common,
        )  # fmt: skip
        twin = self._row(
            acct=other.id, day=day, cents=-cents, payee_id=self._transfer_payee(account.id),
            category_id=category if account.offbudget else None, **common,
        )  # fmt: skip
        mine.transferred_id, twin.transferred_id = twin.id, mine.id
        return mine.id

    def _children(self, parent_id: str) -> list:
        return list(
            self.s.scalars(
                select(self.db.Transactions)
                .where(
                    self.db.Transactions.parent_id == parent_id, self.db.Transactions.tombstone == 0
                )
                .order_by(text("rowid"))
            )
        )

    def _write_lines(self, row, lines: list[dict]) -> None:
        """Every line of a split, the old ones reused in order so their ids hold. None: a plain row."""
        old = self._children(row.id) if row.is_parent else []
        row.is_parent = 1 if lines else 0
        for index, spec in enumerate(lines):
            fields = dict(
                amount=to_cents(spec["amount"]),
                category_id=self._category(spec.get("category_id")),
                notes=spec.get("memo"),
            )
            if index < len(old):
                for name, value in fields.items():
                    setattr(old[index], name, value)
                continue
            child = self._row(
                acct=row.acct, day=row.date, cents=fields["amount"], payee_id=row.payee_id,
                category_id=fields["category_id"], notes=fields["notes"], cleared=row.cleared,
                parent_id=row.id,
            )  # fmt: skip
            child.is_child = 1
            # Actual lists a split's lines newest-sorted first; they show in the order written.
            child.sort_order = (row.sort_order or 0) - index - 1
        for extra in old[len(lines) :]:
            extra.delete()

    def update(self, op: dict) -> None:
        row = self.s.get(self.db.Transactions, op["id"])
        if row is None or row.tombstone:
            raise NotThere("This transaction is no longer in Actual.")
        if op.get("cleared") == "uncleared" and row.reconciled:
            raise Refused("This transaction is reconciled in Actual. Unlock it there first.")
        children = self._children(row.id) if row.is_parent else []
        if "date" in op:
            row.date = to_day(op["date"])
        if "amount" in op:
            row.amount = to_cents(op["amount"])
        if "memo" in op:
            row.notes = op["memo"]
        if op.get("cleared") in ("cleared", "uncleared"):
            row.cleared = int(op["cleared"] == "cleared")
        if "account_id" in op:
            row.acct = self._account(op["account_id"]).id
        if op.get("payee_name"):
            row.payee_id = self._payee(op["payee_name"])
        self._update_category(row, op)
        if "subtransactions" in op:
            self._write_lines(row, op["subtransactions"])
            children = self._children(row.id) if row.is_parent else []
        for child in children:
            child.date, child.acct, child.payee_id = row.date, row.acct, row.payee_id
            child.cleared = row.cleared

    def _update_category(self, row, op: dict) -> None:
        """An opening reads no category when Actual has it under an income one (``shape``), and
        that blank isn't a change to send back."""
        if "category_id" not in op or (row.starting_balance_flag and op["category_id"] is None):
            return
        row.category_id = self._category(op["category_id"])

    def delete(self, row_id: str) -> None:
        row = self.s.get(self.db.Transactions, row_id)
        if row is None or row.tombstone:
            raise NotThere("This transaction is no longer in Actual.")
        twin = self.s.get(self.db.Transactions, row.transferred_id) if row.transferred_id else None
        row.delete()
        if twin is not None and not twin.tombstone:
            twin.delete()

    # ── Assigned ──
    def budget_cell(self, month: int, category_id: str) -> list:
        table = self.db.ZeroBudgets
        return list(
            self.s.scalars(
                select(table).where(table.month == month, table.category_id == category_id)
            )
        )

    def set_budget(self, month: int, category_id: str, cents: int) -> None:
        cells = self.budget_cell(month, category_id)
        for cell in cells:
            cell.amount = cents
        if not cells:
            self._category(category_id)
            self.s.add(
                self.db.ZeroBudgets(
                    id=f"{month}-{category_id}", month=month, category_id=category_id, amount=cents
                )
            )

    # ── categories and groups ──
    def _next_order(self, table) -> float:
        orders = self.s.scalars(select(table.sort_order).where(table.tombstone == 0)).all()
        return max((o for o in orders if o is not None), default=0.0) + _ORDER_STEP

    def create_category(self, group_id: str, name: str) -> str:
        group = self.s.get(self.db.CategoryGroups, group_id)
        if group is None or group.tombstone:
            raise NotThere("That category group is no longer in Actual.")
        cid = str(uuid.uuid4())
        self.s.add(
            self.db.Categories(
                id=cid,
                name=name,
                hidden=False,
                is_income=group.is_income or 0,
                cat_group=group_id,
                sort_order=self._next_order(self.db.Categories),
                tombstone=0,
            )
        )
        self.s.add(self.db.CategoryMapping(id=cid, transfer_id=cid))
        return cid

    def update_category(self, category_id: str, fields: dict) -> None:
        category = self.s.get(self.db.Categories, category_id)
        if category is None:
            raise NotThere("That category is no longer in Actual.")
        if "name" in fields:
            category.name = fields["name"]
        if "hidden" in fields:
            category.hidden = bool(fields["hidden"])
        if "category_group_id" in fields:
            category.cat_group = fields["category_group_id"]

    def create_group(self, name: str) -> str:
        gid = str(uuid.uuid4())
        self.s.add(
            self.db.CategoryGroups(
                id=gid,
                name=name,
                is_income=0,
                hidden=False,
                sort_order=self._next_order(self.db.CategoryGroups),
                tombstone=0,
            )
        )
        return gid

    def update_group(self, group_id: str, name: str) -> None:
        group = self.s.get(self.db.CategoryGroups, group_id)
        if group is None:
            raise NotThere("That category group is no longer in Actual.")
        group.name = name

    def note(self, category_id: str) -> str | None:
        found = self.s.get(self.db.Notes, category_id)
        return found.note if found else None

    def set_note(self, category_id: str, note: str) -> None:
        found = self.s.get(self.db.Notes, category_id)
        if found is None:
            self.s.add(self.db.Notes(id=category_id, note=note))
        else:
            found.note = note
