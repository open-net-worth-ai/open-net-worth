"""PayloadTableStore — generic JsonStore-surface base over a (seq, id, payload) table.

The template every remaining flat-list JSON store (Plans 3-4, ~40 stores) subclasses:
table + revision + subclass + importer, instead of hand-rolling read-modify-write
plumbing each time. Its surface is an EXACT behavioral clone of ``db.json_store.
JsonStore`` — read that module first, it is the executable spec. A payload-table row IS
one JsonStore item: ``seq`` stands in for the flat list's insertion-order index, and
``id`` is promoted from a plain dict key to a real, DB-enforced primary key (a duplicate
id therefore raises on write here, where a JsonStore-backed flat list would silently
tolerate it — a stricter, deliberate improvement, not a parity gap: nothing in this
codebase relies on duplicate ids ever existing).

CONCURRENCY: JsonStore serializes every mutating call under one lock PER FILE PATH
(``json_store._lock_for``). A payload table has no file path, so the equivalent unit is
the TABLE NAME — ``_lock_for_table`` is the same registry pattern (a dict keyed by name,
built lazily under a meta-lock) collapsed onto that unit; the registry itself lives in
``backend.db.engine`` as of Plan 3 task 1 (moved from here — see that module's own
comment for why: ``locked_unit_of_work``, below, needs the SAME registry, and hosting it
in payload_store.py would force engine.py to import payload_store.py, an import cycle),
this module just imports the function. Unlike EvidenceStore / PriceHistoryStore (each
hard-codes ONE bare module lock because each only ever guards its own single table),
PayloadTableStore backs MANY distinct tables from ONE class, so the registry is
load-bearing here, not decorative. Every mutate holds its table's lock across the FULL
read -> hook -> write cycle, inside ONE transaction (``self._eng().begin()`` — the same
commit-on-clean-exit/rollback-on-exception contract as ``backend.db.engine.unit_of_work``,
written directly so an injected ``engine=`` override — tests pass their own — is
honored; the free function always binds the process singleton). ``seq`` is assigned
``max(seq)+1`` (0 for an empty table) INSIDE that same transaction/lock, closing the
exact race ``test_merge_hits_concurrent_writers_no_seq_collision`` (evidence_store.py)
proved against a SELECT-then-INSERT done outside a shared lock — now structural for
every future subclass instead of a per-store lesson re-learned.

CONN= COMPOSITION (Plan 3 task 1): every public mutate below takes an optional
``conn: Connection | None = None`` as its LAST keyword argument — the choke-point seam
every multi-store money write composes through, starting with Plan 3 task 4's ledger
chokes. ``conn=None`` (the default, and every call site that predates this task) is
BYTE-IDENTICAL to the mutate's original behavior: take this table's lock, open
``self._eng().begin()``, write, commit on clean exit / roll back on exception — see
``_use_conn`` below. Passing ``conn=<a connection>`` instead means the CALLER already
owns BOTH a transaction (``conn`` IS that open transaction) and this table's lock — the
mutate then does NOT acquire ``self._lock`` and does NOT call ``begin()``; it runs its
statements straight against the given connection and still fires the checkpoint hook
through it, same as always.

LOUD WARNING: nothing inside a mutate can detect or refuse an unsafe ``conn=`` call —
accepting the caller's connection at all means trusting the caller already holds this
table's lock, since taking it again on the same thread would deadlock against itself
(``threading.Lock`` is not reentrant). The ONLY supported way to obtain a ``conn`` for
this purpose is ``backend.db.engine.locked_unit_of_work(*tables)``, which acquires every
composed table's lock in one global order (alphabetical by name) before opening the
transaction — that global order is what makes composing SEVERAL tables' writes
deadlock-free across concurrent callers. A ``conn=`` passed from anywhere else (a bare
``engine.unit_of_work()``, or a connection whose locks were taken in some other order) is
racing every other thread that touches the same table correctly — treat any such call
site as a bug, not a shortcut.

CHECKPOINTS: when ``checkpointed=True`` (the default), every mutate collects the
before-image of each pk it is about to touch — ``(pk, payload)`` if the row exists,
``(pk, None)`` if it doesn't — and fires ``core.io.fire_db_write_hook`` BEFORE the first
write statement, passing the transaction's OWN connection (the review-hardened contract
from spec D7 / plan 2 task 2: the image then commits atomically with the write it
precedes — a crash lands neither or both, never a checkpoint that outlives the row it
describes, or a row change with no way back; see that module's docstring for what a
conn-less call from inside an open write transaction costs). A store whose table sits
in ``ai.checkpoint._EXCLUDED_TABLES`` may still construct with ``checkpointed=True``
harmlessly — the checkpoint layer is what decides captured-vs-excluded, this seam only
carries the call (mirrors evidence/price_points today). ``checkpointed=False`` is for a
store that must skip even the hook call (a hot append-only cache, the PriceHistoryStore
shape) — no before-image rows are ever computed or passed for it. ``__init__`` REFUSES
(``RuntimeError``, at construction) a ``checkpointed=True`` table whose primary key is
not single-column — the capture-time twin of ``ai.checkpoint._single_pk_col``'s replay-time
guard (duplicated, not imported: ``db`` must never import ``ai``), since ``payload_table()``
itself only ever mints a single-column ``id`` pk and a hand-built composite-pk ``Table``
would otherwise only be caught the first time a restore replayed it.

``query``/``count`` are Python-side equality filters over ``all()`` — NO JSON-path SQL
(the dialect rule: SQLite's ``json_extract`` and Postgres's ``->>`` are not portable
enough to lean on for a store this generic; a subclass with a real hot filter earns its
own indexed column instead, as ``evidence``'s ``expires_at``/``domain`` already do).
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    ForeignKey,
    Index,
    Integer,
    Table,
    Text,
    func,
    select,
    text,
)
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import DEFAULT_BOOK_ID, metadata, settings


def _assert_single_column_pk(table: Table) -> None:
    """Capture-time guard (Plan-2 final review I2 PLUS): a checkpointed store's table
    must have a single-column primary key, checked at CONSTRUCTION — not discovered
    only at replay time, deep inside a restore, the way ``ai.checkpoint._single_pk_col``
    already guards. The message MIRRORS that helper's exactly; the check is duplicated
    here rather than imported because ``db`` must never import ``ai`` (the dependency
    rule — ``db`` sits BELOW ``ai``). A composite pk's delete-by-fragment on replay
    would touch every row sharing that fragment, not just the intended one; refusing
    the whole store at construction is strictly earlier and louder than that."""
    cols = list(table.primary_key.columns)
    if len(cols) != 1:
        raise RuntimeError(
            f"write_log checkpoints support single-column primary keys only; "
            f"table {table.name!r} has {len(cols)} — extend write_log before "
            "checkpoint-including this table (composite pks would corrupt "
            "unrelated rows on replay)"
        )


def payload_table(name: str) -> Table:
    """Register a (seq, id, payload) table on the shared metadata. ``seq`` orders rows
    by insertion (unique-indexed, never reused); ``id`` is the real primary key;
    ``payload`` is the verbatim item dict, stored whole — round-trip fidelity, like
    ``evidence.payload`` (see tables.py for why JSON, not JSONB)."""
    return Table(
        name,
        metadata,
        Column("seq", Integer, nullable=False),
        Column("id", Text, primary_key=True),
        Column("payload", JSON, nullable=False),
        Index(f"ix_{name}_seq", "seq", unique=True),
    )


def book_payload_table(name: str, *extra: Any) -> Table:
    """Tier-1 book-scoped flat-list table: ``payload_table()`` PLUS a book_id FK column and a
    book-LEADING unique seq index — the money core's exact index shape (ix_<name>_book_seq).
    The one schema helper every book-scoped flat-list store (planning wave 4a, investments
    wave b) is built from. ``extra`` adds promoted columns/indexes (``lots.txn_id``); the store
    keeps them in step through ``_insert_row``/``_payload_values``."""
    return Table(
        name,
        metadata,
        Column("book_id", Text, ForeignKey("books.id"), nullable=False),
        Column("seq", Integer, nullable=False),
        Column("id", Text, primary_key=True),
        Column("payload", JSON, nullable=False),
        Index(f"ix_{name}_book_seq", "book_id", "seq", unique=True),
        *extra,
    )


class PayloadTableStore:
    def __init__(
        self,
        table: Table,
        *,
        id_field: str = "id",
        engine=None,
        checkpointed: bool = True,
    ):
        if checkpointed:
            _assert_single_column_pk(table)
        self._table = table
        self._id_field = id_field
        self._engine = engine
        self._checkpointed = checkpointed
        self._lock = _lock_for_table(table.name)

    def _eng(self):
        """``engine=`` given at construction (test injection) is honored permanently.
        The DEFAULT (``engine=None``) is NEVER cached here — resolved fresh via
        ``get_engine()`` on every call (fix round 1, plan 3 task 4 — the identical bug
        found and fixed in all six money stores; ``PayloadTableStore`` is a long-lived
        MODULE singleton too, e.g. ``InstrumentStore()`` in ``services/activity.py``,
        ``services/dividend_calendar.py``, ``services/equity.py``, ``services/income.py``
        — constructed once at import time and shared for the process's whole lifetime).
        Caching the lazily-resolved default permanently would pin it to whichever engine
        happened to be live the FIRST time it was ever used, silently ignoring every
        later ``db_isolated``-style redirect (env var + ``engine.dispose_engine()``) —
        exactly the bug this avoids."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    # ── Row + hook helpers ───────────────────────────────────────────────────────

    def _row(self, conn, id_val: str) -> dict | None:
        """Full row ``{seq, id, payload}``, or None. This — not the bare payload — is
        the shape write_log's before_payload must carry: a restore replays it via
        ``table.insert().values(**before_payload)`` (ai/checkpoint.py), which needs
        every real column so the row reappears at its ORIGINAL seq/position, not
        whatever seq happens to be next at restore time."""
        row = conn.execute(select(self._table).where(self._table.c.id == id_val)).first()
        return dict(row._mapping) if row else None

    def _rows_map(self, conn, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        rows = conn.execute(select(self._table).where(self._table.c.id.in_(ids)))
        return {r.id: dict(r._mapping) for r in rows}

    def _insert_row(self, seq: int, id_val: Any, item: dict) -> dict:
        """Column dict for ONE new row. Overridden by ``BookPayloadTableStore``. Default is
        byte-identical to the pre-refactor inline ``{"seq", "id", "payload"}`` — instruments
        (Tier-2) is unaffected. Every INSERT site below (insert/bulk_insert/upsert's insert
        branch/replace_all's insert branch) builds its row through this ONE overridable
        helper instead of four duplicated inline dicts, so a book-scoped variant is a
        one-method override rather than four repeated method bodies."""
        return {"seq": seq, "id": id_val, "payload": item}

    def _payload_values(self, item: dict) -> dict:
        """Column values for a payload UPDATE — overridden by a store with a promoted column."""
        return {"payload": item}

    def _row_item(self, row: Any) -> dict:
        """The item one fetched row reads as — overridden by a store with a promoted column."""
        return row.payload

    def _next_seq(self, conn) -> int:
        return conn.execute(select(func.coalesce(func.max(self._table.c.seq), -1))).scalar_one() + 1

    def _fire_hook(self, conn, rows: list[tuple[Any, dict | None]]) -> None:
        """Fire the checkpoint before-image hook THROUGH the caller's own open
        transaction — see the module docstring's CHECKPOINTS section for why ``conn``
        must never be omitted here. ``Any`` (not ``str``) for the pk slot: it comes
        straight from ``item.get(id_field)``, which — like JsonStore's own
        ``item.get(self._id_field)`` — is not guaranteed non-None if a caller inserts
        an item missing its id field; that reaches the DB as a NOT NULL/PK violation,
        a loud failure, same as an id-less item silently never matching anything in a
        JsonStore-backed flat list would be a quiet one."""
        if not self._checkpointed or not rows:
            return
        fire_db_write_hook(self._table.name, rows, conn=conn)

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """The conn= contract (module docstring's CONN= COMPOSITION section): given
        the caller's own open connection, use it AS-IS — no lock, no transaction of
        our own, since the caller already holds both. Given None (every call site
        before Plan 3 task 1, and every call site that still wants standalone
        behavior), this is byte-identical to the pre-task-1 shape: take this
        table's lock, open self._eng().begin(), yield it, commit on clean exit /
        roll back on exception."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    # ── Query API (lock-free, mirrors JsonStore's read methods) ─────────────────

    def all(self, *, conn: Connection | None = None) -> list[dict]:
        """``conn``, when given, reads THROUGH the caller's own open transaction (Task 4,
        storage plan 4b) instead of a fresh, separate connection — load-bearing for a
        composed choke's read-after-write (e.g. ``position_figures.rederive``
        re-reading a position's open lots right after ``bulk_consume`` wrote them in the
        SAME still-open transaction): a fresh connection cannot see another connection's
        uncommitted rows, so it would silently compute a STALE aggregate. Omitting ``conn``
        (``None``, the default — every call site before Task 4) is byte-identical: a
        fresh, lock-free read."""
        if conn is not None:
            return [
                self._row_item(r)
                for r in conn.execute(select(self._table).order_by(self._table.c.seq))
            ]
        with self._eng().connect() as owned_conn:
            return [
                self._row_item(r)
                for r in owned_conn.execute(select(self._table).order_by(self._table.c.seq))
            ]

    def find_by_id(self, id_val: str, *, conn: Connection | None = None) -> dict | None:
        """``conn``, when given, reads THROUGH the caller's own open transaction (choke
        composition, plan 4a task 5 fix round 1) instead of a fresh, separate connection —
        load-bearing for a domain wrapper (``EnvelopeStore.update_envelope``,
        ``EnvelopeGroupsStore.update_group``) whose OWN existence pre-check must see a row
        this SAME transaction already inserted but has not committed yet; a fresh connection
        cannot see uncommitted writes from another (here: this same, still-open) transaction,
        so it would wrongly report "not found" and the wrapper would silently no-op. Omitting
        ``conn`` (``None``, the default) is byte-identical to before: a fresh, lock-free read."""
        if conn is not None:
            row = self._row(conn, id_val)
            return row["payload"] if row else None
        with self._eng().connect() as owned_conn:
            row = self._row(owned_conn, id_val)
        return row["payload"] if row else None

    def query(self, **filters: Any) -> list[dict]:
        """Equality filter on any payload field. None values are SKIPPED (not "match
        null") — Python-side, see the module docstring's dialect-rule paragraph."""
        items = self.all()
        for key, value in filters.items():
            if value is None:
                continue
            items = [i for i in items if i.get(key) == value]
        return items

    def count(self, **filters: Any) -> int:
        return len(self.query(**filters))

    # ── Write API ─────────────────────────────────────────────────────────────

    def insert(self, item: dict, *, conn: Connection | None = None) -> dict:
        id_val = item.get(self._id_field)
        with self._use_conn(conn) as conn:
            # A successful INSERT proves the id was new — the PK constraint would
            # otherwise have raised and rolled back this whole transaction, hook
            # included — so no existence lookup is needed before the before-image.
            self._fire_hook(conn, [(id_val, None)])
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**self._insert_row(seq, id_val, item)))
        return item

    def bulk_insert(self, new_items: list[dict], *, conn: Connection | None = None) -> list[dict]:
        """Append multiple items in one transaction. Empty input is a true no-op — an
        empty executemany is a driver-dependent landmine, and JsonStore's own no-op
        write has no observable side effect for a caller to depend on either."""
        if not new_items:
            return new_items
        with self._use_conn(conn) as conn:
            self._fire_hook(conn, [(item.get(self._id_field), None) for item in new_items])
            seq = self._next_seq(conn)
            conn.execute(
                sa_insert(self._table),
                [
                    self._insert_row(seq + i, item.get(self._id_field), item)
                    for i, item in enumerate(new_items)
                ],
            )
        return new_items

    def upsert(self, item: dict, *, conn: Connection | None = None) -> dict:
        """Insert or replace by id_field. A replace KEEPS the row's seq (matches
        JsonStore's in-place ``items[i] = item``); a new id is appended (next seq)."""
        id_val = item.get(self._id_field)
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            self._fire_hook(conn, [(id_val, existing)])
            if existing is not None:
                conn.execute(
                    sa_update(self._table)
                    .where(self._table.c.id == id_val)
                    .values(**self._payload_values(item))
                )
            else:
                seq = self._next_seq(conn)
                conn.execute(sa_insert(self._table).values(**self._insert_row(seq, id_val, item)))
        return item

    def update_by_id(
        self, id_val: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Patch in place (``{**item, **changes}``), same seq/position. No-op (no
        write, no hook) if the id doesn't exist — returns None, matching JsonStore."""
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return None
            merged = {**existing["payload"], **changes}
            self._fire_hook(conn, [(id_val, existing)])
            conn.execute(
                sa_update(self._table)
                .where(self._table.c.id == id_val)
                .values(**self._payload_values(merged))
            )
        return merged

    def bulk_patch(
        self, updates: dict[str, dict], *, conn: Connection | None = None
    ) -> dict[str, dict]:
        """Patch multiple ids in ONE transaction (atomic under the table lock — see
        JsonStore.bulk_patch's docstring for why two rows must commit together).
        Returns id -> patched item for FOUND ids only; ids absent from the table are
        silently skipped, same as JsonStore."""
        if not updates:
            return {}
        with self._use_conn(conn) as conn:
            existing_rows = self._rows_map(conn, list(updates.keys()))
            patched: dict[str, dict] = {}
            hook_rows: list[tuple[str, dict | None]] = []
            for iid, changes in updates.items():
                if iid in existing_rows:
                    patched[iid] = {**existing_rows[iid]["payload"], **changes}
                    hook_rows.append((iid, existing_rows[iid]))
            if patched:
                self._fire_hook(conn, hook_rows)
                for iid, merged in patched.items():
                    conn.execute(
                        sa_update(self._table)
                        .where(self._table.c.id == iid)
                        .values(**self._payload_values(merged))
                    )
        return patched

    def delete(self, id_val: str, *, conn: Connection | None = None) -> bool:
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return False
            self._fire_hook(conn, [(id_val, existing)])
            conn.execute(sa_delete(self._table).where(self._table.c.id == id_val))
        return True

    def replace_all(self, items: list[dict], *, conn: Connection | None = None) -> None:
        """Wipe the table and renumber every row's seq from 0, in ``items`` order.
        Checkpoint images EVERY existing row (all touched by the wipe) plus
        ``(id, None)`` for any incoming id not already present (a fresh insert)."""
        with self._use_conn(conn) as conn:
            existing_map = {r.id: dict(r._mapping) for r in conn.execute(select(self._table))}
            incoming_ids = {item.get(self._id_field) for item in items}
            hook_rows = list(existing_map.items())
            hook_rows += [(iid, None) for iid in incoming_ids if iid not in existing_map]
            self._fire_hook(conn, hook_rows)
            conn.execute(self._table.delete())
            if items:
                conn.execute(
                    sa_insert(self._table),
                    [
                        self._insert_row(i, item.get(self._id_field), item)
                        for i, item in enumerate(items)
                    ],
                )


class BookPayloadTableStore(PayloadTableStore):
    """Tier-1 book-scoped store. IDENTICAL public surface to PayloadTableStore; every row it
    inserts carries book_id=DEFAULT_BOOK_ID. Reads are UNFILTERED today — the exact behavior
    of the money stores (account_store.py carries the column + writes DEFAULT_BOOK_ID + has
    book-leading indexes but applies no WHERE book_id on reads; the enforced base-class filter
    is the deferred family-mode concern). Reused by wave b (investments)."""

    def _insert_row(self, seq: int, id_val: Any, item: dict) -> dict:
        return {"book_id": DEFAULT_BOOK_ID, "seq": seq, "id": id_val, "payload": item}


class NamespacedSettingsStore:
    """Dict-shaped singleton config keyed by a fixed ``namespace`` string — ONE row
    ``(namespace, payload)`` in a namespace-keyed table (plan 4d task 1, spec:205; ``table=``
    added Wave 5C so a checkpoint-EXCLUDED singleton can use the SAME shape over a DIFFERENT
    physical table — see ``db.tables.runtime_state``'s own docstring for why it can't share
    ``settings``). NOT a ``PayloadTableStore`` subclass: that shape is a flat LIST of
    independently addressable rows (seq/id/payload); this is a single fixed-key ROW per
    namespace, closer to ``plan_state``'s/``watchlists``' per-book singleton shape —
    ``_eng``/``_use_conn`` mirror ``PlanStateStore``'s verbatim, keyed by ``namespace`` instead
    of ``book_id`` (many different namespaces share ONE table, rather than one row per book).
    ``table`` defaults to ``settings`` (the checkpoint-INCLUDED, non-portable home for
    ``memory_settings``/``preferred_models``/Wave 5C's ``dedup_config`` — every pre-Wave-5C
    caller omits it, byte-identical behavior); pass ``table=runtime_state`` for a
    checkpoint-EXCLUDED singleton (Wave 5C's ``harness_state``) instead.

    ``get() -> dict`` returns this namespace's payload, or ``{}`` when the row doesn't exist
    yet. ``save(doc)`` replaces the WHOLE payload — a namespace is one blob, never
    per-key-patched, mirroring ``MemorySettingsStore._save_doc``/``PreferredModelsStore.
    replace`` — firing ``fire_db_write_hook`` with the before-image so a checkpoint-INCLUDED
    namespace is undoable by ``/rewind`` (an EXCLUDED one, ``ai.checkpoint._EXCLUDED_TABLES``,
    no-ops there like any other excluded table). A subclass or caller layers domain methods
    (e.g. ``enabled()``/``set_enabled()``) over these two.
    """

    def __init__(self, namespace: str, table: Table = settings, engine=None):
        self._namespace = namespace
        self._table = table
        self._engine = engine
        self._write_lock = _lock_for_table(table.name)

    def _eng(self):
        """``engine=`` given at construction (test injection) is honored permanently. The
        DEFAULT (``engine=None``) is never cached here — resolved fresh via ``get_engine()``
        on every call, exactly like ``PlanStateStore._eng``: a namespaced settings store may
        also be constructed once as a long-lived module singleton, and caching a
        lazily-resolved default would pin it to whichever engine happened to be live the
        FIRST time it was ever used, silently ignoring a later ``db_isolated``-style
        redirect."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``PlanStateStore._use_conn``/``PayloadTableStore._use_conn`` exactly — see
        either module's CONN= COMPOSITION docstring section for the full contract."""
        if conn is not None:
            yield conn
            return
        with self._write_lock, self._eng().begin() as owned_conn:
            yield owned_conn

    @contextmanager
    def _use_serialized_namespace_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """One database-serialized transaction for a namespace read-modify-write.

        ``_use_conn`` preserves the historical in-process lock contract, but independent
        workers have independent lock registries. SQLite therefore takes its database writer
        lock before reading (``BEGIN IMMEDIATE``); Postgres takes a transaction-scoped advisory
        lock derived from the physical table + namespace. Both protect a missing row as well as
        an existing one, which a row lock alone cannot do.
        """
        if conn is not None:
            yield conn
            return

        engine = self._eng()
        if engine.dialect.name == "sqlite":
            with self._write_lock, engine.connect() as owned_conn:
                owned_conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    yield owned_conn
                except Exception:
                    owned_conn.rollback()
                    raise
                else:
                    owned_conn.commit()
            return

        with self._write_lock, engine.begin() as owned_conn:
            if engine.dialect.name == "postgresql":
                lock_material = f"{self._table.name}:{self._namespace}".encode()
                lock_key = int.from_bytes(
                    hashlib.blake2b(lock_material, digest_size=8).digest(), "big", signed=True
                )
                owned_conn.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
                )
            yield owned_conn

    def _row(self, conn: Connection) -> dict | None:
        row = conn.execute(
            select(self._table).where(self._table.c.namespace == self._namespace)
        ).first()
        return dict(row._mapping) if row else None

    def get(self, *, conn: Connection | None = None) -> dict:
        """This namespace's stored payload, or ``{}`` when the row doesn't exist yet."""
        if conn is not None:
            row = self._row(conn)
            return row["payload"] if row else {}
        with self._eng().connect() as owned_conn:
            row = self._row(owned_conn)
        return row["payload"] if row else {}

    def save(self, doc: dict, *, conn: Connection | None = None) -> None:
        """Upsert this namespace's payload — see the class docstring for the
        whole-dict-replace contract."""
        with self._use_conn(conn) as conn:
            existing = self._row(conn)
            fire_db_write_hook(self._table.name, [(self._namespace, existing)], conn=conn)
            if existing is not None:
                conn.execute(
                    sa_update(self._table)
                    .where(self._table.c.namespace == self._namespace)
                    .values(payload=doc)
                )
            else:
                conn.execute(sa_insert(self._table).values(namespace=self._namespace, payload=doc))
