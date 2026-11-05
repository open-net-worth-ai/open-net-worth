"""What an Actual budget must look like before this app reads or writes it (Gate 0).

Every open of a budget replays the migrations its snapshot lacks, and ``actualpy`` records a
migration as done even when it ran only part of it. So the check is made BEFORE they run:

* ``check_pending`` compares the JavaScript migrations still to run with a reviewed safe list.
  A snapshot that needs ``remove_cache`` (budget amounts and notes are copied by code) or any
  JavaScript migration nobody has reviewed is refused, in plain words.
* ``check_schema`` runs after, and refuses a database missing a table or column this client
  reads or writes. Extra tables and columns are fine.

``budget_mode`` reads the mode as Gate 0 settled it: the preference, else ``metadata.json``,
else envelope. Pure: it takes an open ``sqlite3`` connection and never imports ``actual``.
"""

from __future__ import annotations

import re
import sqlite3

from backend.core.errors import ExternalServiceError

# JavaScript migrations reviewed against actualpy's partial replay (docs/adr, Gate 0). Each loses
# only data this app does not read, or (prefs) the mode, which is read from metadata instead.
SAFE_JS_MIGRATIONS = frozenset(
    {
        "reports_move_selected_categories",
        "create_dashboard_table",
        "prefs",
        "multiple_dashboards",
        "add_messages_pending",
    }
)

REFRESH = "Open it in Actual and use Settings, Reset sync, then try again."

# Every column this client reads or writes. Written ones are the ones a mismatch would corrupt.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "transactions": (
        "id isParent isChild acct category amount description notes date financial_id "
        "imported_description starting_balance_flag transferred_id sort_order tombstone cleared "
        "parent_id reconciled schedule error"
    ).split(),
    "categories": "id name is_income cat_group sort_order tombstone hidden".split(),
    "category_groups": "id name is_income sort_order tombstone hidden".split(),
    "category_mapping": "id transferId".split(),
    "payees": "id name category tombstone transfer_acct".split(),
    "payee_mapping": "id targetId".split(),
    "zero_budgets": "id month category amount carryover".split(),
    "reflect_budgets": "id month category amount carryover".split(),
    "accounts": "id name offbudget closed tombstone sort_order".split(),
    "notes": "id note".split(),
    "preferences": "id value".split(),
}

_NAME = re.compile(r"^(\d+)_(.+)\.(\w+)$")
_MODES = {
    "tracking": "tracking",
    "report": "tracking",
    "envelope": "envelope",
    "rollover": "envelope",
}


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def pending_names(conn: sqlite3.Connection, migration_files: list[str]) -> list[str]:
    """The migration files (base names) that ``conn``'s ``__migrations__`` table does not list.
    Mirrors ``actualpy``: a file counts only when it lives under ``migrations/``. A snapshot with
    no ``__migrations__`` table has everything pending, which the check then refuses."""
    try:
        done = {str(row[0]) for row in conn.execute("SELECT id FROM __migrations__")}
    except sqlite3.OperationalError:
        done = set()
    pending = []
    for file in migration_files:
        if not file.startswith("migrations"):
            continue
        base = _basename(file)
        if base.split("_")[0] not in done:
            pending.append(base)
    return pending


def check_pending(names: list[str]) -> None:
    """Raise ``ExternalServiceError`` when a pending migration cannot be replayed safely."""
    for name in names:
        match = _NAME.match(_basename(name))
        if match is None:
            raise ExternalServiceError(
                f"This budget needs a change this app doesn't recognise. {REFRESH}"
            )
        _, label, extension = match.groups()
        if extension == "sql":
            continue
        if extension != "js" or label == "remove_cache" or label not in SAFE_JS_MIGRATIONS:
            raise ExternalServiceError(
                "This budget was saved by an older Actual Budget than this app can read safely. "
                + REFRESH
            )


def check_schema(conn: sqlite3.Connection) -> None:
    """Raise ``ExternalServiceError`` naming what the database lacks."""
    missing = []
    for table, columns in REQUIRED_COLUMNS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            missing.append(table)
        else:
            missing += [f"{table}.{c}" for c in columns if c not in have]
    if missing:
        raise ExternalServiceError(
            "This Actual Budget database doesn't have what this app needs: "
            f"{', '.join(missing)}. Update this app, or open the budget in a matching Actual."
        )


def _mode_of(value) -> str | None:
    return _MODES.get(str(value or "").strip().lower())


def budget_mode(conn: sqlite3.Connection, metadata: dict) -> str:
    """``"envelope"`` or ``"tracking"``: the ``budgetType`` preference, else ``metadata.json``'s
    (``report`` is tracking), else envelope."""
    try:
        row = conn.execute("SELECT value FROM preferences WHERE id = 'budgetType'").fetchone()
    except sqlite3.OperationalError:
        row = None
    return _mode_of(row[0] if row else None) or _mode_of(metadata.get("budgetType")) or "envelope"
