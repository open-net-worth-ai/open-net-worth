"""NewsDeliveryStore — the Intel delivery decider's ledger (table ``news_deliveries``).

Promoted columns (``status``, ``story_id``, ``told_at`` …) carry the status machine and the
reads; ``payload`` is the verbatim row, the round-trip source of truth — the same shape as
``ClaimsStore``. Unlike claims, this is runtime scheduler state like ``pending_pushes``: it fires
no write hook and the table is checkpoint-EXCLUDED, because a /rewind that turned a told item back
into an unsent one would re-send it.

Every transition is a read-then-decide INSIDE the store's own lock and transaction
(``transition``), so "an item is told at most once" cannot race a concurrent writer. The one
choke is ``services.news_delivery.apply_delivery_change`` — reach the write methods only
through it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from sqlalchemy import Connection, func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.db.engine import _lock_for_table
from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import news_deliveries, runtime_state

_PROMOTED = (
    "status",
    "decision",
    "source",
    "story_id",
    "subject",
    "created_at",
    "decided_at",
    "told_at",
    "expires_at",
)


def _columns(seq: int, row: dict) -> dict:
    """Column dict for one row. Promoted columns are DERIVED from ``row`` (never the other way)."""
    values: dict[str, Any] = {col: row.get(col) for col in _PROMOTED}
    values["story_id"] = row.get("story_id") or ""
    values["subject"] = row.get("subject") or ""
    values["status"] = row.get("status") or "pending"
    values["user_set"] = 1 if row.get("user_set") else 0
    return {"seq": seq, "id": row["id"], "payload": row, **values}


class NewsDeliveryStore:
    def __init__(self, engine=None):
        self._engine = engine
        self._lock = _lock_for_table(news_deliveries.name)

    def _eng(self):
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _write(self) -> Iterator[Connection]:
        with self._lock, self._eng().begin() as conn:
            yield conn

    def _read(self, query) -> list[dict]:
        with self._eng().connect() as conn:
            return [r.payload for r in conn.execute(query)]

    # ── Read ─────────────────────────────────────────────────────────────────────

    def find_by_id(self, delivery_id: str) -> dict | None:
        rows = self._read(select(news_deliveries).where(news_deliveries.c.id == delivery_id))
        return rows[0] if rows else None

    def with_status(self, statuses: Iterable[str]) -> list[dict]:
        """Rows in any of ``statuses``, oldest first."""
        return self._read(
            select(news_deliveries)
            .where(news_deliveries.c.status.in_(list(statuses)))
            .order_by(news_deliveries.c.seq)
        )

    def told_since(self, since_iso: str) -> list[dict]:
        """Rows told at or after ``since_iso`` (ISO, UTC), oldest first."""
        return self._read(
            select(news_deliveries)
            .where(news_deliveries.c.status == "told", news_deliveries.c.told_at >= since_iso)
            .order_by(news_deliveries.c.told_at, news_deliveries.c.seq)
        )

    def last_told_at(self) -> str | None:
        with self._eng().connect() as conn:
            return conn.execute(
                select(func.max(news_deliveries.c.told_at)).where(
                    news_deliveries.c.status == "told"
                )
            ).scalar_one()

    # ── Write (the choke — services.news_delivery.apply_delivery_change) ───────────

    def add(self, row: dict) -> tuple[dict, list[str]]:
        """Insert a new row, superseding any pending or waiting row of the same non-empty
        ``story_id`` AND ``source`` in the same transaction (a crash-watch alert and the analysis
        that follows it share a story id and must not replace each other). Returns (row,
        superseded ids)."""
        story_id = row.get("story_id") or ""
        superseded: list[str] = []
        with self._write() as conn:
            if story_id:
                stale = conn.execute(
                    select(news_deliveries).where(
                        news_deliveries.c.story_id == story_id,
                        news_deliveries.c.source == row["source"],
                        news_deliveries.c.status.in_(("pending", "waiting")),
                    )
                ).all()
                for old in stale:
                    payload = {**old.payload, "status": "superseded", "superseded_by": row["id"]}
                    conn.execute(
                        sa_update(news_deliveries)
                        .where(news_deliveries.c.id == old.id)
                        .values(status="superseded", payload=payload)
                    )
                    superseded.append(old.id)
            seq = conn.execute(
                select(func.coalesce(func.max(news_deliveries.c.seq), -1))
            ).scalar_one()
            conn.execute(sa_insert(news_deliveries).values(**_columns(seq + 1, row)))
        return row, superseded

    def transition(
        self,
        delivery_id: str,
        *,
        from_statuses: Iterable[str],
        changes: dict,
        undecided_only: bool = False,
    ) -> dict | None:
        """Apply ``changes`` to the row only while its status is in ``from_statuses`` (and, with
        ``undecided_only``, while it has no decision) — None when the row is missing or already
        past them (a second call to tell or decide the same item lands here, and does nothing)."""
        with self._write() as conn:
            current = conn.execute(
                select(news_deliveries).where(news_deliveries.c.id == delivery_id)
            ).first()
            if current is None or current.status not in set(from_statuses):
                return None
            if undecided_only and current.decision is not None:
                return None
            merged = {**current.payload, **changes}
            values = _columns(current.seq, merged)
            values.pop("seq")
            conn.execute(
                sa_update(news_deliveries)
                .where(news_deliveries.c.id == delivery_id)
                .values(**values)
            )
            return merged

    def count_unfinished(self) -> int:
        with self._eng().connect() as conn:
            return conn.execute(
                select(func.count())
                .select_from(news_deliveries)
                .where(news_deliveries.c.status.in_(("pending", "waiting")))
            ).scalar_one()

    def expire(
        self,
        now_iso: str,
        *,
        created_before: str | None = None,
        user_set_undecided_before: str | None = None,
        everything: bool = False,
    ) -> list[str]:
        """Pending or waiting rows become ``expired``: all of them with ``everything``, those
        created before ``created_before``, and user-set rows still undecided past
        ``user_set_undecided_before`` (a stale alert is never sent as if it were fresh)."""
        expired: list[str] = []
        with self._write() as conn:
            unfinished = conn.execute(
                select(news_deliveries).where(news_deliveries.c.status.in_(("pending", "waiting")))
            ).all()
            for row in unfinished:
                stale_user_set = (
                    user_set_undecided_before is not None
                    and row.user_set
                    and row.decision is None
                    and row.created_at < user_set_undecided_before
                )
                old = created_before is not None and row.created_at < created_before
                if not (everything or old or stale_user_set):
                    continue
                payload = {**row.payload, "status": "expired", "expired_at": now_iso}
                conn.execute(
                    sa_update(news_deliveries)
                    .where(news_deliveries.c.id == row.id)
                    .values(status="expired", payload=payload)
                )
                expired.append(row.id)
        return expired


class NewsDeliveryStateStore(NamespacedSettingsStore):
    """The roundup bookkeeping (``last_roundup_at``, ``revisit_at``): one runtime-state row,
    checkpoint-excluded like the table beside it."""

    def __init__(self, engine=None):
        super().__init__("news_delivery_state", table=runtime_state, engine=engine)
