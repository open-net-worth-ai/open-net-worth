"""The read-only SQL console — the ONE place in this codebase that executes caller-supplied SQL.

Spec: ``docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md``.
Policy (kill switch, caveats, audit logging) lives one layer up in ``services/sql_query.py``;
this module is pure execution and pure enforcement.

## Why the boundary is the CONNECTION, not the SQL string

A SQL-string parser is a blocklist, and blocklists lose. They lose to CTEs, to scalar
subqueries, to views, to ``UNION``, to aliases, to comments spliced mid-keyword, to unicode
confusables, and to whatever is published next month. Any design whose safety rests on
"we checked the text for the word DELETE" is one clever string away from being wrong.

So the text check below (``assert_statement_is_read_only``) is deliberately NOT the security
boundary. It exists so the common mistake produces a clear, actionable error instead of a
cryptic authorizer denial. The actual boundary is enforced beneath the parser, by the database.

### SQLite (the shipped default) — ``set_authorizer``

SQLite invokes the authorizer callback during statement PREPARATION, after parsing, once per
action and once per table/column access. It therefore sees the RESOLVED access, not the text:
a secret table reached through a three-level CTE inside a ``UNION`` arm of a view still arrives
here as a plain ``SQLITE_READ`` on that table's name. That is the property that makes this
robust rather than merely careful.

``_authorizer`` is an ALLOWLIST of actions (``_ALLOWED_ACTIONS``), not a denylist of dangerous
ones — an action code this module has never heard of is refused, so a future SQLite release
adding a new capability fails closed instead of falling through. ``PRAGMA query_only=ON`` is
set as a second, independent control before the authorizer is installed (it must be set first:
the authorizer denies ``SQLITE_PRAGMA``).

**Why not ``mode=ro``?** A third control was considered and rejected on operational grounds:
opening the URI read-only cannot create or recover the ``-shm`` sidecar a WAL database needs,
so on a cold instance it fails with a bare "unable to open database file" that looks like data
corruption to the operator. Two independent controls that always work beat three where the
third turns a working feature into a confusing outage.

### Postgres — a dedicated REVOKE-d role, or no console

Postgres has no authorizer, and a textual scan for table names is NOT a boundary there:
``query_to_xml('select * from secret_config', ...)`` names the table inside a string,
``U&"\\0073ecret_config"`` spells it with escapes, and ``E'..'`` / ``$$..$$`` quoting hides
text from a naive scrubber. So the console is DISABLED on Postgres unless
``ONW_SQL_CONSOLE_DATABASE_URL`` names a connection whose ROLE has been REVOKE-d on every
``DENIED_TABLES`` entry (setup: ``docs/product/operations/sql-console-postgres.md``). The
database then enforces the table boundary exactly as the SQLite authorizer does. Around it
sit the genuine read-only transaction, ``statement_timeout``, an unconditional rollback, the
(over-refusing) identifier scan as a clearer-error layer, and the EXTENDED protocol only
(a prepared statement), which makes a second statement in the same call a server error rather
than something the simple protocol would run.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from decimal import Decimal
from typing import Any, Callable

from backend.core.config import get_settings
from backend.core.errors import ValidationError
from backend.core.sensitive import SECRET_TABLES
from backend.db import engine as eng

logger = logging.getLogger(__name__)

# ── Bounds ───────────────────────────────────────────────────────────────────────────────
DEFAULT_ROWS = 200
MAX_ROWS = 1000
# Chosen from the real data, not from taste. Measured against a 401 MB live instance:
# across the core money/planning tables (transactions, accounts, positions, categories,
# payees, envelopes, lots) the p99.9 cell is 984 chars and the LARGEST is 1,401 — so 2,000
# clips 0.00% of what a real question actually reads, where 500 clipped 3.89% for nothing.
#
# A cap is still non-negotiable: the widest cell on that same instance is a single
# `statements_meta.payload` of 1,063,534 characters (~265K tokens in ONE value), and
# `SELECT payload FROM transactions LIMIT 200` — comfortably inside the ROW cap — is ~28,600
# tokens uncapped. Row-capping alone does not bound the result; both caps are load-bearing.
MAX_CELL_CHARS = 2000
QUERY_TIMEOUT_S = 10.0
_PROGRESS_INSTRUCTIONS = 10_000  # how often SQLite checks the deadline
_SAMPLE_ROWS_FOR_PAYLOAD_KEYS = 25
_UNBOUNDED_CELL = 10**9  # see _cell(max_chars=…) — sampling, never shown to a model

# ── What may not be read ─────────────────────────────────────────────────────────────────
# SECRET_TABLES is REUSED, never re-listed: it is already this codebase's canonical answer to
# "may this table's rows leave the instance?" (password hash, session-signing HMAC, API bearer
# token, every LLM/bank/broker credential, every external-MCP token). A tool result IS those
# rows leaving the instance, into a model context that may be logged, shared, or steered by
# untrusted text.
#
# `connectors` is not a secret table either (it is kept in a sanitised backup), but it holds raw
# SERVER-CHOSEN text — an MCP server's discovered tool descriptions, instructions and publisher
# URLs, which are the prompt-injection surface `get_connector_status` deliberately never relays —
# and personal configuration (the email address, Telegram chat ids, usernames). The purpose-built
# tools (`get_connector_status`, `get_connector_tools`) are the model's way in.
_EXTRA_DENIED_TABLES = frozenset({"connectors"})
DENIED_TABLES: frozenset[str] = SECRET_TABLES | _EXTRA_DENIED_TABLES

# A public demo's visitors share one database, and a visitor's own Lucius would otherwise read
# every other visitor's conversation here. These hold transcripts (`sessions` too: its titles
# and fold summaries are conversation content), staged tool arguments and the queries and tool
# calls other visitors made; they are unreadable on a demo (and only there). On a normal
# instance every conversation is the operator's own, so both transcript tables are readable.
_DEMO_EXTRA_DENIED_TABLES = frozenset(
    {
        "sessions",
        "session_messages",
        "pending_plans",
        "tool_call_log",
        "tool_discovery_log",
        "sql_query_log",
        "artifacts",
        "checkpoints",
        "run_history",
        "cost_log",
        "cost_log_daily",
        "model_telemetry",
    }
)

# ── The statement gate (clear errors, NOT the security boundary — see module docstring) ──
_WRITE_KEYWORDS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "replace",
        "truncate",
        "attach",
        "detach",
        "pragma",
        "vacuum",
        "grant",
        "revoke",
        "begin",
        "commit",
        "rollback",
        "reindex",
        "analyze",
        "savepoint",
        "release",
        "merge",
        "upsert",
    }
)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


_DOLLAR_TAG_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_UNICODE_ESCAPE_RE = re.compile(r"\bu&\s*[\"']", re.IGNORECASE)


def _skip_string(sql: str, i: int, escapes: bool) -> int:
    """Index just past the single-quoted string whose opening quote is at ``i``. ``escapes``
    is True for Postgres ``E'..'`` strings, where a backslash escapes the next character."""
    n, j = len(sql), i + 1
    while j < n:
        if escapes and sql[j] == "\\":
            j += 2
        elif sql[j] == "'":
            return j + 1
        else:
            j += 1
    return n


def _skip_comment(sql: str, i: int) -> int:
    """Index just past the ``/* */`` comment at ``i``; Postgres comments NEST."""
    n, depth, j = len(sql), 1, i + 2
    while j < n and depth:
        if sql.startswith("/*", j):
            depth, j = depth + 1, j + 2
        elif sql.startswith("*/", j):
            depth, j = depth - 1, j + 2
        else:
            j += 1
    return j


def _scrub(sql: str, keep_literals: bool = False) -> str:
    """``sql`` with STRING LITERALS and COMMENTS blanked, but QUOTED IDENTIFIERS kept.

    String forms understood: ``'..'`` (with ``''``), Postgres ``E'..'`` (backslash escapes)
    and ``$tag$..$tag$`` dollar quoting. ``keep_literals`` keeps string CONTENTS visible
    (comments are still blanked) for the Postgres table scan, where a denied name inside a
    string can still be executed (``query_to_xml``).

    The distinction matters and getting it wrong is a real bypass, not a nitpick:

    * A single-quoted ``'DROP TABLE'`` is DATA — a payee name, a memo, a LIKE pattern. Scanning
      it for keywords produces false refusals, so it is blanked.
    * A double-quoted (or backticked) ``"secret_config"`` is an IDENTIFIER — the standard,
      portable way to name a table. Blanking it would hide the table name from
      ``assert_tables_allowed``, which on Postgres IS the table boundary. So the quotes are
      stripped and the CONTENTS are kept, where both scans can see them.

    The cost of keeping identifier contents is that ``SELECT "insert"`` now trips the
    write-keyword scan. That is over-refusal, which is the safe direction.

    Blanking replaces characters with spaces rather than deleting them, so offsets — and
    therefore token boundaries — survive.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            escapes = i > 0 and sql[i - 1] in "eE" and (i < 2 or not _is_ident_char(sql[i - 2]))
            j = _skip_string(sql, i, escapes)
            out.append(" " + sql[i + 1 : j - 1] + " " if keep_literals else " " * (j - i))
            i = j
        elif ch == "$" and (tag := _DOLLAR_TAG_RE.match(sql, i)):
            close = sql.find(tag.group(0), tag.end())
            j = n if close == -1 else close + len(tag.group(0))
            body = sql[tag.end() : close if close != -1 else n]
            out.append(" " + body + " " if keep_literals else " " * (j - i))
            i = j
        elif ch in '"`':
            j = i + 1
            while j < n and sql[j] != ch:
                j += 1
            out.append(" " + sql[i + 1 : j] + " ")  # quotes dropped, identifier preserved
            i = j + 1
        elif ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif ch == "/" and sql.startswith("/*", i):
            j = _skip_comment(sql, i)
            out.append(" " * (j - i))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _split_statements(scrubbed: str) -> list[str]:
    return [part for part in scrubbed.split(";") if part.strip()]


def assert_statement_is_read_only(sql: str) -> str:
    """Return the single normalized statement, or raise ``ValidationError``.

    A pre-flight for GOOD ERROR MESSAGES. The database enforces the real boundary; see the
    module docstring for why this check is not trusted to.
    """
    raw = (sql or "").strip()
    if not raw:
        raise ValidationError("No SQL supplied. Pass a single SELECT statement.")
    scrubbed = _scrub(raw)
    if len(_split_statements(scrubbed)) > 1:
        raise ValidationError(
            "Only ONE statement per call. Split this into separate run_sql_query calls."
        )
    lowered = scrubbed.strip().lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValidationError(
            "Only SELECT (or WITH … SELECT) is allowed — this tool is read-only. "
            "To change data, use the normal write tools so the ledger's invariants still apply."
        )
    tokens = {t.lower() for t in _IDENT_RE.findall(scrubbed)}
    banned = sorted(tokens & _WRITE_KEYWORDS)
    if banned:
        raise ValidationError(
            f"Refused: the statement contains {', '.join(banned)} — this tool is "
            "read-only and executes SELECT only."
        )
    return raw.rstrip(";").strip()


def assert_tables_allowed(sql: str) -> None:
    """Fail-closed identifier scan for denied table names.

    On SQLite this is redundant (the authorizer is authoritative) and kept as a second,
    independent control. On Postgres, where no authorizer exists, it IS the table boundary —
    so it deliberately over-refuses: a denied name appearing anywhere as a bare token, even in
    a context where it could not be a table reference, rejects the whole query.
    """
    if _UNICODE_ESCAPE_RE.search(sql):
        raise ValidationError(
            'Refused: unicode-escaped names (U&"..") are not accepted by this tool.'
        )
    tokens = {t.lower() for t in _IDENT_RE.findall(_scrub(sql, keep_literals=True))}
    hit = sorted(tokens & denied_tables())
    if hit:
        raise ValidationError(
            f"Refused: {', '.join(hit)} is not readable through this tool. "
            "Credential tables are never exposed, and chat transcripts have their own tools."
        )


# ── SQLite: the authorizer ───────────────────────────────────────────────────────────────
# ALLOWLIST, not a denylist: an action code this module has never heard of is refused, so a
# future SQLite capability fails closed rather than falling through.
_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,  # the SELECT itself (no arguments)
        sqlite3.SQLITE_READ,  # one per (table, column) touched — the table gate lives here
        sqlite3.SQLITE_FUNCTION,  # scalar/aggregate calls; load_extension refused below
        sqlite3.SQLITE_RECURSIVE,  # recursive CTEs
    }
)
_DENIED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})
_DENIED_TABLES_LOWER = frozenset(t.lower() for t in DENIED_TABLES)


def denied_tables() -> frozenset[str]:
    """Every table (lower-case) that may not be read RIGHT NOW — wider on a public demo."""
    if get_settings().demo_mode:
        return _DENIED_TABLES_LOWER | _DEMO_EXTRA_DENIED_TABLES
    return _DENIED_TABLES_LOWER


def _authorizer(action: int, arg1: Any, arg2: Any, _db: Any, _trigger: Any) -> int:
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and isinstance(arg1, str):
        if arg1.lower() in denied_tables():
            return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and isinstance(arg2, str):
        if arg2.lower() in _DENIED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _deadline_handler(deadline: float) -> Callable[[], int]:
    def _tick() -> int:
        return 1 if time.monotonic() > deadline else 0

    return _tick


def _sqlite_path() -> str:
    url = eng.database_url()
    path = eng.resolved_sqlite_path(url)
    if path is None:
        raise ValidationError("SQL access is not available on this database configuration.")
    return str(path)


def _sqlite_connection() -> sqlite3.Connection:
    """A locked-down connection. Order is load-bearing: the pragmas must be set BEFORE the
    authorizer is installed, because the authorizer denies ``SQLITE_PRAGMA``."""
    conn = sqlite3.connect(_sqlite_path(), timeout=5.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(
        _deadline_handler(time.monotonic() + QUERY_TIMEOUT_S), _PROGRESS_INSTRUCTIONS
    )
    return conn


# ── Value serialization ──────────────────────────────────────────────────────────────────


class _Clipper:
    """Bounds each cell AND counts how many it had to bound.

    The count is the point. ``ai/tools/_bounded.py`` states the invariant this enforces — *a
    bounded read must never look like a complete one* — and an ellipsis at the end of a string
    does not satisfy it: a model reading a clipped ``payload`` sees plausible JSON, never
    notices the tail is missing, and concludes the field it wanted does not exist. The ROW cap
    has always reported itself honestly (``showing``/``truncated``/``total``); this makes the
    CELL cap equally honest.
    """

    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        self.count = 0

    def clip(self, value: Any) -> Any:
        out = _cell(value, self.max_chars)
        if isinstance(out, str) and out.endswith("…") and out is not value:
            self.count += 1
        return out


def _cell(value: Any, max_chars: int = MAX_CELL_CHARS) -> Any:
    """JSON-safe, bounded. Money columns are Text-typed decimal strings in this schema, so
    they arrive as ``str`` and pass through exactly — no float coercion anywhere.

    ``max_chars`` exists because the cap is about bounding MODEL CONTEXT, not about safety:
    ``_payload_keys`` below reads payloads it never shows to anyone (only the derived key
    NAMES are returned), and truncating those mid-JSON would silently break the parse — which
    it did, until this parameter existed.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > max_chars:
            return value[:max_chars] + "…"
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    # A Postgres JSON column arrives already parsed; SQLite hands the same document back as
    # text. Re-serialise so both read as JSON, never as a Python repr.
    out = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
    return out[:max_chars] + "…" if len(out) > max_chars else out


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_ROWS
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_ROWS
    return max(1, min(value, MAX_ROWS))


def dialect() -> str:
    return "sqlite" if eng.database_url().startswith("sqlite") else "postgresql"


# ── Execution ────────────────────────────────────────────────────────────────────────────


def execute_readonly(sql: str, limit: int | None = None) -> dict:
    """Run ONE read-only statement and return bounded, JSON-safe rows.

    Raises ``ValidationError`` for anything refused — by the text gate, by the identifier
    scan, or by the database itself.
    """
    statement = assert_statement_is_read_only(sql)
    assert_tables_allowed(statement)
    cap = _clamp_limit(limit)
    started = time.perf_counter()
    columns, rows, truncated, clipped = _fetch(statement, cap, MAX_CELL_CHARS)
    out = {
        "columns": columns,
        "rows": rows,
        "showing": len(rows),
        "truncated": truncated,
        # A STRING, deliberately. We cannot know the true count without a second full scan,
        # and inventing a number is exactly the failure ai/tools/_bounded.py exists to
        # prevent: a bounded read must never look like a complete one.
        "total": (
            "unknown — re-run with COUNT(*) for the true total" if truncated else str(len(rows))
        ),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "dialect": dialect(),
    }
    if clipped:
        # Reported, never silent — see _Clipper. Long values are USUALLY a `payload` blob, so
        # the recovery path names the tool that shows what is inside one.
        out["cells_truncated"] = clipped
        out["cells_truncated_note"] = (
            f"{clipped} value(s) were longer than {MAX_CELL_CHARS} characters and end in '…' — "
            "those cells are INCOMPLETE. To see one in full, select the specific field instead "
            "of the whole column (json_extract(payload, '$.field')) for a single row, or use "
            "substr() to page through it."
        )
    return out


def _fetch(statement: str, cap: int, max_cell: int) -> tuple[list[str], list[list], bool, int]:
    """Dialect dispatch. ``statement`` MUST already have passed both gates.

    Returns ``(columns, rows, row_cap_bit, clipped_cell_count)``.
    """
    if dialect() == "sqlite":
        return _run_sqlite(statement, cap, max_cell)
    return _run_postgres(statement, cap, max_cell)


def _run_sqlite(statement: str, cap: int, max_cell: int) -> tuple[list[str], list[list], bool, int]:
    conn = _sqlite_connection()
    try:
        cursor = conn.execute(statement)
        fetched = cursor.fetchmany(cap + 1)
        columns = [d[0] for d in (cursor.description or [])]
    except sqlite3.DatabaseError as exc:
        raise _translate_sqlite_error(exc) from exc
    finally:
        conn.set_authorizer(None)
        conn.close()
    clipper = _Clipper(max_cell)
    rows = [[clipper.clip(v) for v in row] for row in fetched[:cap]]
    return columns, rows, len(fetched) > cap, clipper.count


def _translate_sqlite_error(exc: sqlite3.DatabaseError) -> ValidationError:
    """Turn SQLite's terse refusals into something the model can act on."""
    message = str(exc)
    if "not authorized" in message.lower():
        logger.info("sql_console: authorizer refused a statement")
        return ValidationError(
            "Refused by the database: this tool may only READ, and may not read credential "
            "tables or chat transcripts. Rewrite the query against the data tables."
        )
    if "interrupted" in message.lower():
        return ValidationError(
            f"Query exceeded the {QUERY_TIMEOUT_S:.0f}s limit. Narrow it with a WHERE clause, "
            "or aggregate instead of scanning."
        )
    return ValidationError(f"SQL error: {message}")


CONSOLE_URL_ENV = "ONW_SQL_CONSOLE_DATABASE_URL"
_console_engine = None


def console_database_url() -> str:
    """The dedicated read-only role's URL, or ``""`` when none is configured."""
    return (os.environ.get(CONSOLE_URL_ENV) or "").strip()


def _get_console_engine():
    global _console_engine
    url = console_database_url()
    if not url:
        raise ValidationError(
            "SQL access is disabled on Postgres: it needs a dedicated read-only database role. "
            f"Set {CONSOLE_URL_ENV} to a connection whose role is REVOKE-d on the credential "
            "tables (see the SQL console Postgres setup guide)."
        )
    if _console_engine is None:
        _console_engine = eng._make_engine(url)
    return _console_engine


def _run_postgres(
    statement: str, cap: int, max_cell: int
) -> tuple[list[str], list[list], bool, int]:
    """Runs on the console role's own engine, as ONE prepared statement (extended protocol —
    the simple protocol would run ``select 1; delete ...`` as two). The role, not the text
    gate, is the table boundary."""
    import psycopg

    connection = _get_console_engine().connect()
    raw = connection.connection.dbapi_connection
    try:
        raw.read_only = True
        with raw.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{int(QUERY_TIMEOUT_S * 1000)}ms'")
            cur.execute(statement, prepare=True)
            columns = [d.name for d in (cur.description or [])]
            fetched = cur.fetchmany(cap + 1)
    except psycopg.Error as exc:
        raise ValidationError(f"SQL error: {exc}") from exc
    finally:
        # The DBAPI's own rollback: the transaction was opened on the raw cursor, which
        # SQLAlchemy never saw, so `connection.rollback()` was a no-op — and psycopg refuses
        # to reset `read_only` while the connection is still in one.
        raw.rollback()
        raw.read_only = None
        connection.close()
    clipper = _Clipper(max_cell)
    rows = [[clipper.clip(v) for v in row] for row in fetched[:cap]]
    return columns, rows, len(fetched) > cap, clipper.count
