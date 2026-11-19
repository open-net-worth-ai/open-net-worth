"""TradingAgentsRunStore — data access for the TradingAgents runs (flag ``trading_agents``).

One table, ``trading_agents_runs`` (``db/tables.py``). Data access only: every decision — which
status may follow which, what a run may record — is made by
``services.trading_agents.runs.apply_run_change``, the one writer. A write takes the caller's
``conn`` from ``transaction()`` so the choke reads and writes one row inside one unit of work.
Reads open their own connection. The table is checkpoint-excluded, so nothing here fires a write
hook.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional, Sequence

from sqlalchemy import Connection, delete, func, insert, select, update

from backend.db.engine import get_engine, locked_unit_of_work
from backend.db.tables import trading_agents_runs as runs


def _row(result) -> Optional[dict]:
    row = result.mappings().first()
    return dict(row) if row is not None else None


def _filtered(stmt, ticker: Optional[str], statuses: Optional[Sequence[str]]):
    if ticker:
        stmt = stmt.where(runs.c.ticker == ticker)
    if statuses:
        stmt = stmt.where(runs.c.status.in_(list(statuses)))
    return stmt


class TradingAgentsRunStore:
    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with locked_unit_of_work(runs) as conn:
            yield conn

    def insert(self, row: dict, conn: Connection) -> dict:
        conn.execute(insert(runs).values(**row))
        return self.get(row["id"], conn=conn)

    def get(self, run_id: str, conn: Optional[Connection] = None) -> Optional[dict]:
        stmt = select(runs).where(runs.c.id == run_id)
        if conn is not None:
            return _row(conn.execute(stmt))
        with get_engine().connect() as c:
            return _row(c.execute(stmt))

    def update(self, run_id: str, patch: dict, conn: Connection) -> dict:
        conn.execute(update(runs).where(runs.c.id == run_id).values(**patch))
        return self.get(run_id, conn=conn)

    def delete(self, run_id: str, conn: Connection) -> None:
        conn.execute(delete(runs).where(runs.c.id == run_id))

    def list(
        self,
        *,
        ticker: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Newest first. Every column, reports included — a caller that only needs a row of a
        table trims it."""
        stmt = _filtered(select(runs), ticker, statuses)
        stmt = stmt.order_by(runs.c.created_at.desc(), runs.c.id.desc())
        with get_engine().connect() as c:
            return [dict(r) for r in c.execute(stmt.limit(limit).offset(offset)).mappings()]

    def count(
        self, *, ticker: Optional[str] = None, statuses: Optional[Sequence[str]] = None
    ) -> int:
        stmt = _filtered(select(func.count()).select_from(runs), ticker, statuses)
        with get_engine().connect() as c:
            return int(c.execute(stmt).scalar_one())
