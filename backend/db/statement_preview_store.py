"""StatementPreviewStore — the finished decision preview of a statement, one row per statement.

A derived, overwritten-in-place cache (table ``statement_previews``, checkpoint-excluded, ON
DELETE CASCADE with its statement): ``decision_preview`` writes its final response here before
the record can read ``ready``, and a reopen reads it back as-is. Data access only — what makes a
stored preview servable for a request is ``services.statements.stored_previews``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import statement_previews


class StatementPreviewStore:
    def __init__(self, engine: Engine | None = None):
        self._engine = engine
        self._lock = _lock_for_table(statement_previews.name)

    def _eng(self) -> Engine:
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    def get(self, statement_id: str) -> dict | None:
        with self._eng().connect() as conn:
            row = conn.execute(
                select(statement_previews).where(statement_previews.c.statement_id == statement_id)
            ).first()
        return dict(row._mapping) if row else None

    def put(
        self,
        statement_id: str,
        *,
        fmt: str,
        instr_hash: str | None,
        account_arg: str | None,
        resolved_account_id: str | None,
        preview: dict,
    ) -> None:
        values = {
            "fmt": fmt,
            "instr_hash": instr_hash,
            "account_arg": account_arg,
            "resolved_account_id": resolved_account_id,
            "preview": preview,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with self._lock, self._eng().begin() as conn:
            existing = conn.execute(
                select(statement_previews).where(statement_previews.c.statement_id == statement_id)
            ).first()
            fire_db_write_hook(
                statement_previews.name,
                [(statement_id, dict(existing._mapping) if existing else None)],
                conn=conn,
            )
            if existing:
                conn.execute(
                    sa_update(statement_previews)
                    .where(statement_previews.c.statement_id == statement_id)
                    .values(**values)
                )
            else:
                conn.execute(
                    sa_insert(statement_previews).values(statement_id=statement_id, **values)
                )

    def delete(self, statement_id: str) -> None:
        with self._lock, self._eng().begin() as conn:
            existing = conn.execute(
                select(statement_previews).where(statement_previews.c.statement_id == statement_id)
            ).first()
            if existing is None:
                return
            fire_db_write_hook(
                statement_previews.name, [(statement_id, dict(existing._mapping))], conn=conn
            )
            conn.execute(
                sa_delete(statement_previews).where(
                    statement_previews.c.statement_id == statement_id
                )
            )
