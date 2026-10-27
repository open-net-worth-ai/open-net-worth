"""The connectors tables and the secret/restore/undo policy sets that guard them.

``connector_secrets`` is a credential table (SQL-denied, stripped from a sanitised backup);
``connectors`` is NOT a secret — it stays in a sanitised backup — but it holds raw server-chosen
MCP text and personal settings, so Lucius's SQL may not read it either. Both are non-restorable
and excluded from undo. Every check resolves by table NAME, the only granularity the policy sets
have.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.ai.checkpoint import _EXCLUDED_TABLES
from backend.core.errors import ValidationError
from backend.core.sensitive import (
    NON_RESTORABLE_TABLES,
    RETIRED_SECRET_TABLES,
    SECRET_TABLES,
    is_secret_table,
)
from backend.db import sql_console
from backend.db.connector_store import ConnectorStore
from backend.db.tables import connector_secrets, connectors, metadata

_SENTINEL = "sentinel-credential-9f2c"


@pytest.fixture
def store(db_isolated):
    s = ConnectorStore()
    s.create("plaid", "plaid", "Plaid", config={"environment": "sandbox"})
    s.set_secrets("plaid", {"secret": _SENTINEL})
    return s


def _checkpointed_db_file() -> Path:
    """The isolated test database as a plain file (WAL folded in), never the ambient DB_FILE."""
    from backend.db.engine import get_engine

    path = Path(get_engine().url.database)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return path


# ── the policy sets ───────────────────────────────────────────────────────────────────────


def test_only_connector_secrets_is_a_secret_table():
    assert "connector_secrets" in SECRET_TABLES
    assert is_secret_table("connector_secrets")
    assert "connectors" not in SECRET_TABLES
    assert not is_secret_table("connectors")


def test_both_tables_are_non_restorable():
    assert {"connectors", "connector_secrets"} <= NON_RESTORABLE_TABLES


def test_both_tables_are_checkpoint_excluded():
    assert {"connectors", "connector_secrets"} <= _EXCLUDED_TABLES


def test_the_policy_names_resolve_to_real_tables():
    assert {connectors.name, connector_secrets.name} <= set(metadata.tables)


# ── Lucius's SQL ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("table", ["connectors", "connector_secrets"])
def test_lucius_sql_is_denied_both_connector_tables(store, table):
    with pytest.raises(ValidationError):
        sql_console.execute_readonly(f"SELECT * FROM {table}")


@pytest.mark.parametrize("table", ["connectors", "connector_secrets"])
def test_the_schema_tool_hides_both_connector_tables(store, table):
    from backend.db import sql_introspect

    assert table not in sql_introspect.readable_tables()
    with pytest.raises(ValidationError):
        sql_introspect.describe_table(table)


def test_the_postgres_revoke_list_names_both_connector_tables():
    assert {"connectors", "connector_secrets"} <= sql_console.DENIED_TABLES
    doc = Path(__file__).resolve().parents[1] / "docs/product/operations/sql-console-postgres.md"
    assert "connector_secrets, connectors FROM onw_sql_console" in doc.read_text()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT c.id, s.secrets FROM connectors c JOIN connector_secrets s ON s.connector_id = c.id",
        "SELECT * FROM (SELECT secrets FROM connector_secrets)",
        "WITH x AS (SELECT secrets FROM connector_secrets) SELECT * FROM x",
        "SELECT secrets FROM CONNECTOR_SECRETS",
    ],
)
def test_no_query_shape_reaches_connector_secrets(store, sql):
    with pytest.raises(ValidationError):
        sql_console.execute_readonly(sql)


# ── backups ───────────────────────────────────────────────────────────────────────────────


def test_a_sanitised_backup_keeps_connectors_but_strips_their_secrets(store, tmp_path):
    from backend.services.backup import _sanitise_db_bytes

    stripped_bytes, stripped = _sanitise_db_bytes(_checkpointed_db_file().read_bytes())

    assert "connector_secrets" in stripped
    assert "connectors" not in stripped
    copy = tmp_path / "copy.db"
    copy.write_bytes(stripped_bytes)
    with sqlite3.connect(copy) as conn:
        assert conn.execute("SELECT id FROM connectors WHERE id = 'plaid'").fetchall() == [
            ("plaid",)
        ]
        assert conn.execute("SELECT * FROM connector_secrets").fetchall() == []
    assert _SENTINEL.encode() not in stripped_bytes


def test_a_restore_puts_the_current_connectors_and_secrets_back_together(store):
    """The backup's copy of both tables is replaced by what was live, parents before children
    (``connector_secrets`` references ``connectors``): clearing the parent first would cascade-
    delete the child rows it is about to re-insert."""
    from backend.db.engine import get_engine
    from backend.services import backup

    captured = backup._capture_non_restorable_rows()
    assert captured["connector_secrets"][0]["secrets"] == {"secret": _SENTINEL}

    # Simulate the onw.db swap: the "backup" had a different connector and different secret.
    with get_engine().begin() as conn:
        conn.execute(connector_secrets.delete())
        conn.execute(connectors.delete())
    store.create("ynab", "ynab", "YNAB")
    store.set_secrets("ynab", {"access_token": "from-the-old-backup"})

    backup._reapply_non_restorable_rows(captured)

    assert {r["id"] for r in store.list()} == {"coingecko", "yahoo_finance", "plaid"}
    assert store.secrets("plaid") == {"secret": _SENTINEL}
    assert store.secrets("ynab") == {}


def test_the_restore_table_order_puts_parents_first():
    from backend.services import backup

    names = [t.name for t in backup._non_restorable_tables()]
    assert names.index("connectors") < names.index("connector_secrets")


# ── QA clone ──────────────────────────────────────────────────────────────────────────────


def test_a_qa_clone_carries_neither_connector_table(store, tmp_path):
    from backend.scripts.clone_data_for_qa import _copy_sanitised_sqlite

    dest = tmp_path / "clone.db"

    _copy_sanitised_sqlite(_checkpointed_db_file(), dest)

    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT * FROM connectors").fetchall() == []
        assert conn.execute("SELECT * FROM connector_secrets").fetchall() == []
    assert _SENTINEL.encode() not in dest.read_bytes()


# ── a backup taken BEFORE the upgrade still carries the retired mcp_clients table ──────────


def _pre_migration_db(path: Path) -> bytes:
    """A database shaped like one from before the connectors migration: the old ``mcp_clients``
    table holding a server whose URL and token are credentials, beside an ordinary table."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE mcp_clients (seq INTEGER, id TEXT PRIMARY KEY, payload JSON)")
        conn.execute(
            "INSERT INTO mcp_clients VALUES (1, 'zap', ?)",
            (
                f'{{"url": "https://actions.example.com/s/{_SENTINEL}/mcp", "token": "{_SENTINEL}"}}',
            ),
        )
        conn.execute("CREATE TABLE payees (id TEXT, name TEXT)")
        conn.execute("INSERT INTO payees VALUES ('p1', 'Corner Cafe')")
    return path.read_bytes()


def test_the_retired_table_set_names_mcp_clients():
    assert "mcp_clients" in RETIRED_SECRET_TABLES
    assert RETIRED_SECRET_TABLES.isdisjoint(metadata.tables)


def test_a_sanitised_backup_strips_the_retired_mcp_clients_table(tmp_path):
    from backend.services.backup import _sanitise_db_bytes

    raw = _pre_migration_db(tmp_path / "old.db")

    stripped_bytes, stripped = _sanitise_db_bytes(raw)

    assert "mcp_clients" in stripped
    copy = tmp_path / "copy.db"
    copy.write_bytes(stripped_bytes)
    with sqlite3.connect(copy) as conn:
        assert conn.execute("SELECT * FROM mcp_clients").fetchall() == []
        assert conn.execute("SELECT name FROM payees").fetchall() == [("Corner Cafe",)]
    assert _SENTINEL.encode() not in stripped_bytes


def test_a_qa_clone_strips_the_retired_mcp_clients_table(tmp_path):
    from backend.scripts.clone_data_for_qa import _copy_sanitised_sqlite

    source = tmp_path / "old.db"
    _pre_migration_db(source)
    dest = tmp_path / "clone.db"

    _copy_sanitised_sqlite(source, dest)

    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT * FROM mcp_clients").fetchall() == []
    assert _SENTINEL.encode() not in dest.read_bytes()
