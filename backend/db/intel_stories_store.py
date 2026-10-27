"""IntelStoriesStore — the analyst-pipeline story-arc list (RFC intel-signals-data-model,
"intel_signals -> intel_stories + signal_timeline_entries + daily_digests").

DB-backed (tables ``intel_stories`` + ``signal_timeline_entries``), promoted query-surface
columns + a verbatim ``payload`` — the ``accounts``/``evidence`` shape (real per-type extra
fields, e.g. ``risk`` rows carry ``status_reason`` with no equivalent on any other type), NOT
a plain ``PayloadTableStore`` (that shape is bare seq/id/payload only). Renamed from
``IntelSignalsStore``/``intel_signals`` — every row here IS a story now (the old
``daily_digest`` shape moved to ``DailyDigestStore``/``daily_digests``), so the old
``is_story_row`` multi-shape filter no longer applies: every row this store returns already
satisfies it.

Timeline entries (``signal_timeline_entries``) are owned DIRECTLY by this class — mirrors how
``TransactionStore`` owns ``splits``/``external_refs`` internally with no separate store
class, not a new pattern (RFC §10 task 1). Every write method takes an optional ``conn=`` for
composition under ``backend.db.engine.locked_unit_of_work`` — ``conn=None`` (the default)
takes this store's OWN lock (shared across both tables, matching ``TransactionStore``'s
one-lock-for-parent-plus-children shape) and opens its own transaction; ``conn=<a connection>``
runs straight against it, no lock of its own — see ``PayloadTableStore``'s CONN= COMPOSITION
docstring for the full contract this mirrors. The choke point is
``services.intel_stories.apply_story_change`` — a caller should reach these methods only
through it going forward.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import Connection, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import intel_stories, signal_timeline_entries

_STORY_PROMOTED_DEFAULTS: dict[str, Any] = {
    "ticker": None,
    "instrument_id": None,
    "current_summary": "",
    "portfolio_impact": "",
    "action_items": [],
    "affected_holdings": None,
    "resolved_reason": None,
    "evidence_watermark": None,
    "created_at": "",
    "updated_at": "",
}
_STORY_BOOL_FIELDS = ("analyzed", "read", "dismissed", "resolved", "archived")


def _story_insert_values(seq: int, story: dict) -> dict:
    """Column dict for one ``intel_stories`` row. Promoted columns are DERIVED from
    ``story`` (never the other way — ``payload`` is the round-trip source of truth).
    ``id``/``type``/``label``/``status`` carry no default: a story missing one of those
    hits the table's own NOT NULL/CHECK constraint, a loud failure — same posture as an
    id-less ``PayloadTableStore`` insert (see that module's docstring)."""
    row: dict = {
        "seq": seq,
        "id": story.get("id"),
        "type": story.get("type"),
        "label": story.get("label"),
        "status": story.get("status"),
        "payload": story,
    }
    for field, default in _STORY_PROMOTED_DEFAULTS.items():
        row[field] = story.get(field) if story.get(field) is not None else default
    for field in _STORY_BOOL_FIELDS:
        row[field] = bool(story.get(field))
    return row


_ENTRY_DEFAULTS: dict[str, Any] = {
    "date": "",
    "title": "",
    "summary": "",
    "sources": [],
    "supersedes": None,
    "contradicts": None,
    "status": None,
    "confirmed_at": None,
}


def _entry_insert_values(signal_id: str, entry: dict) -> dict:
    """Column dict for one ``signal_timeline_entries`` row. ``seq`` is DB-assigned
    (autoincrement rowid alias) — never supplied here. ``id`` is the caller-minted
    ``uuid4().hex`` business identity (analyst.py's existing convention, unchanged);
    this function does not mint one."""
    row: dict = {
        "id": entry.get("id"),
        "signal_id": signal_id,
        "superseded": bool(entry.get("superseded")),
        "payload": entry,
    }
    for field, default in _ENTRY_DEFAULTS.items():
        row[field] = entry.get(field) if entry.get(field) is not None else default
    return row


class IntelStoriesStore:
    def __init__(self, engine=None):
        self._engine = engine
        self._lock = _lock_for_table(intel_stories.name)

    def _eng(self):
        """``engine=`` given at construction (test injection) is honored permanently.
        The DEFAULT is never cached — resolved fresh via ``get_engine()`` on every call
        (the fix every long-lived module-singleton store in this codebase carries — see
        ``PayloadTableStore._eng``'s docstring for the bug this avoids)."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``PayloadTableStore._use_conn`` — see its CONN= COMPOSITION docstring
        for the full contract. ONE lock covers both ``intel_stories`` and
        ``signal_timeline_entries`` for a standalone (``conn=None``) call — this store
        always writes both tables together (a strict superset of
        ``locked_unit_of_work(intel_stories, signal_timeline_entries)``'s own two locks),
        matching ``TransactionStore``'s one-lock-for-parent-plus-children shape."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    def _story_row(self, conn: Connection, id_val: str) -> dict | None:
        row = conn.execute(select(intel_stories).where(intel_stories.c.id == id_val)).first()
        return dict(row._mapping) if row else None

    def _fire_story_hook(self, conn: Connection, rows: list[tuple[Any, dict | None]]) -> None:
        """``Any`` (not ``str``) for the pk slot — mirrors ``PayloadTableStore._fire_hook``'s
        own typing: it comes straight from ``story.get("id")``, not guaranteed non-None if a
        caller inserts an item missing its id field (that reaches the DB as a NOT NULL/PK
        violation, a loud failure)."""
        if rows:
            fire_db_write_hook(intel_stories.name, rows, conn=conn)

    def _fire_entry_hook(self, conn: Connection, rows: list[tuple[Any, dict | None]]) -> None:
        if rows:
            fire_db_write_hook(signal_timeline_entries.name, rows, conn=conn)

    def _next_seq(self, conn: Connection) -> int:
        return (
            conn.execute(select(func.coalesce(func.max(intel_stories.c.seq), -1))).scalar_one() + 1
        )

    # ── Story CRUD ────────────────────────────────────────────────────────────────

    def all(self, *, conn: Connection | None = None) -> list[dict]:
        if conn is not None:
            return [
                r.payload for r in conn.execute(select(intel_stories).order_by(intel_stories.c.seq))
            ]
        with self._eng().connect() as owned_conn:
            return [
                r.payload
                for r in owned_conn.execute(select(intel_stories).order_by(intel_stories.c.seq))
            ]

    def find_by_id(self, id_val: str, *, conn: Connection | None = None) -> dict | None:
        if conn is not None:
            row = self._story_row(conn, id_val)
            return row["payload"] if row else None
        with self._eng().connect() as owned_conn:
            row = self._story_row(owned_conn, id_val)
        return row["payload"] if row else None

    def query(self, **filters: Any) -> list[dict]:
        items = self.all()
        for key, value in filters.items():
            if value is None:
                continue
            items = [i for i in items if i.get(key) == value]
        return items

    def find_story_by_ticker(self, ticker: str) -> dict | None:
        """Case-insensitive ticker scan — every row in this store IS a story now (the old
        multi-shape ``intel_signals`` table's ``is_story_row`` filter has no successor here,
        RFC intel-signals-data-model). The only caller is ``intel_agent._persist_story``'s
        existing-story lookup. Macro/sector/geography stories store ``ticker`` as None — the
        key exists, so ``.get``'s default never applies."""
        ticker_up = ticker.upper()
        for story in self.all():
            if (story.get("ticker") or "").upper() == ticker_up:
                return story
        return None

    def find_story_by_label(self, label: str) -> dict | None:
        """Label counterpart to ``find_story_by_ticker`` — same story-row scan."""
        label_lo = label.lower()
        for story in self.all():
            if (story.get("label") or "").lower() == label_lo:
                return story
        return None

    def insert(self, story: dict, *, conn: Connection | None = None) -> dict:
        id_val = story.get("id")
        with self._use_conn(conn) as c:
            self._fire_story_hook(c, [(id_val, None)])
            seq = self._next_seq(c)
            c.execute(sa_insert(intel_stories).values(**_story_insert_values(seq, story)))
        return story

    def upsert(self, story: dict, *, conn: Connection | None = None) -> dict:
        """Insert or replace by id. A replace KEEPS the row's seq (matches
        ``PayloadTableStore.upsert``'s in-place-replace convention)."""
        id_val = story.get("id")
        with self._use_conn(conn) as c:
            existing = self._story_row(c, id_val)
            self._fire_story_hook(c, [(id_val, existing)])
            if existing is not None:
                values = _story_insert_values(existing["seq"], story)
                values.pop("seq")
                c.execute(
                    sa_update(intel_stories).where(intel_stories.c.id == id_val).values(**values)
                )
            else:
                seq = self._next_seq(c)
                c.execute(sa_insert(intel_stories).values(**_story_insert_values(seq, story)))
        return story

    def update_by_id(
        self, id_val: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        with self._use_conn(conn) as c:
            existing = self._story_row(c, id_val)
            if existing is None:
                return None
            merged = {**existing["payload"], **changes}
            self._fire_story_hook(c, [(id_val, existing)])
            values = _story_insert_values(existing["seq"], merged)
            values.pop("seq")
            c.execute(sa_update(intel_stories).where(intel_stories.c.id == id_val).values(**values))
        return merged

    def delete(self, id_val: str, *, conn: Connection | None = None) -> bool:
        with self._use_conn(conn) as c:
            existing = self._story_row(c, id_val)
            if existing is None:
                return False
            self._fire_story_hook(c, [(id_val, existing)])
            # ON DELETE CASCADE takes signal_timeline_entries with it.
            c.execute(sa_delete(intel_stories).where(intel_stories.c.id == id_val))
        return True

    # ── Timeline entries (owned directly — mirrors TransactionStore/splits) ────────

    def entries_for_signal(
        self, signal_id: str, *, limit: int | None = None, conn: Connection | None = None
    ) -> list[dict]:
        """Entries for ``signal_id``, NEWEST-FIRST (``seq DESC``) — matches the old
        prepend-ordered ``timeline[]`` array's wire shape (``StoryArcCard.tsx``,
        ``ai/tools/asset_context.py::_timeline_entry_view`` both expect newest-first).
        ``limit``, when given, caps to the N most recent — the bounded-read shape
        ``_bounded_timeline_view`` embeds on the list feed."""
        q = (
            select(signal_timeline_entries)
            .where(signal_timeline_entries.c.signal_id == signal_id)
            .order_by(signal_timeline_entries.c.seq.desc())
        )
        if limit is not None:
            q = q.limit(limit)
        if conn is not None:
            return [r.payload for r in conn.execute(q)]
        with self._eng().connect() as owned_conn:
            return [r.payload for r in owned_conn.execute(q)]

    def count_entries_for_signal(self, signal_id: str, *, conn: Connection | None = None) -> int:
        q = (
            select(func.count())
            .select_from(signal_timeline_entries)
            .where(signal_timeline_entries.c.signal_id == signal_id)
        )
        if conn is not None:
            return conn.execute(q).scalar_one()
        with self._eng().connect() as owned_conn:
            return owned_conn.execute(q).scalar_one()

    def append_timeline_entry(
        self, signal_id: str, entry: dict, *, conn: Connection | None = None
    ) -> dict:
        """Insert one new timeline entry row. ``entry`` must already carry its own
        ``id`` (``uuid4().hex``, minted by the caller — analyst.py's existing
        convention, unchanged); this method does not mint one. The row's DB-assigned
        ``seq`` is not known until after the INSERT, so the checkpoint before-image
        fires AFTER it (still inside the same open transaction — atomicity only
        requires ONE transaction, not a literal statement order; see
        ``core.io.fire_db_write_hook``'s TRANSACTION CONTRACT)."""
        with self._use_conn(conn) as c:
            result = c.execute(
                sa_insert(signal_timeline_entries).values(**_entry_insert_values(signal_id, entry))
            )
            seq = result.inserted_primary_key[0]
            self._fire_entry_hook(c, [(str(seq), None)])
        return entry

    def mark_entry_superseded(
        self, signal_id: str, entry_id: str, *, conn: Connection | None = None
    ) -> bool:
        """Flip ONE entry's ``superseded`` flag (ADR
        2026-08-28-sourced-and-corroborated-intel-signal-updates: a later entry
        CONFIRMED-reversed this one). Scoped to ``signal_id`` — entry ids are globally
        unique (``uuid4().hex``) but this mirrors ``_find_timeline_entry``'s own
        one-signal-at-a-time scope (RFC §2.2), never a bare cross-signal id lookup.
        Returns False if no such entry exists under this signal."""
        with self._use_conn(conn) as c:
            row = c.execute(
                select(signal_timeline_entries).where(
                    signal_timeline_entries.c.signal_id == signal_id,
                    signal_timeline_entries.c.id == entry_id,
                )
            ).first()
            if row is None:
                return False
            existing = dict(row._mapping)
            merged_payload = {**existing["payload"], "superseded": True}
            self._fire_entry_hook(c, [(str(existing["seq"]), existing)])
            c.execute(
                sa_update(signal_timeline_entries)
                .where(signal_timeline_entries.c.seq == existing["seq"])
                .values(superseded=1, payload=merged_payload)
            )
        return True

    def touch_entry(self, signal_id: str, entry_id: str, *, conn: Connection | None = None) -> bool:
        """Bump ONE entry's own ``as_of`` (payload-only — not a promoted column) to now.
        Mirrors ``mark_entry_superseded``'s shape: ``analyst._update_signal``'s
        duplicate-match branch uses this to record that a repeat mention was reconfirmed
        by a fresh evidence check, without creating a new development or touching anything
        else about the entry. Scoped to ``signal_id`` — same one-signal-at-a-time posture
        as ``mark_entry_superseded``. Returns False if no such entry exists under this
        signal."""
        with self._use_conn(conn) as c:
            row = c.execute(
                select(signal_timeline_entries).where(
                    signal_timeline_entries.c.signal_id == signal_id,
                    signal_timeline_entries.c.id == entry_id,
                )
            ).first()
            if row is None:
                return False
            existing = dict(row._mapping)
            merged_payload = {
                **existing["payload"],
                "as_of": datetime.now(timezone.utc).isoformat(),
            }
            self._fire_entry_hook(c, [(str(existing["seq"]), existing)])
            c.execute(
                sa_update(signal_timeline_entries)
                .where(signal_timeline_entries.c.seq == existing["seq"])
                .values(payload=merged_payload)
            )
        return True
