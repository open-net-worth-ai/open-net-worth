"""TransactionAuditStore — the transaction provenance / audit trail (storage only).

An append-only sequence of events (``txn_audit`` table). One row per transaction mutation
(create / update / delete / restore), written at the ledger write choke point. This
store is **dumb CRUD only** — building an ``AuditEvent`` and reading the active
``AuditScope`` lives in ``services/audit.py``.

It is a **runtime/forensic record, NOT user-authored data**, so it is on the checkpoint
EXCLUSION list (``ai.checkpoint._EXCLUDED_TABLES``): restoring an earlier ledger state
must NOT erase the history of how the data got there. Append-only, capped (drop oldest)
like ``CorrectionStore`` / ``NotificationLogStore``.

DESIGN: ``.claude/rfcs/transaction-provenance.md``.

PLAN 3 TASK 4: DB-backed (Core, table ``txn_audit`` — backend/db/tables.py). Rows are
promoted-columns-over-verbatim-payload: ``payload`` is the round-trip source of truth (the
event dict exactly as ``append``/``append_many`` were given), ``txn_id``/``at``/``actor``/
``surface`` are the derived query surface, recomputed from ``payload`` on every write via
``money_io._txn_audit_row`` (never duplicated here).

Hand-rolled, not a ``payload_store.PayloadTableStore`` subclass: every mutate here writes
more than the bare ``{seq, id, payload}`` shape ``PayloadTableStore`` writes (the four
promoted columns above), so full method-body reuse isn't available. The conn=/lock/hook
plumbing mirrors ``PayloadTableStore``'s own (see that module's docstring for the CONN=
COMPOSITION and CHECKPOINTS contracts — honored identically here).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, cast

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.money_io import _txn_audit_row
from backend.db.tables import txn_audit

MAX_EVENTS = 20000  # total events retained; oldest dropped (single-user forensic log)


class TransactionAuditStore:
    def __init__(self, engine: Engine | None = None):
        self._table = txn_audit
        self._engine = engine
        self._lock = _lock_for_table(txn_audit.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently.
        The DEFAULT (``engine=None``) is never cached here — resolved fresh via
        ``get_engine()`` on every call. This store can be used as a long-lived MODULE
        singleton (e.g. ``services.ledger.transactions``/``services.audit._audit``),
        constructed once at import time and shared for the process's whole lifetime;
        caching the lazily-resolved default would pin it to whichever engine happened
        to be live the FIRST time it was ever used, silently ignoring every later
        ``db_isolated``-style redirect (env var + ``engine.dispose_engine()``) —
        exactly the bug this avoids."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``PayloadTableStore._use_conn`` exactly — see that module's CONN=
        COMPOSITION docstring section for the full contract."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    def _row(self, conn: Connection, id_val: str) -> dict | None:
        row = conn.execute(select(self._table).where(self._table.c.id == id_val)).first()
        return dict(row._mapping) if row else None

    def _rows_map(self, conn: Connection, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        rows = conn.execute(select(self._table).where(self._table.c.id.in_(ids)))
        return {r.id: dict(r._mapping) for r in rows}

    def _next_seq(self, conn: Connection) -> int:
        return conn.execute(select(func.coalesce(func.max(self._table.c.seq), -1))).scalar_one() + 1

    def _enforce_cap(self, conn: Connection) -> None:
        """Drop the oldest rows (by seq) once the table holds more than MAX_EVENTS — mirrors
        JsonStore's ``items[-MAX_EVENTS:]`` trim after append. Only ``append``/``append_many``
        call this (matching the original: the generic inherited CRUD surface never enforced
        the cap either — a latent quirk this task preserves, not fixes)."""
        n = conn.execute(select(func.count()).select_from(self._table)).scalar_one()
        if n <= MAX_EVENTS:
            return
        overflow = n - MAX_EVENTS
        oldest = conn.execute(select(self._table).order_by(self._table.c.seq).limit(overflow)).all()
        if not oldest:
            return
        rows = [dict(r._mapping) for r in oldest]
        fire_db_write_hook(self._table.name, [(r["id"], r) for r in rows], conn=conn)
        ids = [r["id"] for r in rows]
        conn.execute(sa_delete(self._table).where(self._table.c.id.in_(ids)))

    # ── Query API (lock-free, mirrors JsonStore's read methods) ─────────────────

    def all(self) -> list[dict]:
        with self._eng().connect() as conn:
            return [
                r.payload for r in conn.execute(select(self._table).order_by(self._table.c.seq))
            ]

    def find_by_id(self, id_val: str) -> dict | None:
        with self._eng().connect() as conn:
            row = self._row(conn, id_val)
        return row["payload"] if row else None

    def query(self, **filters: Any) -> list[dict]:
        items = self.all()
        for key, value in filters.items():
            if value is None:
                continue
            items = [i for i in items if i.get(key) == value]
        return items

    def count(self, **filters: Any) -> int:
        return len(self.query(**filters))

    def creation_events(self, txn_ids: list[str]) -> dict[str, dict]:
        """The FIRST create/restore event per transaction, for a batch of ids in ONE query —
        the review queue reads creation provenance for a whole page without a per-row scan."""
        ids = [i for i in dict.fromkeys(txn_ids) if i]
        if not ids:
            return {}
        stmt = (
            select(self._table.c.txn_id, self._table.c.payload)
            .where(
                self._table.c.txn_id.in_(ids),
                self._table.c.payload["op"].as_string().in_(["create", "restore"]),
            )
            .order_by(self._table.c.seq)
        )
        found: dict[str, dict] = {}
        with self._eng().connect() as conn:
            for txn_id, payload in conn.execute(stmt):
                found.setdefault(txn_id, payload)
        return found

    # ── Write API (JsonStore parity — never cap-enforced; see _enforce_cap) ─────

    def insert(self, item: dict, *, conn: Connection | None = None) -> dict:
        id_val = cast(str, item.get("id"))
        with self._use_conn(conn) as conn:
            fire_db_write_hook(self._table.name, [(id_val, None)], conn=conn)
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**_txn_audit_row(seq, item)))
        return item

    def bulk_insert(self, new_items: list[dict], *, conn: Connection | None = None) -> list[dict]:
        if not new_items:
            return new_items
        with self._use_conn(conn) as conn:
            fire_db_write_hook(
                self._table.name, [(item.get("id"), None) for item in new_items], conn=conn
            )
            seq = self._next_seq(conn)
            conn.execute(
                sa_insert(self._table),
                [_txn_audit_row(seq + i, item) for i, item in enumerate(new_items)],
            )
        return new_items

    def upsert(self, item: dict, *, conn: Connection | None = None) -> dict:
        id_val = cast(str, item.get("id"))
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            if existing is not None:
                conn.execute(
                    sa_update(self._table)
                    .where(self._table.c.id == id_val)
                    .values(
                        **{
                            k: v
                            for k, v in _txn_audit_row(existing["seq"], item).items()
                            if k != "id"
                        }
                    )
                )
            else:
                seq = self._next_seq(conn)
                conn.execute(sa_insert(self._table).values(**_txn_audit_row(seq, item)))
        return item

    def update_by_id(
        self, id_val: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return None
            merged = {**existing["payload"], **changes}
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            conn.execute(
                sa_update(self._table)
                .where(self._table.c.id == id_val)
                .values(
                    **{
                        k: v
                        for k, v in _txn_audit_row(existing["seq"], merged).items()
                        if k != "id"
                    }
                )
            )
        return merged

    def bulk_patch(
        self, updates: dict[str, dict], *, conn: Connection | None = None
    ) -> dict[str, dict]:
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
                fire_db_write_hook(self._table.name, hook_rows, conn=conn)
                for iid, merged in patched.items():
                    conn.execute(
                        sa_update(self._table)
                        .where(self._table.c.id == iid)
                        .values(
                            **{
                                k: v
                                for k, v in _txn_audit_row(
                                    existing_rows[iid]["seq"], merged
                                ).items()
                                if k != "id"
                            }
                        )
                    )
        return patched

    def delete(self, id_val: str, *, conn: Connection | None = None) -> bool:
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return False
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            conn.execute(sa_delete(self._table).where(self._table.c.id == id_val))
        return True

    def replace_all(self, items: list[dict], *, conn: Connection | None = None) -> None:
        with self._use_conn(conn) as conn:
            existing_map = {r.id: dict(r._mapping) for r in conn.execute(select(self._table))}
            incoming_ids = {item.get("id") for item in items}
            hook_rows = list(existing_map.items())
            hook_rows += [(iid, None) for iid in incoming_ids if iid not in existing_map]
            fire_db_write_hook(self._table.name, hook_rows, conn=conn)
            conn.execute(self._table.delete())
            if items:
                conn.execute(
                    sa_insert(self._table),
                    [_txn_audit_row(i, item) for i, item in enumerate(items)],
                )

    # ── Writes (the real public API) ─────────────────────────────────────────

    def _append_one(self, conn: Connection, event: dict) -> None:
        eid = cast(str, event.get("id"))
        fire_db_write_hook(self._table.name, [(eid, None)], conn=conn)
        seq = self._next_seq(conn)
        conn.execute(sa_insert(self._table).values(**_txn_audit_row(seq, event)))
        self._enforce_cap(conn)

    def _append_many_rows(self, conn: Connection, events: list[dict]) -> None:
        fire_db_write_hook(self._table.name, [(e.get("id"), None) for e in events], conn=conn)
        seq = self._next_seq(conn)
        conn.execute(
            sa_insert(self._table),
            [_txn_audit_row(seq + i, e) for i, e in enumerate(events)],
        )
        self._enforce_cap(conn)

    def append(self, event: dict, *, conn: Connection | None = None) -> dict:
        """Append a fully-formed audit event (caller supplies id/ts/etc. via the
        service), capping the table at ``MAX_EVENTS`` by dropping the oldest. Returns
        the stored event.

        SAVEPOINT-ISOLATED when ``conn`` is given (plan 3 task 4, fix round 1 — mirrors
        ``ai.checkpoint.CheckpointContext.record_db_before``'s own POSTGRES DIVERGENCE
        fix, same rationale). ``services.audit``'s ``record_txn_event``/``record_txn_events``
        are documented to NEVER RAISE — a provenance failure must not break the ledger
        write it documents. That swallow was safe when audit always opened its OWN
        independent transaction; riding a ledger choke's SHARED ``locked_unit_of_work``
        (this task) makes it dialect-unsafe: SQLite tolerates a failed statement inside
        an open transaction, but Postgres aborts the WHOLE session-level transaction on
        ANY statement failure — a bare failing insert here would poison the caller's
        transaction, the swallow would hide it, and the CALLER's later COMMIT would then
        raise an unrelated-looking error, rolling back a ledger write that had already
        logically succeeded (the exact atomicity break this task's chokes exist to
        prevent). ``conn.begin_nested()`` confines any failure to just this append: the
        exception still propagates out of this method (the swallow itself stays in
        ``services.audit``, unchanged) — only the SQL-transaction-poisoning side effect
        is contained, so the shared ``conn`` is exactly as usable afterward as if this
        call had never run. The ``conn=None`` path (this store opens its OWN short
        transaction via ``_use_conn`; nothing else shares it) is UNCHANGED — nothing
        external depends on that transaction surviving a failure, so there is nothing a
        savepoint would protect there."""
        shared = conn is not None
        with self._use_conn(conn) as conn:
            if shared:
                with conn.begin_nested():
                    self._append_one(conn, event)
            else:
                self._append_one(conn, event)
        return event

    # What an erased event keeps: that something happened, when, and by whom — never what.
    _STUB_KEYS = ("id", "txn_id", "op", "ts", "actor", "surface", "source")

    def redact_for_txns(self, txn_ids: list[str], *, conn: Connection | None = None) -> int:
        """Cut every event of ``txn_ids`` to a stub (``_STUB_KEYS``): the before/after images,
        the user's intent preview, notes, session and automation names go. The permanent-delete
        choke's half of erasing a transaction (``services.transaction_purge``). Returns the
        number of events rewritten."""
        if not txn_ids:
            return 0
        with self._use_conn(conn) as conn:
            rows = conn.execute(
                select(self._table).where(self._table.c.txn_id.in_(list(txn_ids)))
            ).all()
            for row in rows:
                stub = {k: row.payload.get(k) for k in self._STUB_KEYS if k in row.payload}
                conn.execute(
                    sa_update(self._table).where(self._table.c.id == row.id).values(payload=stub)
                )
        return len(rows)

    def append_many(self, events: list[dict], *, conn: Connection | None = None) -> list[dict]:
        """Append many events in ONE transaction (the bulk import/delete path —
        avoids an O(N) write per row). No-op on an empty list.

        SAVEPOINT-ISOLATED when ``conn`` is given — see ``append``'s docstring for the
        full rationale (identical here: ``record_txn_events`` is the bulk sibling of the
        same NEVER-RAISES contract, exposed to the same Postgres shared-transaction
        hazard)."""
        if not events:
            return []
        shared = conn is not None
        with self._use_conn(conn) as conn:
            if shared:
                with conn.begin_nested():
                    self._append_many_rows(conn, events)
            else:
                self._append_many_rows(conn, events)
        return events

    # ── Queries ───────────────────────────────────────────────────────────────

    def for_txn(self, txn_id: str) -> list[dict]:
        """All events for one transaction, oldest-first (``ts`` ascending — ISO-8601
        sorts lexicographically; stable on ties)."""
        events = [e for e in self.all() if e.get("txn_id") == txn_id]
        events.sort(key=lambda e: e.get("ts", ""))
        return events

    def has_any_for(self, txn_id: str) -> bool:
        """True if any event exists for this transaction (drives the forensic-fallback
        decision in the service: recorded events win; reconstruct only when none)."""
        return any(e.get("txn_id") == txn_id for e in self.all())
