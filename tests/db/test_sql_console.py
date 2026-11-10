"""The read-only SQL console — the refusal matrix.

This file is the evidence behind two of the three claims the feature is allowed to make:
Lucius cannot WRITE, and Lucius cannot read a CREDENTIAL. Everything else here is bounds and
ergonomics; these two are the security boundary, so they are tested adversarially — the
question is not "does the happy path work" but "can I get through".

Runs against SQLite here and Postgres in CI (tests/db/conftest.py::db_engine), which matters:
the two dialects enforce the table boundary by DIFFERENT mechanisms (a post-parse authorizer
vs. a fail-closed identifier scan), so a test that only ever ran on SQLite would be proving
half the system.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db import sql_console
from backend.db.account_store import AccountStore


@pytest.fixture
def seeded(db_engine):
    """Two real accounts, so a SELECT has something to return."""
    store = AccountStore(engine=db_engine)
    store.create(
        {"id": "acc_a", "name": "Checking", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    store.create(
        {
            "id": "acc_b",
            "name": "Card",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
        }
    )
    return db_engine


# ── CLAIM 1: it cannot write ─────────────────────────────────────────────────────────────

WRITE_STATEMENTS = [
    "INSERT INTO accounts (id, book_id, seq, name, kind, type, currency, data) VALUES ('x','b',9,'n','asset','checking','SGD','{}')",
    "UPDATE accounts SET name = 'pwned'",
    "DELETE FROM accounts",
    "DROP TABLE accounts",
    "ALTER TABLE accounts ADD COLUMN sneaky TEXT",
    "CREATE TABLE evil (id TEXT)",
    "CREATE TEMP TABLE evil (id TEXT)",
    "REPLACE INTO accounts (id) VALUES ('x')",
    "TRUNCATE TABLE accounts",
    "VACUUM",
    "REINDEX",
]


@pytest.mark.parametrize("statement", WRITE_STATEMENTS)
def test_every_write_statement_is_refused(seeded, statement):
    with pytest.raises(ValidationError):
        sql_console.execute_readonly(statement)


def test_the_data_is_actually_unchanged_after_a_refused_write(seeded):
    with pytest.raises(ValidationError):
        sql_console.execute_readonly("UPDATE accounts SET name = 'pwned'")
    result = sql_console.execute_readonly("SELECT name FROM accounts ORDER BY name")
    assert [r[0] for r in result["rows"]] == ["Card", "Checking"]


def test_attach_is_refused(seeded):
    """ATTACH reads any file on disk as a database — and can CREATE one."""
    with pytest.raises(ValidationError):
        sql_console.execute_readonly("ATTACH DATABASE '/tmp/evil.db' AS evil")


def test_pragma_is_refused(seeded):
    with pytest.raises(ValidationError):
        sql_console.execute_readonly("PRAGMA table_list")


def test_multiple_statements_are_refused(seeded):
    """The classic smuggle: a legitimate read followed by a write."""
    with pytest.raises(ValidationError) as exc:
        sql_console.execute_readonly("SELECT 1; DELETE FROM accounts")
    assert "one statement" in str(exc.value).lower()


def test_a_write_hidden_after_a_leading_cte_is_refused(seeded):
    """Postgres accepts `WITH … AS (…) DELETE …`, so a naive 'starts with WITH' check passes
    it. The token scan is what catches this one."""
    with pytest.raises(ValidationError):
        sql_console.execute_readonly(
            "WITH doomed AS (SELECT id FROM accounts) DELETE FROM accounts"
        )


def test_a_keyword_inside_a_comment_cannot_hide_a_write(seeded):
    with pytest.raises(ValidationError):
        sql_console.execute_readonly("SELECT 1 /* harmless */ ; DROP TABLE accounts")


# ── CLAIM 2: it cannot read a credential ─────────────────────────────────────────────────

SECRET_REACHES = [
    "SELECT * FROM secret_config",
    'SELECT * FROM "secret_config"',
    "SELECT * FROM SECRET_CONFIG",
    "WITH leak AS (SELECT * FROM secret_config) SELECT * FROM leak",
    "SELECT (SELECT COUNT(*) FROM secret_config) AS n",
    "SELECT id FROM accounts UNION ALL SELECT namespace FROM secret_config",
    "SELECT * FROM connector_secrets",
    "SELECT * FROM accounts WHERE id IN (SELECT namespace FROM secret_config)",
]


@pytest.mark.parametrize("statement", SECRET_REACHES)
def test_every_route_to_a_credential_table_is_refused(seeded, statement):
    with pytest.raises(ValidationError):
        sql_console.execute_readonly(statement)


def test_the_operators_own_conversations_are_readable(seeded):
    """Every conversation on a normal instance is the operator's own — titles and messages are
    readable, so Lucius can find an earlier chat. Only a demo denies them."""
    got = sql_console.execute_readonly(
        "SELECT s.title, COUNT(m.id) FROM sessions s "
        "LEFT JOIN session_messages m ON m.session_id = s.id GROUP BY s.id"
    )
    assert got["rows"] == []


def test_denied_tables_reuses_the_canonical_secret_set():
    """SECRET_TABLES is reused, never re-listed — a newly-classified credential table becomes
    unreadable here automatically, with no second list to forget."""
    from backend.core.sensitive import SECRET_TABLES

    assert SECRET_TABLES <= sql_console.DENIED_TABLES


# ── The happy path ───────────────────────────────────────────────────────────────────────


def test_a_plain_select_works(seeded):
    result = sql_console.execute_readonly("SELECT 1 AS n")
    assert result["columns"] == ["n"]
    assert result["rows"] == [[1]]
    assert result["truncated"] is False


def test_a_real_query_over_real_data(seeded):
    result = sql_console.execute_readonly(
        "SELECT kind, COUNT(*) AS n FROM accounts GROUP BY kind ORDER BY kind"
    )
    assert result["rows"] == [["asset", 1], ["liability", 1]]


def test_a_string_literal_containing_a_keyword_is_not_refused(seeded):
    """A payee legitimately called 'DROP TABLE' must not break the tool."""
    result = sql_console.execute_readonly("SELECT name FROM accounts WHERE name = 'DROP TABLE'")
    assert result["rows"] == []


# ── Bounds and honesty ───────────────────────────────────────────────────────────────────


def test_the_row_cap_bites_and_reports_itself_honestly(seeded):
    result = sql_console.execute_readonly("SELECT id FROM accounts", limit=1)
    assert result["showing"] == 1
    assert result["truncated"] is True
    # A STRING, never a fabricated number — a bounded read must never look complete.
    assert "unknown" in result["total"]


def test_an_uncapped_result_reports_its_real_total(seeded):
    result = sql_console.execute_readonly("SELECT id FROM accounts")
    assert result["truncated"] is False
    assert result["total"] == "2"


def test_the_limit_is_clamped_to_max_rows(seeded):
    assert sql_console._clamp_limit(10**9) == sql_console.MAX_ROWS
    assert sql_console._clamp_limit(0) == 1
    assert sql_console._clamp_limit(None) == sql_console.DEFAULT_ROWS
    assert sql_console._clamp_limit("nonsense") == sql_console.DEFAULT_ROWS


def test_a_huge_cell_is_truncated(seeded):
    result = sql_console.execute_readonly(
        f"SELECT '{'x' * (sql_console.MAX_CELL_CHARS * 3)}' AS big"
    )
    assert len(result["rows"][0][0]) == sql_console.MAX_CELL_CHARS + 1  # + the ellipsis


def test_a_parsed_json_cell_is_serialised_as_json_not_a_python_repr():
    """A Postgres JSON column arrives as a dict; SQLite returns text. Either way the model
    (and ``sql_introspect._payload_keys``, which json.loads it) must see valid JSON."""
    import json

    cell = sql_console._cell({"currency": "SGD", "tags": ["a", None]})
    assert json.loads(cell) == {"currency": "SGD", "tags": ["a", None]}


def test_a_clipped_cell_says_so_instead_of_looking_complete(seeded):
    """ai/tools/_bounded.py's invariant applied to the CELL cap: an ellipsis is not a
    machine-readable signal, and a model reading a clipped payload sees plausible JSON and
    concludes the missing field does not exist."""
    result = sql_console.execute_readonly(
        f"SELECT '{'x' * (sql_console.MAX_CELL_CHARS * 3)}' AS big"
    )
    assert result["cells_truncated"] == 1
    assert "INCOMPLETE" in result["cells_truncated_note"]
    assert "json_extract" in result["cells_truncated_note"]


def test_an_unclipped_result_carries_no_truncation_noise(seeded):
    """A flag that is always present is a flag nobody reads."""
    result = sql_console.execute_readonly("SELECT name FROM accounts")
    assert "cells_truncated" not in result


def test_the_cell_cap_clips_nothing_in_the_core_money_tables(seeded):
    """Regression guard on the number itself. Measured against a real 401 MB instance: the
    largest cell across transactions/accounts/positions/categories/payees/envelopes/lots was
    1,401 chars. If this cap ever drops below that, real data starts silently disappearing
    from ordinary queries."""
    assert sql_console.MAX_CELL_CHARS >= 1500


def test_empty_sql_is_refused():
    with pytest.raises(ValidationError):
        sql_console.execute_readonly("   ")


def test_a_broken_query_reports_the_sql_error(seeded):
    with pytest.raises(ValidationError) as exc:
        sql_console.execute_readonly("SELECT nope FROM accounts")
    assert "SQL error" in str(exc.value)
