"""MemoryForgottenStore — the fingerprints of facts the user told Lucius to forget.

One row per fingerprint (table ``memory_forgotten``): a SHA-256 of a forgotten fact's normalised
text and when it was forgotten — never the text. Data access only; computing a fingerprint and
deciding what it blocks is the memory write choke's job (``ai.memory_write``). Writes fire the
checkpoint before-image hook like ``memory`` itself, so a rewound forget drops its fingerprint
with the fact coming back. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 7).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import memory_forgotten


class MemoryForgottenStore:
    def __init__(self, engine: Engine | None = None):
        self._engine = engine
        self._lock = _lock_for_table(memory_forgotten.name)

    def _eng(self) -> Engine:
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    def all_hashes(self) -> set[str]:
        with self._eng().connect() as conn:
            return {r.hash for r in conn.execute(select(memory_forgotten.c.hash))}

    def known(self, hashes: set[str]) -> set[str]:
        """The subset of ``hashes`` on record."""
        if not hashes:
            return set()
        with self._eng().connect() as conn:
            return self._present(conn, hashes)

    def record(self, hashes: set[str]) -> None:
        """Put ``hashes`` on record; one already there keeps its first date."""
        if not hashes:
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._eng().begin() as conn:
            fresh = sorted(hashes - self._present(conn, hashes))
            if not fresh:
                return
            fire_db_write_hook(memory_forgotten.name, [(h, None) for h in fresh], conn=conn)
            conn.execute(
                sa_insert(memory_forgotten), [{"hash": h, "forgotten_at": stamp} for h in fresh]
            )

    def clear(self, hashes: set[str]) -> None:
        """Take ``hashes`` off record — the user saved that text again."""
        if not hashes:
            return
        with self._lock, self._eng().begin() as conn:
            rows = conn.execute(
                select(memory_forgotten).where(memory_forgotten.c.hash.in_(sorted(hashes)))
            ).fetchall()
            if not rows:
                return
            fire_db_write_hook(
                memory_forgotten.name, [(r.hash, dict(r._mapping)) for r in rows], conn=conn
            )
            conn.execute(
                sa_delete(memory_forgotten).where(
                    memory_forgotten.c.hash.in_([r.hash for r in rows])
                )
            )

    @staticmethod
    def _present(conn, hashes: set[str]) -> set[str]:
        rows = conn.execute(
            select(memory_forgotten.c.hash).where(memory_forgotten.c.hash.in_(sorted(hashes)))
        )
        return {r.hash for r in rows}
