"""
ImportBatchStore — tracks bulk-add batches so they can be rolled back/forward.

A batch is created whenever the AI bulk-books transactions in one call (`add_transactions`) —
a real statement import, or any other multi-row booking (e.g. an agent logging several emails
at once). Only a batch with `statement_ref_id` set is actually backed by an uploaded statement.
Each transaction in the batch carries an `batch_id` pointing back here.

Schema:
    id                : str   (UUID)
    created_at        : str   (ISO 8601 UTC)
    description       : str   (human label, e.g. "MoMo Apr 2026")
    source_account_id : str | None
    transaction_count : int
    status            : "active" | "rolled_back"
    import_session_id : str | None  (shared across all batches of the same statement split)
    statement_ref_id  : str | None  (ID of the StatementStore record this batch came from)
    balance_check     : dict | None (result of verify_statement_balance, if run)

PLAN 3 TASK 4: DB-backed (Core, table ``import_batches`` — backend/db/tables.py). Rows are
promoted-columns-over-verbatim-payload, like ``accounts``: ``payload`` is the round-trip
source of truth (the batch dict exactly as ``create_batch``/an update built it — note
``create_batch``'s own conditional key inclusion below is UNCHANGED, so a batch created
without e.g. ``balance_check`` still has no such key in its stored payload), the rest are the
derived query surface, recomputed from ``payload`` on every write via
``money_io._import_batch_row`` (never duplicated here).

Hand-rolled, not a ``payload_store.PayloadTableStore`` subclass: every mutate here writes
more than the bare ``{seq, id, payload}`` shape ``PayloadTableStore`` writes. The conn=/lock/
hook plumbing mirrors ``PayloadTableStore``'s own (see that module's docstring for the CONN=
COMPOSITION and CHECKPOINTS contracts — honored identically here).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, cast

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.money_io import _import_batch_row
from backend.db.tables import import_batches


class ImportBatchStore:
    def __init__(self, engine: Engine | None = None):
        self._table = import_batches
        self._engine = engine
        self._lock = _lock_for_table(import_batches.name)

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

    def _next_seq(self, conn: Connection) -> int:
        return conn.execute(select(func.coalesce(func.max(self._table.c.seq), -1))).scalar_one() + 1

    def _write_existing(self, conn: Connection, id_val: str, row: dict) -> None:
        conn.execute(
            sa_update(self._table)
            .where(self._table.c.id == id_val)
            .values(**{k: v for k, v in row.items() if k != "id"})
        )

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

    # ── Write API (JsonStore parity) ─────────────────────────────────────────────

    def insert(self, item: dict, *, conn: Connection | None = None) -> dict:
        id_val = cast(str, item.get("id"))
        with self._use_conn(conn) as conn:
            fire_db_write_hook(self._table.name, [(id_val, None)], conn=conn)
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**_import_batch_row(seq, item)))
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
                [_import_batch_row(seq + i, item) for i, item in enumerate(new_items)],
            )
        return new_items

    def upsert(self, item: dict, *, conn: Connection | None = None) -> dict:
        id_val = cast(str, item.get("id"))
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            if existing is not None:
                self._write_existing(conn, id_val, _import_batch_row(existing["seq"], item))
            else:
                seq = self._next_seq(conn)
                conn.execute(sa_insert(self._table).values(**_import_batch_row(seq, item)))
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
            self._write_existing(conn, id_val, _import_batch_row(existing["seq"], merged))
        return merged

    def bulk_patch(
        self, updates: dict[str, dict], *, conn: Connection | None = None
    ) -> dict[str, dict]:
        if not updates:
            return {}
        with self._use_conn(conn) as conn:
            existing_rows = {
                iid: row for iid in updates if (row := self._row(conn, iid)) is not None
            }
            patched: dict[str, dict] = {}
            hook_rows: list[tuple[str, dict | None]] = []
            for iid, changes in updates.items():
                if iid in existing_rows:
                    patched[iid] = {**existing_rows[iid]["payload"], **changes}
                    hook_rows.append((iid, existing_rows[iid]))
            if patched:
                fire_db_write_hook(self._table.name, hook_rows, conn=conn)
                for iid, merged in patched.items():
                    self._write_existing(
                        conn, iid, _import_batch_row(existing_rows[iid]["seq"], merged)
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
                    [_import_batch_row(i, item) for i, item in enumerate(items)],
                )

    # ── Domain API ────────────────────────────────────────────────────────────

    def create_batch(
        self,
        description: str,
        transaction_count: int,
        source_account_id: str | None = None,
        balance_check: dict | None = None,
        import_session_id: str | None = None,
        statement_ref_id: str | None = None,
        *,
        batch_id: str | None = None,
        conn: Connection | None = None,
    ) -> dict:
        batch = {
            "id": batch_id or new_id("imp"),
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "description": description,
            "source_account_id": source_account_id,
            "transaction_count": transaction_count,
            "status": "active",
        }
        if import_session_id is not None:
            batch["import_session_id"] = import_session_id
        if statement_ref_id is not None:
            batch["statement_ref_id"] = statement_ref_id
        if balance_check is not None:
            batch["balance_check"] = balance_check
        return self.insert(batch, conn=conn)

    def set_balance_check(
        self, batch_id: str, balance_check: dict, *, conn: Connection | None = None
    ) -> dict | None:
        return self.update_by_id(batch_id, {"balance_check": balance_check}, conn=conn)

    def set_rolled_back_txn_ids(
        self, batch_id: str, txn_ids: list[str], *, conn: Connection | None = None
    ) -> dict | None:
        """Record the exact transaction IDs that rollback_import_batch soft-deleted.

        Stored so restore_import_batch can un-delete only those rows — not any
        transactions the user may have explicitly deleted before or after the rollback.
        """
        return self.update_by_id(batch_id, {"rolled_back_txn_ids": txn_ids}, conn=conn)

    def query_by_session(self, import_session_id: str) -> list[dict]:
        return [b for b in self.all() if b.get("import_session_id") == import_session_id]

    def set_status(
        self, batch_id: str, status: str, *, conn: Connection | None = None
    ) -> dict | None:
        return self.update_by_id(batch_id, {"status": status}, conn=conn)

    def list_batches(self, status: str | None = None) -> list[dict]:
        items = self.all()
        if status is not None:
            items = [b for b in items if b.get("status") == status]
        return sorted(items, key=lambda b: b.get("created_at", ""), reverse=True)
