"""Book-scoped repositories and transaction boundary for trading state."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import func, select, update

from backend.core.errors import ConflictError, NotFoundError
from backend.db.engine import locked_unit_of_work
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import DEFAULT_BOOK_ID, TRADING_TABLES


class TradingStore(BookPayloadTableStore):
    def __init__(self, kind: str, *, engine=None):
        super().__init__(TRADING_TABLES[f"trading_{kind}"], engine=engine, checkpointed=False)

    def _insert_row(self, seq, id_val, item):
        return {**super()._insert_row(seq, id_val, item), "scope_id": item.get("scope_id", "")}

    def all(self, *, conn=None):
        return self.list_scope(None, conn=conn)

    def list_scope(self, scope_id: str | None, *, conn=None, limit=None, offset=0):
        query = select(self._table.c.payload).where(self._table.c.book_id == DEFAULT_BOOK_ID)
        if scope_id is not None:
            query = query.where(self._table.c.scope_id == scope_id)
        query = query.order_by(self._table.c.seq).offset(offset)
        if limit is not None:
            query = query.limit(limit)
        if conn is not None:
            return list(conn.execute(query).scalars())
        with self._eng().connect() as connection:
            return list(connection.execute(query).scalars())

    def required(self, item_id: str, *, conn=None):
        row = self.find_by_id(item_id, conn=conn)
        if row is None:
            raise NotFoundError("Trading record not found", record_id=item_id)
        return row

    def count_scope(self, scope_id: str, *, conn):
        return conn.execute(
            select(func.count())
            .select_from(self._table)
            .where(self._table.c.book_id == DEFAULT_BOOK_ID, self._table.c.scope_id == scope_id)
        ).scalar_one()


@contextmanager
def trading_transaction():
    """The singleton row serializes financial decisions across processes and DB dialects."""
    with locked_unit_of_work(*TRADING_TABLES.values()) as conn:
        control = TRADING_TABLES["trading_control"]
        result = conn.execute(
            update(control).where(control.c.id == "trading_lock").values(seq=control.c.seq)
        )
        if result.rowcount != 1:
            raise ConflictError("Trading transaction ownership record is missing")
        yield conn
