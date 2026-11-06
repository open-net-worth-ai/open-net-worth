"""Where SQL a server chose may run.

A chosen server is trusted with a budget, not with the book. The migrations it asks for by name, it
also supplies, so they run on a connection that can reach this one file and nothing else: no other
database can be attached, which also stops the ``VACUUM`` forms that attach one to write to, and no
``PRAGMA`` runs. No genuine migration from any Actual version we hold needs either, bar
``remove_cache``, which ``compat`` refuses by name anyway. A future one that does is refused in
plain words until it is reviewed. Imports nothing from ``actual``: what it needs of it is passed in.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

from backend.core.errors import ExternalServiceError
from backend.services.actual_sync import compat

logger = logging.getLogger(__name__)

_REFUSED = (
    "This Actual server sent a change to the budget that this app couldn't run safely, so the "
    f"budget wasn't read. {compat.REFRESH}"
)
_DENIED = frozenset({sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA})
MIGRATION_SECONDS = 600.0
READ_SECONDS = 300.0  # one read of a whole budget: 1.5s for 5,000 rows
_STEPS = 10_000  # how many SQLite steps between looks at the clock


def _authorize(action: int, *_details) -> int:
    return sqlite3.SQLITE_DENY if action in _DENIED else sqlite3.SQLITE_OK


def limit(conn: sqlite3.Connection, seconds: float) -> None:
    """``conn`` gives up on any statement after ``seconds`` from now, and doesn't lean on what the
    file's own schema names. A view or a migration can hold a query that never ends, and a run's
    deadline is only checked between batches."""
    conn.execute("PRAGMA trusted_schema=OFF")
    stop_at = time.monotonic() + seconds
    conn.set_progress_handler(lambda: time.monotonic() > stop_at, _STEPS)


def confine(conn: sqlite3.Connection) -> None:
    """``conn`` stays on its one file from here on, whatever it is asked to run."""
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    conn.set_authorizer(_authorize)


def _script(file: str, body: bytes, js_statements: Callable[[str], list[str]]) -> str:
    text = body.decode()
    return "\n".join(js_statements(text)) if file.endswith(".js") else text


def _apply(
    conn: sqlite3.Connection,
    files: list[str],
    fetch: Callable[[str], bytes],
    js_statements: Callable[[str], list[str]],
) -> None:
    """The library's loop: what the budget lacks, fetched and run, each recorded as done. The time
    limit covers the fetching, or a server could spend it one file at a time."""
    stop_at = time.monotonic() + MIGRATION_SECONDS
    for file in files:
        if not file.startswith("migrations"):
            continue
        number = compat.migration_number(file)
        if conn.execute("SELECT id FROM __migrations__ WHERE id = ?", (number,)).fetchone():
            continue
        body = fetch(file)
        if time.monotonic() > stop_at:
            raise ExternalServiceError(_REFUSED)
        conn.executescript(_script(file, body, js_statements))
        conn.execute("INSERT INTO __migrations__ (id) VALUES (?)", (number,))
    conn.commit()


def run_migrations(
    db: Path,
    files: list[str],
    fetch: Callable[[str], bytes],
    js_statements: Callable[[str], list[str]],
) -> None:
    """Check what the budget at ``db`` still needs, then run it, confined. ``fetch`` gets a file
    from the server. A refusal is one fixed sentence, raised outside the ``except``: what SQLite
    says can quote the statement or a path. What it leaves half done dies with the copy."""
    failure = None
    with closing(sqlite3.connect(db)) as conn:
        limit(conn, MIGRATION_SECONDS)
        confine(conn)
        compat.check_pending(compat.pending_names(conn, files))
        try:
            _apply(conn, files, fetch, js_statements)
        except (sqlite3.Error, ValueError, OverflowError) as exc:
            logger.warning("Actual migration refused (%s)", type(exc).__name__)
            failure = ExternalServiceError(_REFUSED)
    if failure is not None:
        raise failure
