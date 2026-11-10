"""Postgres: the SQL console's own database login, kept in step with ``DENIED_TABLES`` by the app.

Postgres has no authorizer, so the table boundary there is a LOGIN that the database itself
refuses to let read the denied tables (``db/sql_console.py``'s docstring). It must be a login
of its own, never ``SET ROLE`` on the app's connection: the session user could switch back
mid-statement (``set_config('role', …)``) and run a nested ``query_to_xml`` as itself.

The app owns that login's permissions — an operator never runs a GRANT (ADR
``2026-10-27-the-app-keeps-the-postgres-sql-login-in-step``):

* **Managed (default):** the app creates ``onw_sql_<database>`` with a random password kept in
  ``secret_config`` (itself denied), the first time it is needed. Creating a login needs
  ``CREATEROLE`` on the app's own login; without it SQL access is unavailable and
  ``console_status()`` names the one command that fixes it.
* **Operator-supplied:** ``ONW_SQL_CONSOLE_DATABASE_URL`` names a login made by hand. The app
  syncs that login's table permissions the same way.

Preparation (once per process, retried while unavailable) grants SELECT on every table outside
the denied set and revokes everything on the denied set. Then EVERY session — at preparation and
again on each query's own connection — must pass ``_SESSION_CHECK``, asked of the database by
the session itself: it signed in as the role it runs as, that login has no dangerous attribute
and no other role to switch to, and no denied table is readable. The console uses no connection
pool, so nothing a query leaves behind (a session advisory lock, a setting) outlives it.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.pool import NullPool

from backend.core.errors import ValidationError
from backend.db import engine as eng
from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import secret_config

logger = logging.getLogger(__name__)

_SECRET_NAMESPACE = "sql_console"
_ADVISORY_LOCK = 7_301_455_119  # serializes provisioning across processes sharing one database
_LOCK_TIMEOUT = "15s"
_RETRY_AFTER_S = 30.0

# One round trip, answered by the session about itself. `session_user`, not `current_user`: a
# login that STARTS as a restricted role (`options=-c role=…`, `ALTER ROLE … SET role`) can
# switch back to itself mid-statement, so the login is what must be harmless.
_SESSION_CHECK = """
SELECT session_user = current_user AND current_setting('role') = 'none',
       r.rolsuper, r.rolreplication, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
       ARRAY(SELECT o.rolname FROM pg_roles o WHERE o.rolname <> session_user
             AND pg_has_role(session_user, o.oid, 'MEMBER') ORDER BY 1),
       ARRAY(SELECT c.relname::text FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %(schema)s AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
             AND lower(c.relname) = ANY(%(denied)s)
             AND has_any_column_privilege(session_user, c.oid, 'SELECT') ORDER BY 1)
FROM pg_roles r WHERE r.rolname = session_user
"""
_ATTRIBUTES = ("SUPERUSER", "REPLICATION", "BYPASSRLS", "CREATEROLE", "CREATEDB")


@dataclass(frozen=True)
class ConsoleStatus:
    available: bool
    reason: str = ""
    fix: str = ""  # one command an operator can run when the app cannot fix it alone
    login: str = ""


class _Unavailable(Exception):
    def __init__(self, reason: str, fix: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.fix = fix


_lock = threading.Lock()
_engine: Engine | None = None
_status: ConsoleStatus | None = None
_checked_at = 0.0
_schema = ""
_denied: frozenset[str] = frozenset()


def connect(denied: frozenset[str], override_url: str) -> Connection:
    """A checked connection as the console login. Raises when SQL is unavailable. A failed
    sign-in (another process reset the managed password) re-prepares once."""
    for attempt in (1, 2):
        current = prepare(denied, override_url)
        engine = _engine
        if not current.available or engine is None:
            raise ValidationError(
                f"SQL access is unavailable on this Postgres database: {current.reason}"
            )
        try:
            connection = engine.connect()
        except Exception:  # noqa: BLE001 — a sign-in failure: re-prepare, then give up
            if attempt == 2:
                raise ValidationError("SQL access could not sign in to the database.") from None
            _invalidate()
            continue
        try:
            _check_session(connection.connection.dbapi_connection, _schema, _denied)
        except _Unavailable as exc:
            connection.close()
            _mark_unavailable(exc)
            raise ValidationError(
                f"SQL access is unavailable on this Postgres database: {exc.reason}"
            ) from None
        return connection
    raise AssertionError("unreachable")


def prepare(denied: frozenset[str], override_url: str) -> ConsoleStatus:
    """Sync and check the console login once per process; while unavailable, retry at most
    every ``_RETRY_AFTER_S`` so an operator's fix takes effect without a restart."""
    global _engine, _status, _checked_at, _schema, _denied
    with _lock:
        fresh = _status is not None and (
            _status.available or time.monotonic() - _checked_at < _RETRY_AFTER_S
        )
        if fresh:
            return _status  # type: ignore[return-value]
        if _engine is not None:
            _engine.dispose()
            _engine = None
        try:
            _engine, login, _schema = _prepare(denied, override_url)
            _denied = denied
            _status = ConsoleStatus(True, login=login)
        except _Unavailable as exc:
            logger.warning("sql_console_role: SQL access unavailable: %s", exc.reason)
            _status = ConsoleStatus(False, exc.reason, exc.fix)
        except Exception as exc:  # noqa: BLE001 — anything unexpected fails CLOSED
            logger.exception("sql_console_role: preparing the SQL login failed")
            _status = ConsoleStatus(
                False, f"preparing the SQL login failed ({type(exc).__name__}); see the app log."
            )
        _checked_at = time.monotonic()
        return _status


def last_status() -> ConsoleStatus | None:
    return _status


def reset() -> None:
    """Forget the prepared login (tests; a schema rebuilt underneath a running process)."""
    global _engine, _status, _checked_at
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine, _status, _checked_at = None, None, 0.0


def _invalidate() -> None:
    global _status
    with _lock:
        _status = None


def _mark_unavailable(exc: _Unavailable) -> None:
    global _status, _checked_at
    with _lock:
        _status = ConsoleStatus(False, exc.reason, exc.fix)
        _checked_at = time.monotonic()
    logger.warning("sql_console_role: SQL access switched off: %s", exc.reason)


def _prepare(denied: frozenset[str], override_url: str) -> tuple[Engine, str, str]:
    main = eng.get_engine()
    managed = not override_url
    if managed:
        role = _managed_role_name(main)
    else:
        url = make_url(override_url)
        if not url.username:
            raise _Unavailable("ONW_SQL_CONSOLE_DATABASE_URL names no database login.")
        role = url.username
    with main.connect() as conn:
        app_login = conn.execute(text("SELECT session_user")).scalar_one()
    if role == app_login:
        # Revoking the denied tables from the app's OWN login would lock the app out of its
        # credentials — and that login could read them anyway.
        raise _Unavailable(
            "ONW_SQL_CONSOLE_DATABASE_URL uses the app's own database login; Lucius needs a "
            "separate one. Remove the setting and the app will create it."
        )
    if managed:
        url = main.url.set(username=role, password=_ensure_managed_login(main, role))
    schema = _sync_grants(main, role, denied)
    try:
        console = _connect(url, role, managed=managed)
    except _Unavailable:
        if not managed:
            raise
        # A stored password that no longer matches (a restore, a same-named login): reset once.
        url = main.url.set(username=role, password=_ensure_managed_login(main, role, reset=True))
        console = _connect(url, role, managed=True)
    try:
        with console.connect() as c:
            _check_session(c.connection.dbapi_connection, schema, denied)
    except BaseException:
        console.dispose()
        raise
    return console, role, schema


def _managed_role_name(main: Engine) -> str:
    database = main.url.database or "onw"
    return "onw_sql_" + re.sub(r"[^a-z0-9_]", "_", database.lower())[:40]


def _q(raw: Any, sql: str, *idents: str) -> str:
    """``sql`` with each ``{}`` filled by a quoted identifier, via psycopg's own quoting."""
    from psycopg import sql as pgsql

    return pgsql.SQL(sql).format(*(pgsql.Identifier(i) for i in idents)).as_string(raw)


def _locked(conn: Connection) -> None:
    """Serialize with other processes, but never wait forever on a lock someone else holds."""
    conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK})


def _ensure_managed_login(main: Engine, role: str, *, reset: bool = False) -> str:
    """The managed login's password, creating the login or (re)setting its password."""
    import psycopg
    from psycopg import sql as pgsql

    store = NamespacedSettingsStore(_SECRET_NAMESPACE, table=secret_config, engine=main)
    with main.begin() as conn:
        _locked(conn)
        stored = (store.get(conn=conn) or {}).get("password")
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
        ).first()
        if exists and isinstance(stored, str) and stored and not reset:
            return stored
        password = secrets.token_urlsafe(32)
        raw = conn.connection.dbapi_connection
        verb = "ALTER ROLE {} WITH" if exists else "CREATE ROLE {} WITH"
        statement = pgsql.SQL(verb + " LOGIN NOINHERIT NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            pgsql.Identifier(role), pgsql.Literal(password)
        )
        try:
            with conn.begin_nested():  # a refusal must leave the transaction usable
                conn.exec_driver_sql(statement.as_string(raw))
        except Exception as exc:  # noqa: BLE001 — classified below, re-raised otherwise
            if not isinstance(getattr(exc, "orig", exc), psycopg.errors.InsufficientPrivilege):
                raise
            raise _no_createrole(conn, role) from exc
        store.save({"password": password, "role": role}, conn=conn)
        logger.info("sql_console_role: %s the SQL login %s", "reset" if exists else "created", role)
        return password


def _no_createrole(conn: Connection, role: str) -> _Unavailable:
    app_login = conn.execute(text("SELECT session_user")).scalar_one()
    version = int(conn.execute(text("SHOW server_version_num")).scalar_one())
    reason = (
        f"the app's database login ({app_login}) isn't allowed to create the restricted login "
        "Lucius's SQL runs as."
    )
    if version >= 160000:
        return _Unavailable(reason, fix=f'ALTER ROLE "{app_login}" CREATEROLE;')
    # Before Postgres 16, CREATEROLE can grant itself almost anything — so don't suggest it.
    return _Unavailable(
        reason + " On this Postgres version, have an admin create it instead, then set "
        "ONW_SQL_CONSOLE_DATABASE_URL to it and restart.",
        fix=f"CREATE ROLE \"{role}\" LOGIN NOINHERIT PASSWORD '<choose a password>';",
    )


def _sync_grants(main: Engine, role: str, denied: frozenset[str]) -> str:
    """SELECT on every readable table, nothing on a denied one. One statement per savepoint,
    so a table the app doesn't own fails alone; ``_check_session`` decides whether it matters."""
    with main.begin() as conn:
        _locked(conn)
        schema = conn.execute(text("SELECT current_schema()")).scalar_one()
        raw = conn.connection.dbapi_connection
        statements = [_q(raw, "GRANT USAGE ON SCHEMA {} TO {}", schema, role)]
        for table in inspect(conn).get_table_names():
            if table.lower() in denied:
                statements.append(_q(raw, "REVOKE ALL ON TABLE {}.{} FROM {}", schema, table, role))
            else:
                statements.append(_q(raw, "GRANT SELECT ON TABLE {}.{} TO {}", schema, table, role))
        for statement in statements:
            try:
                with conn.begin_nested():
                    conn.exec_driver_sql(statement)
            except Exception as exc:  # noqa: BLE001 — one table must not sink the rest
                logger.warning("sql_console_role: %s failed: %s", statement.split(" ON ")[0], exc)
    return schema


def _connect(url: URL, role: str, *, managed: bool) -> Engine:
    # NullPool: each query gets a fresh session, so a session-level advisory lock or setting a
    # query takes is released when it ends instead of riding a pooled connection.
    console = create_engine(
        url.render_as_string(hide_password=False), poolclass=NullPool, hide_parameters=True
    )
    try:
        with console.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — every sign-in failure means "unavailable"
        console.dispose()
        hint = (
            "Check that the server accepts password sign-ins for it (pg_hba.conf), or set "
            "ONW_SQL_CONSOLE_DATABASE_URL to a login you made."
            if managed
            else "Check ONW_SQL_CONSOLE_DATABASE_URL."
        )
        logger.warning("sql_console_role: sign-in as %s failed: %s", role, type(exc).__name__)
        raise _Unavailable(f"the database refused a sign-in as {role}. {hint}") from exc
    return console


def _check_session(raw: Any, schema: str, denied: frozenset[str]) -> None:
    """``_SESSION_CHECK`` on this exact session; raises ``_Unavailable`` naming what's wrong.
    Leaves no transaction open, so the caller can still set it read-only."""
    try:
        with raw.cursor() as cur:
            cur.execute(_SESSION_CHECK, {"schema": schema, "denied": sorted(denied)})
            row = cur.fetchone()
    finally:
        raw.rollback()
    if row is None:
        raise _Unavailable("Lucius's SQL login could not be found in pg_roles.")
    signed_in_as_itself, *attributes, other_roles, readable = row
    if not signed_in_as_itself:
        raise _Unavailable(
            "Lucius's SQL login starts as a different role than it signs in as, so it could "
            "switch back to the login mid-query."
        )
    held = [name for name, on in zip(_ATTRIBUTES, attributes) if on]
    if held:
        raise _Unavailable(f"Lucius's SQL login has {', '.join(held)}, which it must not.")
    if other_roles:
        raise _Unavailable(
            "Lucius's SQL login is a member of other roles "
            f"({', '.join(other_roles)}), so it could switch to one that reads credentials."
        )
    if readable:
        raise _Unavailable(
            f"Lucius's SQL login can still read {', '.join(readable)}, and the app could not "
            "remove that access."
        )
