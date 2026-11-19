"""One process-wide SQLAlchemy engine + the unit-of-work seam.

SQLite is the shipped default (WAL, foreign keys ON); Postgres is selected by
setting ONW_DATABASE_URL (e.g. postgresql+psycopg://user:pw@host/db). Repositories
accept an optional Connection so callers can compose several writes into ONE
transaction via unit_of_work() — the multi-store-atomicity seam the JSON stores
never had. locked_unit_of_work() extends this to SEVERAL stores' tables at once,
adding the per-table lock ordering that makes composing them deadlock-free (Plan 3
task 1 — the money-core choke-point seam). Spec:
docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Iterator, Optional

from sqlalchemy import Connection, Engine, Table, create_engine, event
from sqlalchemy.engine import make_url

from backend.core import req_timing
from backend.core.paths import DB_FILE

_log = logging.getLogger(__name__)

_lock = threading.Lock()
_engine: Optional[Engine] = None


# ── Per-table write-lock registry ────────────────────────────────────────────
# Mirrors json_store._lock_for's registry shape, collapsed onto TABLE NAME instead
# of file path (backend.db.payload_store.PayloadTableStore's CONCURRENCY docstring
# explains why the unit is the table). Lives HERE, not in payload_store.py where the
# pattern originated through Plan 2: payload_store.py already imports get_engine()
# from this module (PayloadTableStore._eng()), and locked_unit_of_work() below needs
# this SAME registry to lock several stores' tables in one call — if the registry
# stayed in payload_store.py, this module would have to import IT, and
# payload_store already imports engine.py, which is an import cycle. Hosting the
# registry here instead (payload_store now imports _lock_for_table FROM engine)
# keeps the dependency one-directional: engine.py stays a leaf other db/ modules
# import, never the reverse. (Plan 3 task 1.)
_registry_lock = threading.Lock()
_table_locks: dict[str, threading.Lock] = {}


def _lock_for_table(name: str) -> threading.Lock:
    with _registry_lock:
        if name not in _table_locks:
            _table_locks[name] = threading.Lock()
        return _table_locks[name]


def database_url() -> str:
    return os.environ.get("ONW_DATABASE_URL") or f"sqlite:///{DB_FILE}"


def _attach_request_timing(eng: Engine) -> None:
    """Record every executed statement's driver time + rowcount into the active request's
    per-phase stats (see core.req_timing + main.py's timing middleware). No-op outside a
    request scope, so scheduler/CLI/test queries cost nothing here."""

    @event.listens_for(eng, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        context._onw_query_t0 = time.perf_counter()

    @event.listens_for(eng, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        t0 = getattr(context, "_onw_query_t0", None)
        if t0 is None:
            return
        elapsed_ms = (time.perf_counter() - t0) * 1000
        rc = getattr(cursor, "rowcount", -1)
        req_timing.record_db(elapsed_ms, rc if isinstance(rc, int) and rc > 0 else 0)


_SQLITE_MAX_OVERFLOW = 45


def _make_engine(url: str) -> Engine:
    # hide_parameters=True (FR-M5) — keep bound SQL VALUES out of every DBAPIError's str():
    # an IntegrityError reaches both the LLM context (ai/assistant.py's str(exc)) and the boot
    # log (migrate.py's {exc}), and the "[parameters: (...)]" tail would otherwise leak row
    # data there. The URL channel stays covered separately by migrate._redact_url.
    if url.startswith("sqlite"):
        # max_overflow: ~50 worker threads (FastAPI's 40 + asyncio's executor) share this engine.
        # The default 5+10 ran dry under load and the event loop's own small reads queued in
        # the pool's checkout for seconds (loop_watch BLOCKED, 2026-11-19). WAL lets readers run
        # concurrently and SQLite still serialises writers itself, so more connections cost only
        # file handles — and overflow ones are closed as soon as they are returned.
        eng = create_engine(
            url,
            connect_args={"check_same_thread": False},
            hide_parameters=True,
            pool_size=5,
            max_overflow=_SQLITE_MAX_OVERFLOW,
        )
        sqlite_path = resolved_sqlite_path(url)

        @event.listens_for(eng, "connect")
        def _set_pragmas(dbapi_conn, _record):  # pragma: no cover - exercised via tests
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
            _restrict_sqlite_modes(sqlite_path)

        _attach_request_timing(eng)
        return eng
    eng = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    _attach_request_timing(eng)
    return eng


def resolved_sqlite_path(url: str) -> Path | None:
    """The filesystem path behind a sqlite URL, or None for non-sqlite/:memory:."""
    if not url.startswith("sqlite"):
        return None
    database = make_url(url).database
    if not database or database == ":memory:":
        return None
    return Path(database)


def _restrict_sqlite_modes(db_path: Path | None) -> None:
    """chmod the sqlite database file — plus its ``-wal``/``-shm`` sidecars when present — to
    0o600 (owner-only), the same at-rest posture ``auth.json`` already has (FR-I3): financial
    data must not sit world-readable at 0644. No-op for non-sqlite / ``:memory:`` (``db_path``
    is None) so Postgres is skipped silently. Called from the sqlite ``connect`` event, so it
    is idempotent per connect and best-effort — a chmod failure (e.g. a file this process does
    not own) is logged at debug and swallowed, never allowed to break a DB connection."""
    if db_path is None:
        return
    for suffix in ("", "-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        try:
            if sidecar.exists():
                os.chmod(sidecar, 0o600)
        except OSError:
            _log.debug("could not chmod sqlite file %s to 0600", sidecar, exc_info=True)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                url = database_url()
                # Create the RESOLVED sqlite file's parent dir — not DB_FILE's —
                # so an ONW_DATABASE_URL override into a fresh directory works
                # (create_engine connects lazily; a missing dir would otherwise
                # surface later as "unable to open database file" on first use).
                db_path = resolved_sqlite_path(url)
                if db_path is not None:
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                _engine = _make_engine(url)
    return _engine


def dispose_engine() -> None:
    """Drop the singleton (tests; the migrate wrapper's restore path)."""
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
    # Drop the read cache's probe connection + memoised entries: they belong to the engine we
    # just disposed, and a fresh engine (e.g. test isolation) must not inherit another database's
    # data_version epoch. Deferred import — read_cache imports this module.
    from backend.db import read_cache

    read_cache.reset()


# ── After-commit callback seam ───────────────────────────────────────────────
# A composed choke (a trade — vest/dispose/option — threading a caller-supplied conn=
# into e.g. ledger.create_transaction) runs INSIDE the outer unit_of_work()'s still-open
# transaction. A side effect that itself needs to open a SECOND write transaction (e.g.
# publishing a DB-backed automation event, storage plan 4c) must not fire from in there —
# SQLite allows only one open writer at a time, so a second begin() on the same engine
# blocks behind the first until PRAGMA busy_timeout trips, raising "database is locked"
# (empirically reproduced: tests/services/test_trade_choke_atomicity.py::
# test_vest_apply_does_not_self_deadlock_on_lot_mint). on_commit() lets such a side effect
# register itself instead of running immediately; unit_of_work() fires every registered
# callback exactly once, synchronously, right after ITS OWN transaction actually commits —
# and not at all if it rolls back, closing the sibling phantom-event-on-rollback gap for
# the same composed path (a rollback must publish nothing).
#
# Never nested in practice: a conn=-composed callee reuses the caller's connection instead
# of opening its own unit_of_work(), so exactly one of these is ever active per thread at a
# time (mirrors the non-reentrant per-table locks locked_unit_of_work relies on below) —
# but the set/reset pair is still written to nest correctly if that ever changes.
_pending_commit_callbacks: ContextVar[Optional[list]] = ContextVar(
    "_pending_commit_callbacks", default=None
)


def on_commit(callback: Callable[[], None]) -> None:
    """Register ``callback`` to run once, after the CURRENTLY OPEN unit_of_work() /
    locked_unit_of_work() transaction commits — never if it rolls back. See the module
    note above for why this exists. Falls back to firing immediately (logged) if called
    with no transaction open — defensive only; every real caller registers from inside a
    conn= composed under locked_unit_of_work(), so this branch is not expected to be hit."""
    pending = _pending_commit_callbacks.get()
    if pending is None:
        _log.warning("on_commit() called with no active unit_of_work — firing immediately")
        _run_commit_callback(callback)
        return
    pending.append(callback)


def _run_commit_callback(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        # A committed write must never look like it failed because a post-commit side
        # effect (e.g. best-effort event publication) blew up — log and move on, and let
        # one bad callback never stop the others queued behind it.
        _log.exception("post-commit callback failed")


@contextmanager
def unit_of_work() -> Iterator[Connection]:
    """One transaction: commits on clean exit, rolls back on exception. Callbacks
    registered via on_commit() during the block fire, in order, immediately after a
    successful commit — an exception (rollback) discards them unfired."""
    token = _pending_commit_callbacks.set([])
    try:
        with get_engine().begin() as conn:
            yield conn
        # Reached ONLY on a clean exit: get_engine().begin()'s own __exit__ already ran
        # (and committed) by this point. An exception raised by the caller's body instead
        # propagates straight out through that __exit__'s rollback-and-reraise, skipping
        # this line entirely — so a rolled-back transaction fires no callback at all.
        for callback in _pending_commit_callbacks.get() or []:
            _run_commit_callback(callback)
    finally:
        _pending_commit_callbacks.reset(token)


@contextmanager
def locked_unit_of_work(*tables: Table) -> Iterator[Connection]:
    """One transaction spanning several stores' tables, with their per-table locks
    held in GLOBAL ORDER (alphabetical by table name) — the deadlock-freedom rule
    for every multi-store choke. Acquires locks, then opens unit_of_work().

    Every multi-table choke MUST compose through here — never take more than one
    PayloadTableStore table's lock by any other path — because alphabetical order
    is only deadlock-free if EVERY caller sorts the same way; one caller acquiring
    (accounts, transactions) while another acquires (transactions, accounts)
    directly is the textbook lock-ordering deadlock this function exists to close.
    Duplicate tables collapse to one acquisition (threading.Lock is not reentrant —
    locking the same table twice on one thread would deadlock against itself, not
    against another thread).

    A PayloadTableStore mutate composed into this transaction MUST be called with
    conn=<this transaction's connection> — that is what tells the mutate to skip
    its OWN per-table lock/begin() (see PayloadTableStore's conn= contract), since
    the lock for that table is already held here for the whole transaction. A
    plain (conn=None) mutate on one of these SAME tables from another thread while
    this transaction is open still blocks correctly (it takes the real lock); it is
    only a second locked_unit_of_work / conn= caller racing the lock ORDER itself
    that this function's "always compose through here" contract protects against.
    """
    names = sorted({t.name for t in tables})
    locks = [_lock_for_table(name) for name in names]
    for lock in locks:
        lock.acquire()
    try:
        with unit_of_work() as conn:
            yield conn
    finally:
        for lock in reversed(locks):
            lock.release()
