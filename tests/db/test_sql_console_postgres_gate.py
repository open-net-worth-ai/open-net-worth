"""M4 / scan#1 — the Postgres SQL console: the app-managed login, and the hardened scrub.

The login tests need a real server (``ONW_TEST_DATABASE_URL``, CI's ``db (postgres dialect)``
job). Roles are cluster-wide and outlive each test's dropped schema, so every role a test makes
is dropped in its teardown.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.core.errors import ValidationError
from backend.db import engine as eng
from backend.db import sql_console, sql_console_role

_PG_URL = os.environ.get("ONW_TEST_DATABASE_URL") or ""
needs_postgres = pytest.mark.skipif(
    not _PG_URL.startswith("postgres"), reason="needs a Postgres server (ONW_TEST_DATABASE_URL)"
)


def test_sqlite_needs_no_login(db_engine):
    if _PG_URL.startswith("postgres"):
        pytest.skip("sqlite only")
    assert sql_console.console_status().available


def test_settings_reads_whether_sql_can_actually_run(db_engine, monkeypatch):
    from backend.api import config as config_api

    monkeypatch.setattr(
        config_api,
        "console_status",
        lambda: sql_console_role.ConsoleStatus(False, "a reason", 'ALTER ROLE "x" CREATEROLE;'),
    )
    got = config_api.get_sql_query_config()
    assert got == {
        "enabled": True,
        "available": False,
        "reason": "a reason",
        "fix": 'ALTER ROLE "x" CREATEROLE;',
    }


# ── _scrub: every string form, so a ';' or a keyword inside one is data ──────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'a;b'",
        "SELECT E'it\\'s; DROP TABLE x'",
        "SELECT $$ ; DROP TABLE x $$",
        "SELECT $q$ ; DROP TABLE x $q$",
        "SELECT /* ; /* nested */ DROP */ 1",
    ],
)
def test_scrub_blanks_strings_and_nested_comments(sql):
    scrubbed = sql_console._scrub(sql)
    assert ";" not in scrubbed and "DROP" not in scrubbed
    sql_console.assert_statement_is_read_only(sql)  # one statement, no banned keyword


def test_scrub_keeps_dollar_parameters():
    assert sql_console._scrub("SELECT $1, $2") == "SELECT $1, $2"


def test_escaped_quote_in_e_string_does_not_end_the_string_early():
    # If the scrubber ended the string at \' it would see "; DELETE" as live SQL.
    with pytest.raises(ValidationError):
        sql_console.assert_statement_is_read_only("SELECT E'a\\'' ; DELETE FROM accounts")


# ── The textual scan still names the bypass shapes (the login is the real boundary) ──────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT query_to_xml('select * from secret_config', true, true, '')",
        "SELECT query_to_xml(E'select * from secret_config', true, true, '')",
        "SELECT query_to_xml($$select * from secret_config$$, true, true, '')",
        'SELECT * FROM U&"\\0073ecret_config"',
        "SELECT * FROM U&'x'",
    ],
)
def test_scan_refuses_postgres_bypass_shapes(sql):
    with pytest.raises(ValidationError):
        sql_console.assert_tables_allowed(sql)


def test_scan_still_allows_ordinary_literals():
    sql_console.assert_tables_allowed("SELECT payee FROM transactions WHERE payee = 'Café'")


# ── The app-managed login, against a real server ─────────────────────────────────────────


def _admin(sql: str) -> None:
    admin = create_engine(_PG_URL)
    with admin.begin() as c:
        c.execute(text(sql))
    admin.dispose()


def _drop_role(name: str) -> None:
    admin = create_engine(_PG_URL)
    with admin.begin() as c:
        if c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": name}).first():
            c.execute(text(f'DROP OWNED BY "{name}"'))
            c.execute(text(f'DROP ROLE "{name}"'))
    admin.dispose()


def _managed_role() -> str:
    return sql_console_role._managed_role_name(eng.get_engine())


def _bypass_scan(statement: str) -> list:
    """Straight to the database, skipping the text scan, so the LOGIN is what refuses."""
    return sql_console._run_postgres(statement, 10, 2000)[1]


@needs_postgres
def test_postgres_runs_one_prepared_statement_only(db_engine):
    assert sql_console.execute_readonly("SELECT 1 AS one")["rows"] == [[1]]
    with pytest.raises(ValidationError):
        sql_console.execute_readonly("SELECT 1; SELECT 2")


@needs_postgres
@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM secret_config",
        "SELECT * FROM connector_secrets",
        "SELECT * FROM connectors",
        "SELECT query_to_xml('select * from secret_config', true, true, '')",
        "SELECT set_config('role', session_user, true), "
        "query_to_xml('select * from secret_config', true, true, '')",
    ],
)
def test_the_app_made_login_cannot_read_credentials(db_engine, statement):
    status = sql_console.console_status()
    assert status.available and status.login == _managed_role()
    with pytest.raises(ValidationError, match="permission denied"):
        _bypass_scan(statement)


@needs_postgres
def test_conversations_are_readable_with_no_operator_step(db_engine):
    got = sql_console.execute_readonly(
        "SELECT s.title, COUNT(m.id) FROM sessions s "
        "LEFT JOIN session_messages m ON m.session_id = s.id GROUP BY s.id"
    )
    assert got["rows"] == []


@needs_postgres
def test_a_table_added_by_an_upgrade_is_readable_after_a_restart(db_engine):
    sql_console.console_status()
    _admin("CREATE TABLE added_by_upgrade (id int)")
    sql_console_role.reset()  # the restart after an upgrade
    assert sql_console.execute_readonly("SELECT COUNT(*) FROM added_by_upgrade")["rows"] == [[0]]


@needs_postgres
def test_access_someone_granted_to_a_credential_table_is_taken_back(db_engine):
    sql_console.console_status()
    _admin(f'GRANT SELECT ON secret_config TO "{_managed_role()}"')
    sql_console_role.reset()
    assert sql_console.console_status().available
    with pytest.raises(ValidationError, match="permission denied"):
        _bypass_scan("SELECT * FROM secret_config")


@needs_postgres
def test_a_login_that_could_switch_roles_is_refused(db_engine):
    sql_console.console_status()
    _admin(f'GRANT pg_read_all_data TO "{_managed_role()}"')
    try:
        sql_console_role.reset()
        status = sql_console.console_status()
        assert not status.available and "pg_read_all_data" in status.reason
        with pytest.raises(ValidationError, match="unavailable"):
            sql_console.execute_readonly("SELECT 1")
    finally:
        _admin(f'REVOKE pg_read_all_data FROM "{_managed_role()}"')


@needs_postgres
def test_the_apps_own_login_is_never_used_as_the_console(db_engine, monkeypatch):
    monkeypatch.setenv(sql_console.CONSOLE_URL_ENV, _PG_URL)
    status = sql_console.console_status()
    assert not status.available and "own database login" in status.reason
    with db_engine.connect() as c:  # nothing was revoked from the app itself
        c.execute(text("SELECT COUNT(*) FROM secret_config")).scalar_one()


@needs_postgres
def test_an_operator_made_login_is_synced_too(db_engine, monkeypatch):
    _drop_role("onw_test_operator_login")
    _admin("CREATE ROLE onw_test_operator_login LOGIN PASSWORD 'pw-for-test'")
    try:
        url = make_url(_PG_URL).set(username="onw_test_operator_login", password="pw-for-test")
        monkeypatch.setenv(sql_console.CONSOLE_URL_ENV, url.render_as_string(hide_password=False))
        assert sql_console.console_status().login == "onw_test_operator_login"
        assert sql_console.execute_readonly("SELECT COUNT(*) FROM sessions")["rows"] == [[0]]
        with pytest.raises(ValidationError, match="permission denied"):
            _bypass_scan("SELECT * FROM secret_config")
    finally:
        sql_console_role.reset()
        _drop_role("onw_test_operator_login")


@needs_postgres
def test_an_app_login_without_createrole_is_told_the_one_fix(db_engine, monkeypatch):
    _drop_role("onw_test_limited_app")
    _admin("CREATE ROLE onw_test_limited_app LOGIN NOCREATEROLE PASSWORD 'pw-for-test'")
    _admin("GRANT USAGE ON SCHEMA public TO onw_test_limited_app")
    _admin("GRANT SELECT, INSERT, UPDATE ON secret_config TO onw_test_limited_app")
    _drop_role(_managed_role())
    try:
        url = make_url(_PG_URL).set(username="onw_test_limited_app", password="pw-for-test")
        monkeypatch.setenv("ONW_DATABASE_URL", url.render_as_string(hide_password=False))
        eng.dispose_engine()
        status = sql_console.console_status()
        assert not status.available
        assert status.fix == 'ALTER ROLE "onw_test_limited_app" CREATEROLE;'
    finally:
        sql_console_role.reset()
        eng.dispose_engine()
        _drop_role("onw_test_limited_app")


@needs_postgres
def test_a_forgotten_password_is_reset_not_refused(db_engine):
    assert sql_console.console_status().available
    with db_engine.begin() as c:  # e.g. secrets stripped by a restore
        c.execute(text("DELETE FROM secret_config WHERE namespace = 'sql_console'"))
    sql_console_role.reset()
    assert sql_console.execute_readonly("SELECT 1")["rows"] == [[1]]


# ── Security review, 2026-10-28: each reproduced finding stays fixed ──────────────────────


@needs_postgres
def test_a_login_that_starts_as_the_restricted_role_cannot_pass_as_it(db_engine, monkeypatch):
    """Signing in as a privileged login with `options=-c role=<restricted>` made every check
    about `current_user` pass, while the session could switch back and read secret_config."""
    sql_console.console_status()
    restricted = _managed_role()
    _drop_role("onw_test_privileged")
    _admin("CREATE ROLE onw_test_privileged LOGIN SUPERUSER PASSWORD 'pw-for-test'")
    try:
        url = make_url(_PG_URL).set(
            username="onw_test_privileged",
            password="pw-for-test",
            query={"options": f"-c role={restricted}"},
        )
        monkeypatch.setenv(sql_console.CONSOLE_URL_ENV, url.render_as_string(hide_password=False))
        sql_console_role.reset()
        status = sql_console.console_status()
        assert not status.available and "different role than it signs in as" in status.reason
    finally:
        sql_console_role.reset()
        _drop_role("onw_test_privileged")


@needs_postgres
def test_a_session_advisory_lock_ends_with_the_query(db_engine):
    """A pooled console connection kept a model-taken advisory lock after rollback."""
    sql_console.execute_readonly("SELECT pg_advisory_lock(424242)")
    admin = create_engine(_PG_URL)
    with admin.connect() as c:
        held = c.execute(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = 424242")
        ).scalar_one()
    admin.dispose()
    assert held == 0


@needs_postgres
def test_a_dangerous_attribute_on_the_login_is_refused(db_engine):
    sql_console.console_status()
    _admin(f'ALTER ROLE "{_managed_role()}" CREATEDB')
    try:
        sql_console_role.reset()
        status = sql_console.console_status()
        assert not status.available and "CREATEDB" in status.reason
    finally:
        _admin(f'ALTER ROLE "{_managed_role()}" NOCREATEDB')


@needs_postgres
def test_every_query_rechecks_its_own_session(db_engine):
    """Access granted AFTER the login was prepared is caught by the next query, no restart."""
    assert sql_console.console_status().available
    _admin(f'GRANT pg_read_all_data TO "{_managed_role()}"')
    try:
        with pytest.raises(ValidationError, match="member of other roles"):
            sql_console.execute_readonly("SELECT 1")
        assert not sql_console_role.last_status().available
    finally:
        _admin(f'REVOKE pg_read_all_data FROM "{_managed_role()}"')


@needs_postgres
def test_a_stale_stored_password_is_reset_once(db_engine):
    assert sql_console.console_status().available
    with db_engine.begin() as c:  # e.g. another instance's secrets restored here
        c.execute(
            text(
                "UPDATE secret_config SET payload = CAST(:p AS json) WHERE namespace = 'sql_console'"
            ),
            {"p": '{"password": "not-the-real-one"}'},
        )
    sql_console_role.reset()
    assert sql_console.execute_readonly("SELECT 1")["rows"] == [[1]]
