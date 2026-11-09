"""The compatibility rules for opening an Actual budget: which pending migrations are safe to
replay, which columns must exist, and which budget mode a snapshot is in.

Every name and number is made up; the databases are built here, in memory.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.core.errors import ExternalServiceError
from backend.services.actual_sync import compat

SAFE_JS = [
    "1722717601000_reports_move_selected_categories.js",
    "1722804019000_create_dashboard_table.js",
    "1723665565000_prefs.js",
    "1765518577215_multiple_dashboards.js",
    "1788468782000_add_messages_pending.js",
]

SCHEMA = {
    "transactions": (
        "id isParent isChild acct category amount description notes date financial_id "
        "imported_description starting_balance_flag transferred_id sort_order tombstone "
        "cleared parent_id reconciled schedule error"
    ),
    "categories": "id name is_income cat_group sort_order tombstone hidden",
    "category_groups": "id name is_income sort_order tombstone hidden",
    "category_mapping": "id transferId",
    "payees": "id name category tombstone transfer_acct",
    "payee_mapping": "id targetId",
    "zero_budgets": "id month category amount carryover",
    "reflect_budgets": "id month category amount carryover",
    "accounts": "id name offbudget closed tombstone sort_order",
    "notes": "id note",
    "preferences": "id value",
}


def database(extra: dict[str, str] | None = None, drop: tuple[str, ...] = ()) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for table, columns in {**SCHEMA, **(extra or {})}.items():
        conn.execute(f"CREATE TABLE {table} ({', '.join(columns.split())})")
    for ref in drop:
        table, _, column = ref.partition(".")
        if column:
            cols = [c for c in SCHEMA[table].split() if c != column]
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
        else:
            conn.execute(f"DROP TABLE {table}")
    return conn


# ── pending migrations ──────────────────────────────────────────────────────


def test_known_safe_pending_passes():
    compat.check_pending(SAFE_JS)
    compat.check_pending(["migrations/" + name for name in SAFE_JS])


def test_sql_migrations_always_pass():
    compat.check_pending(
        ["1780606215000_add_bank_sync_status.sql", "1787013118115_add_account_groups.sql"]
    )


def test_nothing_pending_passes():
    compat.check_pending([])


def test_remove_cache_pending_refused():
    with pytest.raises(ExternalServiceError) as err:
        compat.check_pending(["1632571489012_remove_cache.js", *SAFE_JS])
    text = str(err.value)
    assert "Reset sync" in text and "Actual" in text


def test_unknown_js_migration_refused():
    with pytest.raises(ExternalServiceError) as err:
        compat.check_pending(["1799999999000_brand_new_thing.js"])
    assert "Reset sync" in str(err.value)


@pytest.mark.parametrize("name", ["mystery", "1799999999000_x.py", "1799999999000_x"])
def test_an_unrecognised_file_is_refused(name):
    with pytest.raises(ExternalServiceError):
        compat.check_pending([name])


def test_pending_names_come_from_the_migrations_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE __migrations__ (id INT PRIMARY KEY NOT NULL)")
    conn.executemany("INSERT INTO __migrations__ VALUES (?)", [(1548957970627,), (1723665565000,)])
    files = [
        "db.sqlite",
        "migrations/1548957970627_remove-db-version.sql",
        "migrations/1632571489012_remove_cache.js",
        "migrations/1723665565000_prefs.js",
        "migrations/1788468782000_add_messages_pending.js",
    ]
    assert compat.pending_names(conn, files) == [
        "1632571489012_remove_cache.js",
        "1788468782000_add_messages_pending.js",
    ]


def test_a_snapshot_with_no_migrations_table_has_everything_pending():
    conn = sqlite3.connect(":memory:")
    files = ["db.sqlite", "migrations/1632571489012_remove_cache.js"]
    assert compat.pending_names(conn, files) == ["1632571489012_remove_cache.js"]
    with pytest.raises(ExternalServiceError):
        compat.check_pending(compat.pending_names(conn, files))


# ── written columns ─────────────────────────────────────────────────────────


def test_the_full_schema_passes():
    compat.check_schema(database())


@pytest.mark.parametrize(
    "missing",
    [
        "transactions.starting_balance_flag",
        "transactions.transferred_id",
        "categories.hidden",
        "category_groups.is_income",
        "category_mapping.transferId",
        "payees.transfer_acct",
        "payee_mapping.targetId",
        "zero_budgets.carryover",
        "accounts.offbudget",
        "notes.note",
        "zero_budgets",
    ],
)
def test_missing_written_column_refused(missing):
    with pytest.raises(ExternalServiceError) as err:
        compat.check_schema(database(drop=(missing,)))
    assert missing.split(".")[0] in str(err.value)
    assert "doesn't have" in str(err.value) or "does not have" in str(err.value)


def test_extra_columns_fine():
    compat.check_schema(
        database(
            {
                "transactions": SCHEMA["transactions"] + " pending raw_synced_data brand_new",
                "accounts": SCHEMA["accounts"] + " account_group_id",
                "tags": "id tag",
            }
        )
    )


# ── budget mode ─────────────────────────────────────────────────────────────


def with_pref(value):
    conn = database()
    if value is not None:
        conn.execute("INSERT INTO preferences VALUES ('budgetType', ?)", (value,))
    return conn


@pytest.mark.parametrize(
    "pref,meta,expected",
    [
        ("tracking", {"budgetType": "rollover"}, "tracking"),
        ("envelope", {"budgetType": "report"}, "envelope"),
        ("report", {}, "tracking"),
        ("rollover", {}, "envelope"),
    ],
)
def test_mode_preference_wins(pref, meta, expected):
    assert compat.budget_mode(with_pref(pref), meta) == expected


@pytest.mark.parametrize("empty", [None, "", "  "])
def test_mode_falls_back_to_metadata(empty):
    assert compat.budget_mode(with_pref(empty), {"budgetType": "report"}) == "tracking"
    assert compat.budget_mode(with_pref(empty), {"budgetType": "tracking"}) == "tracking"
    assert compat.budget_mode(with_pref(empty), {"budgetType": "rollover"}) == "envelope"


def test_mode_defaults_to_envelope():
    assert compat.budget_mode(with_pref(None), {}) == "envelope"
    assert compat.budget_mode(with_pref("???"), {"budgetType": "also ???"}) == "envelope"
    assert compat.budget_mode(database(drop=("preferences",)), {}) == "envelope"
