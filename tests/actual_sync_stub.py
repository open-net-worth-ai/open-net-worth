"""A made-up Actual server for the sync's tests: a sqlite file holding Actual's own tables.

``open_copy`` hands the real ``ActualClient`` a fresh copy of that file with a real ``actualpy``
session on it, so what the client writes goes through ``actualpy``'s models. ``sync`` brings in what
the person did in Actual since the copy was taken (``edit``); ``commit`` sends the copy's state back.
The network is the only thing faked. Every name and number is invented."""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import httpx
from sqlalchemy import create_engine

from backend.core.user_time import user_today
from backend.services import actual_flag
from backend.services.actual_sync import client as actual_client
from backend.services.actual_sync import rows as actual_rows
from backend.services.actual_sync import shape

actual_database = __import__("actual.database").database

BUDGET = "budget-1"
BUDGET_B = "budget-2"
HOST = "acme-actual.example.test"
PASSWORD = "PASSWORD-SENTINEL-9"
URL = f"http://{HOST}:5006"


def month_int(key: str) -> int:
    return int(key.replace("-", ""))


def day_int(iso: str) -> int:
    return int(iso.replace("-", ""))


class FakeActual:
    """``actual.Actual``, as far as ``ActualClient`` uses it."""

    def __init__(self, server: "FakeServer", path: Path):
        from sqlmodel import Session

        self.server, self.path = server, path
        self.home = server.path  # the budget this copy came from
        self.file = SimpleNamespace(file_id=BUDGET, group_id=server.group_id)
        self.engine = create_engine(f"sqlite:///{path}")
        self.session = actual_database.strong_reference_session(Session(self.engine))

    def uncommitted(self) -> bool:
        s = self.session
        return bool(s.new or s.dirty or s.info.get("messages"))

    def _current_group(self) -> None:
        """A reset changes the group, and the server refuses the old one until it is read again."""
        if self.file.group_id != self.server.group_id:
            request = httpx.Request("POST", URL)
            raise httpx.HTTPStatusError(
                "file-has-reset", request=request, response=httpx.Response(400, request=request)
            )

    def sync(self):
        server = self.server
        server.calls.append("sync")
        self._current_group()
        if self.uncommitted():
            server.synced_over_uncommitted = True
        if server.sync_error is not None:
            raise server.sync_error
        with closing(sqlite3.connect(self.path)) as conn:
            for sql, args in server.queue:
                conn.execute(sql, args)
            conn.commit()
        return []

    def commit(self):
        server = self.server
        server.calls.append("commit")
        self.session.flush()
        self.session.commit()
        self._current_group()
        if server.commit_error is not None:
            error, taken = server.commit_error
            server.commit_error = None
            if taken:
                server.take(self.path, self.home)
            raise error
        server.take(self.path, self.home)

    def close(self):
        self.session.close()
        self.engine.dispose()


class FakeServer:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "server.sqlite"
        self.budgets = {BUDGET: self.path}
        self.calls: list[str] = []
        self.queue: list[tuple[str, tuple]] = []
        self.sync_error: Exception | None = None
        self.commit_error: tuple[Exception, bool] | None = None
        self.open_error: Exception | None = None
        self.synced_over_uncommitted = False
        self.mode = "envelope"
        self.currency: str | None = "USD"
        self.copies: list[Path] = []
        self.group_id = "group-1"
        self.opens = 0
        self._create(self.path)

    @staticmethod
    def _create(path: Path) -> None:
        engine = create_engine(f"sqlite:///{path}")
        actual_database.SQLModel.metadata.create_all(engine)
        engine.dispose()

    def add_budget(self, budget_id: str) -> None:
        self.budgets[budget_id] = self.root / f"{budget_id}.sqlite"
        self._create(self.budgets[budget_id])

    @contextmanager
    def on(self, budget_id: str) -> Iterator[None]:
        """What's seeded, edited or read in this block is in that budget."""
        before, self.path = self.path, self.budgets[budget_id]
        try:
            yield
        finally:
            self.path = before

    def reset_sync(self) -> None:
        """Actual's Reset sync: a new group and snapshot, the same file id and row ids."""
        self.group_id = f"group-{uuid.uuid4().hex}"

    # ── what is on the server ──
    def insert(self, table: str, **values) -> None:
        columns = ", ".join(f'"{c}"' for c in values)
        marks = ", ".join("?" for _ in values)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values())
            )
            conn.commit()

    def account(self, id: str, name: str, off: int = 0) -> None:
        self.insert("accounts", id=id, name=name, offbudget=off, closed=0, tombstone=0)
        self.payee(f"pt-{id}", None, transfer=id)

    def payee(self, id: str, name: str | None, transfer: str | None = None) -> None:
        self.insert("payees", id=id, name=name, transfer_acct=transfer, tombstone=0)
        self.insert("payee_mapping", id=id, targetId=id)

    def group(self, id: str, name: str, income: int = 0) -> None:
        self.insert("category_groups", id=id, name=name, is_income=income, hidden=0, tombstone=0)

    def category(self, id: str, name: str, group: str, income: int = 0) -> None:
        self.insert(
            "categories", id=id, name=name, cat_group=group, is_income=income, hidden=0, tombstone=0
        )
        self.insert("category_mapping", id=id, transferId=id)

    def txn(self, id: str, acct: str, cents: int, iso: str, **kw) -> None:
        row = dict(
            isParent=0,
            isChild=0,
            acct=acct,
            amount=cents,
            date=day_int(iso),
            tombstone=0,
            cleared=1,
            reconciled=0,
            starting_balance_flag=0,
            sort_order=1.0,
        )
        self.insert("transactions", id=id, **{**row, **kw})

    def assign(self, key: str, category: str, cents: int) -> None:
        month = month_int(key)
        self.insert(
            "zero_budgets",
            id=f"{month}-{category}",
            month=month,
            category=category,
            amount=cents,
            carryover=0,
        )

    def edit(self, sql: str, *args) -> None:
        """The person, in Actual: on the server now, and in a copy at its next ``sync``."""
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(sql, args)
            conn.commit()
        self.queue.append((sql, args))

    def rows(self, table: str, where: str = "1=1", *args) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {where}", args)]

    def row(self, table: str, id: str) -> dict:
        (found,) = self.rows(table, "id = ?", id)
        return found

    def live(self, where: str = "1=1", *args) -> list[dict]:
        return self.rows("transactions", f"tombstone = 0 AND ({where})", *args)

    def plan(self) -> dict:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return shape.plan_from_rows(
                actual_rows.read_rows(conn, self.mode, user_today().isoformat())
            )

    def take(self, local: Path, home: Path) -> None:
        shutil.copy(local, home)
        with closing(sqlite3.connect(home)) as conn:
            for sql, args in self.queue:
                conn.execute(sql, args)
            conn.commit()

    # ── the door ──
    @contextmanager
    def open_copy(self, store, budget_id):
        actual_flag.require()
        if budget_id not in self.budgets:
            raise AssertionError(f"opened by {budget_id}, which isn't a budget's file id")
        self.opens += 1
        if self.open_error is not None:
            raise self.open_error
        directory = self.root / f"run-{self.opens}"
        directory.mkdir()
        self.copies.append(directory)
        shutil.copy(self.budgets[budget_id], directory / "db.sqlite")
        self.queue = []
        session = FakeActual(self, directory / "db.sqlite")
        session.home = self.budgets[budget_id]
        try:
            yield actual_client.Copy(
                directory=directory,
                name="ACME Budget",
                encrypted=False,
                currency=self.currency,
                server_version="26.11.0",
                mode=self.mode,
                metadata={},
                actual=session,
            )
        finally:
            session.close()
            shutil.rmtree(directory)

    def install(self, monkeypatch) -> "FakeServer":
        monkeypatch.setattr(actual_client, "open_copy", self.open_copy)
        monkeypatch.setattr(
            actual_client,
            "list_budgets",
            lambda store=None: [
                {"id": b, "name": f"ACME {b}", "currency": None, "encrypted": False}
                for b in self.budgets
            ],
        )
        return self


def down() -> httpx.ConnectError:
    return httpx.ConnectError(f"could not reach {URL} with {PASSWORD}")


def seed_other_budget(server: FakeServer, past: str, today: str) -> None:
    """A second made-up budget: its own account and rows, no id shared with ``seed``'s."""
    server.account("b1", "ZENITH Checking")
    server.payee("p-zenith", "Zenith Store")
    server.payee("p-zsb", "Starting Balance")
    server.group("zg", "Zenith Group")
    server.group("zi", "Income", income=1)
    server.category("z-food", "Zenith Food", "zg")
    server.category("z-sb", "Starting Balances", "zi", income=1)
    server.txn(
        "z-open", "b1", 50_000, past, description="p-zsb", category="z-sb", starting_balance_flag=1
    )
    server.txn(
        "z-1", "b1", -1_100, today, description="p-zenith", category="z-food", notes="zen one"
    )


def seed(server: FakeServer, key: str, past: str, today: str) -> None:
    """One small budget: three accounts, spending and income categories, a named opening, an
    expense, a salary, a transfer and a split."""
    server.account("a1", "ACME Checking")
    server.account("a2", "ACME Savings", off=1)
    server.account("a3", "ACME Card")
    server.payee("p-market", "Corner Market")
    server.payee("p-acme", "ACME Corp")
    server.payee("p-sb", "Starting Balance")
    server.group("g1", "Usual")
    server.group("gi", "Income", income=1)
    server.category("c-food", "Food", "g1")
    server.category("c-fun", "Fun", "g1")
    server.category("c-pay", "Salary", "gi", income=1)
    server.category("c-sb", "Starting Balances", "gi", income=1)
    server.txn(
        "t-open",
        "a1",
        100_000,
        past,
        description="p-sb",
        category="c-sb",
        starting_balance_flag=1,
    )
    server.txn("t-market", "a1", -2_500, today, description="p-market", category="c-food")
    server.txn("t-pay", "a1", 300_000, today, description="p-acme", category="c-pay")
    server.txn(
        "t-out", "a1", -10_000, today, description="pt-a2", transferred_id="t-in", notes="save"
    )
    server.txn(
        "t-in", "a2", 10_000, today, description="pt-a1", transferred_id="t-out", notes="save"
    )
    server.txn("t-split", "a1", -5_000, today, description="p-market", isParent=1)
    server.txn(
        "t-l1", "a1", -3_000, today, description="p-market", category="c-food", isChild=1,
        parent_id="t-split", notes="one",
    )  # fmt: skip
    server.txn(
        "t-l2", "a1", -2_000, today, description="p-market", category="c-fun", isChild=1,
        parent_id="t-split", notes="two",
    )  # fmt: skip
    server.assign(key, "c-food", 40_000)
    server.assign(key, "c-fun", 10_000)
