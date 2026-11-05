"""Reads the tables ``shape`` needs from a downloaded Actual copy, with plain ``sqlite3``.

``actualpy``'s own query helpers hide ``category_mapping`` and ``payee_mapping`` and return partial
sets, so the copy is read here, read-only, and handed to the pure ``shape`` as dicts. Imports
nothing from ``actual``.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from backend.services.actual_sync.shape import Rows

_TABLES = {
    "accounts": "id, name, offbudget, closed, tombstone",
    "transactions": (
        "id, isParent, isChild, acct, category, amount, description, notes, date, "
        "starting_balance_flag, transferred_id, tombstone, cleared, parent_id, reconciled"
    ),
    "payees": "id, name, transfer_acct, tombstone",
    "payee_mapping": "id, targetId",
    "categories": "id, name, is_income, cat_group, hidden, tombstone",
    "category_groups": "id, name, is_income, hidden, tombstone",
    "category_mapping": "id, transferId",
    "notes": "id, note",
}
_BUDGET_TABLES = {"envelope": "zero_budgets", "tracking": "reflect_budgets"}
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def connect(directory: Path) -> sqlite3.Connection:
    """The copy's database, read-only."""
    conn = sqlite3.connect(f"{(directory / 'db.sqlite').resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _all(conn: sqlite3.Connection, table: str, columns: str) -> list[dict]:
    return [dict(row) for row in conn.execute(f"SELECT {columns} FROM {table}")]


def read_rows(conn: sqlite3.Connection, mode: str, today: str | None = None) -> Rows:
    rows = Rows(mode=mode, today=today)
    for table, columns in _TABLES.items():
        setattr(rows, table, _all(conn, table, columns))
    rows.budgets = _all(conn, _BUDGET_TABLES[mode], "month, category, amount, carryover")
    rows.held = read_held(conn)
    return rows


def read_held(conn: sqlite3.Connection) -> list[dict]:
    """Money held for later, by month. Only ever shown to a person, so a copy without the table
    reads as none held rather than stopping the run."""
    try:
        return _all(conn, "zero_budget_months", "id, buffered")
    except sqlite3.OperationalError:
        return []


def read_currency(conn: sqlite3.Connection) -> str | None:
    """The budget's default currency preference as a three-letter code, else ``None``."""
    row = conn.execute("SELECT value FROM preferences WHERE id = 'defaultCurrencyCode'").fetchone()
    code = str(row[0] or "").strip().upper() if row else ""
    return code if _CURRENCY.match(code) else None
