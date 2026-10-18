"""SessionStore — server-side persistence for chat sessions (Wave 5E, JSON->DB).

Row shape (table ``sessions``, backend/db/tables.py): ONE row per session, replacing the
four pre-migration file shapes under ``SESSIONS_DIR`` (transcript, ``.fold.json``,
``.proactive.json`` sidecars, and the shared ``index.json`` metadata list) — see that
table's own docstring for the full column-by-column reasoning. ``backend/ai/assistant.py``
is the only caller; its "Disk persistence"/"fold sidecar"/"proactive sidecar"/"session
index" helper functions keep their exact pre-migration names and signatures and now call
through here instead of touching files.

Hand-rolled (not a ``PayloadTableStore`` subclass): a session row carries FIVE independent
JSON columns (``meta``/``transcript``/``fold``/``proactive``/``extraction_cursor``) plus
four promoted query columns, not the generic single-``payload`` shape. ``_eng``/
``_use_conn``/``_lock`` mirror
``ProfileStore``'s plumbing exactly (same lazy-engine-resolution reasoning — every one of
these methods may be called from a request handler with no engine override, so the DEFAULT
is re-resolved on every call, never cached).

NEVER FIRES ``core.io.fire_db_write_hook``: ``sessions`` is checkpoint-EXCLUDED
(``ai.checkpoint._policy._EXCLUDED_TABLES`` — a chat transcript must survive a /rewind), and
unlike every other store in this migration this table's rows can run several MEGABYTES
(the real instance this migration absorbed had a 3.1MB transcript) and are rewritten
several times per single chat turn — computing a before-image nobody will ever look at,
on every one of those writes, is pure waste. Mirrors ``checkpoints``' own
``checkpointed=False`` + ``_EXCLUDED_TABLES`` "belt and braces" precedent (both layers say
never), not a new pattern. The ONE-TIME cutover absorb (``db/session_io.py``,
``import_session_row``) still fires the hook unconditionally, matching every other wave's
absorb — that call is not on a hot path, so it follows the codebase's uniform convention.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from sqlalchemy import Connection, Engine, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.errors import DataIntegrityError
from backend.db.engine import _lock_for_table
from backend.db.tables import session_messages, sessions

logger = logging.getLogger(__name__)

_UNSET = object()  # "leave this column alone" — distinct from a real None (e.g. clearing fold)

# The message fields that get their own column. Everything else rides in `extra`.
_PROMOTED_MESSAGE_KEYS = frozenset({"role", "content", "ts"})


def _iso_from_ts(value) -> Optional[str]:
    """Epoch-ms (the frontend's wire unit) -> the ISO-8601 UTC the columns store."""
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def _ts_from_iso(value) -> Optional[int]:
    """The stored ISO-8601 -> epoch-ms, for the frontend's `ts` field."""
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value)).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _now_ms() -> int:
    """Epoch milliseconds — the per-message stamp's unit, matching what
    `assistant._stamp_message_times` has always written."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _promoted_columns(session_id: str, meta: dict) -> dict:
    """The four query-surface columns, derived from ``meta`` — see ``db/tables.py``'s own
    ``sessions`` comment. Defensive: a real session in this repo's own data omits
    ``source``/``extra_tool_groups``/etc., and `title` defaults exactly like the
    pre-migration ``upsert_session_meta``'s own insert branch ("New conversation")."""
    return {
        "title": meta.get("title") or "New conversation",
        "created_at": meta.get("created_at") or "",
        "updated_at": meta.get("updated_at") or "",
        "message_count": int(meta.get("message_count") or 0),
    }


class SessionStore:
    """Persists one row per chat session — see module docstring for the shape."""

    def __init__(self, engine: Optional[Engine] = None):
        self._table = sessions
        self._engine = engine
        self._lock = _lock_for_table(sessions.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently; the
        DEFAULT (``engine=None``) is resolved fresh via ``get_engine()`` on every call,
        never cached — mirrors ``ProfileStore``/``PayloadTableStore`` for the same
        module-singleton-safety reason (a later ``db_isolated``-style redirect must not be
        silently ignored by a pinned reference)."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Optional[Connection]) -> Iterator[Connection]:
        """Mirrors ``ProfileStore._use_conn`` exactly: a caller-supplied ``conn`` composes
        into an outer unit-of-work; ``None`` opens an owned, lock-held transaction. No
        public method here accepts ``conn=`` today (session persistence is never composed
        into a money/planning choke) — kept private, ready if that ever changes."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    def _row(self, conn: Connection, session_id: str) -> Optional[dict]:
        row = conn.execute(select(self._table).where(self._table.c.id == session_id)).first()
        return dict(row._mapping) if row else None

    def _upsert(
        self,
        conn: Connection,
        session_id: str,
        *,
        meta_patch: Optional[dict] = None,
        fold: Any = _UNSET,
        extraction_cursor: Any = _UNSET,
    ) -> dict:
        """The one RMW core every write method below composes: merge ``meta_patch`` onto
        the existing (or freshly-defaulted) meta dict, set whichever of
        fold/extraction_cursor the caller actually passed (``_UNSET``
        = leave that column alone), and INSERT or UPDATE the row in one statement. Returns
        the final meta dict written. The default-new-row shape mirrors the pre-migration
        ``upsert_session_meta``'s own insert branch (title "New conversation",
        ``created_at``/``updated_at`` = now, ``message_count`` 0, ``model`` ""), plus
        ``permission_mode: "auto"`` — a conversation that has never been persisted before is
        exactly a brand-new one, so this is the one place that default is written explicitly
        rather than left to ``conversation_approval``'s row-absent fallback (see that
        module's ``get_mode``, which mirrors this same "no row yet" case for the read that
        happens before the first message ever creates one)."""
        existing = self._row(conn, session_id)
        if existing is not None:
            meta = dict(existing["meta"] or {})
        else:
            now = _now()
            meta = {
                "id": session_id,
                "title": "New conversation",
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
                "model": "",
                "permission_mode": "auto",
            }
        if meta_patch:
            meta.update(meta_patch)
        values: dict = {**_promoted_columns(session_id, meta), "meta": meta}
        if fold is not _UNSET:
            values["fold"] = fold
        if extraction_cursor is not _UNSET:
            values["extraction_cursor"] = extraction_cursor
        if existing is not None:
            conn.execute(
                sa_update(self._table).where(self._table.c.id == session_id).values(**values)
            )
        else:
            values.setdefault("fold", None)
            values.setdefault("extraction_cursor", None)
            conn.execute(sa_insert(self._table).values(id=session_id, **values))
        return meta

    # ── Transcript ────────────────────────────────────────────────────────────────

    def get_transcript(self, session_id: str) -> Optional[list]:
        """The conversation as a message list, or ``None`` if no session row exists.

        Kept under the pre-0051 name and shape so all ten call sites are unchanged by the
        move off ``sessions.transcript``: this now reads ``session_messages``. The
        ``None`` vs ``[]`` distinction is load-bearing — ``None`` means "no such session",
        ``[]`` means "a session with nothing said yet" — so the ROW is still what decides.
        """
        with self._eng().connect() as conn:
            exists = self._row(conn, session_id) is not None
        if not exists:
            return None
        return self.get_messages(session_id)

    def set_transcript(self, session_id: str, transcript: list) -> None:
        """Reconcile the stored messages to ``transcript`` — append-only in the common case.

        The caller still hands over the whole in-memory list (that is what a turn holds),
        but this no longer rewrites a 3 MB JSON cell to add two messages. It finds the first
        index where stored and incoming diverge, truncates from there, and inserts only the
        tail. A pure append — the overwhelmingly common case — writes exactly the new rows.

        The diff is element-wise rather than "append everything past the old length" because
        a turn genuinely does mutate entries in place: ``_abort_stale_pending`` rewrites a
        dangling ``tool_use``, and the attachment path swaps a resolved message back to its
        ref-shaped durable form. Appending blindly past the old length would persist neither.
        """
        if not isinstance(transcript, list) or any(not isinstance(m, dict) for m in transcript):
            # Fail CLOSED at the write. Pre-0051 this column took anything, so a malformed
            # transcript was stored happily and only caught on the next READ
            # (`assistant.load_session_durable`). Rows cannot represent that shape at all,
            # so the check moves here — earlier, and on the side that actually knows.
            raise DataIntegrityError(
                f"session {session_id!r}: transcript is not a JSON array of message objects"
            )
        # The session ROW first: messages carry a FK to it since revision 0053, so
        # appending before it exists is a constraint violation. (Pre-0053 there was no FK
        # and the order did not matter — exactly the kind of latent ordering assumption a
        # foreign key exists to surface.)
        now = _now()
        with self._use_conn(None) as conn:
            self._upsert(
                conn,
                session_id,
                meta_patch={"updated_at": now, "message_count": len(transcript)},
            )

        stored = self.get_messages(session_id)
        n = min(len(stored), len(transcript))
        first_diff = n
        for i in range(n):
            # Whole-dict comparison: a change to any carried key (the skill host's
            # included) has to re-persist that row, not just a change to the promoted three.
            #
            # ONE field is compared leniently: a stored message always has a `ts` (stamped
            # at INSERT since 0053) while the caller's in-memory copy may not. Treating that
            # as a difference would make EVERY save diverge at index 0 and rewrite the whole
            # conversation — silently undoing the entire point of the migration.
            a, b = stored[i], transcript[i]
            if "ts" not in b:
                a = {k: v for k, v in a.items() if k != "ts"}
            if a != b:
                first_diff = i
                break
        if first_diff < len(stored):
            self.truncate_messages_from(session_id, first_diff)
        self.append_messages(session_id, transcript[first_diff:])

    # ── Messages (revision 0051) ─────────────────────────────────────────────────
    # The append-only substrate the two methods above are built on. `seq` is 0-based and
    # equals what used to be the list index, so every positional consumer (failed-turn
    # rollback, checkpoint anchor, fold window) maps across unchanged. See `db/tables.py`.

    def append_messages(self, session_id: str, messages: list) -> None:
        """INSERT ``messages`` at the end of the conversation.

        A plain INSERT: the database assigns ``id``. No read of the current maximum, and no
        dependence on a lock — which is what makes two writers appending to one session
        (a running turn and an arriving alert) safe even across processes.
        """
        if not messages:
            return
        rows = [
            {
                "session_id": session_id,
                "role": str(m.get("role") or ""),
                "content": m.get("content"),
                # Stamped at INSERT. Ordering is `id`'s job (times tie inside one turn and
                # can move backwards across a clock correction); this is the honest record
                # of when the message came into being. A caller-supplied `ts` (epoch ms,
                # the frontend's wire field) is honoured so a rehydrated message keeps its
                # original time rather than gaining today's.
                "created_at": _iso_from_ts(m.get("ts")) or _now(),
                "updated_at": _iso_from_ts(m.get("ts")) or _now(),
                "extra": {k: v for k, v in m.items() if k not in _PROMOTED_MESSAGE_KEYS} or None,
            }
            for m in messages
        ]
        with self._use_conn(None) as conn:
            conn.execute(sa_insert(session_messages), rows)
            self._stamp_transcript_rev(conn, session_id)

    def get_messages(
        self, session_id: str, *, start: Optional[int] = None, end: Optional[int] = None
    ) -> list:
        """The conversation as ``[{role, content, ts, …}, …]`` in order.

        ``start``/``end`` are POSITIONS (half-open, like a Python slice), sliced
        SERVER-SIDE — the point of the migration's read half: ``?tail=30`` used to
        rehydrate the entire transcript and slice afterwards. Positions are resolved with
        OFFSET/LIMIT against the ``id`` ordering, never by trusting ``id`` to BE the
        position (it is an ordering key; gaps are normal after a rollback).
        """
        q = (
            select(
                session_messages.c.role,
                session_messages.c.content,
                session_messages.c.created_at,
                session_messages.c.extra,
            )
            .where(session_messages.c.session_id == session_id)
            .order_by(session_messages.c.id)
        )
        if start:
            q = q.offset(start)
        if end is not None:
            q = q.limit(max(0, end - (start or 0)))
        with self._eng().connect() as conn:
            rows = conn.execute(q).fetchall()
        out = []
        for r in rows:
            m = {"role": r.role, "content": r.content}
            # The frontend's field is `ts` (epoch ms) — mapped here, so the wire contract is
            # unchanged by the column rename.
            stamp = _ts_from_iso(r.created_at)
            if stamp is not None:
                m["ts"] = stamp
            if r.extra:
                m.update(r.extra)
            out.append(m)
        return out

    def truncate_messages_from(self, session_id: str, position: int) -> None:
        """Drop every message from ``position`` onward.

        The append-only twin of ``del history[n:]`` — a failed turn's rollback and
        ``/rewind``'s restore-to-anchor are the same operation here. ``position`` is
        resolved to an ``id`` first: ids order the conversation but are not positions, so
        deleting ``WHERE id >= position`` would delete the wrong rows the moment any
        session has a gap.
        """
        position = max(0, int(position))
        with self._use_conn(None) as conn:
            cutoff = conn.execute(
                select(session_messages.c.id)
                .where(session_messages.c.session_id == session_id)
                .order_by(session_messages.c.id)
                .offset(position)
                .limit(1)
            ).scalar()
            if cutoff is None:
                return  # nothing at or after that position
            conn.execute(
                sa_delete(session_messages).where(
                    (session_messages.c.session_id == session_id)
                    & (session_messages.c.id >= cutoff)
                )
            )
            self._stamp_transcript_rev(conn, session_id)

    def message_count(self, session_id: str) -> int:
        with self._eng().connect() as conn:
            n = conn.execute(
                select(sa_func.count())
                .select_from(session_messages)
                .where(session_messages.c.session_id == session_id)
            ).scalar()
        return int(n or 0)

    def _stamp_transcript_rev(self, conn: Connection, session_id: str) -> None:
        """A fresh token on the session row, in the writer's own transaction. A plain UPDATE
        — no read-modify-write — so concurrent appenders can only ever leave a token that no
        reader has seen yet, never one a reader already holds."""
        conn.execute(
            sa_update(self._table)
            .where(self._table.c.id == session_id)
            .values(transcript_rev=uuid.uuid4().hex)
        )

    def transcript_revision(self, session_id: str) -> str:
        """The token of the last message write, or ``""`` for a session never written since
        revision 202610071700 (callers treat that as unknown). Ids can NOT stand in for it:
        SQLite reuses the id of a deleted newest row, so a truncate + re-append can leave
        count and max id unchanged."""
        with self._eng().connect() as conn:
            rev = conn.execute(
                select(self._table.c.transcript_rev).where(self._table.c.id == session_id)
            ).scalar()
        return str(rev or "")

    def delete(self, session_id: str) -> None:
        """Drop the WHOLE row (transcript+fold+proactive+meta at once) — replaces the old
        four independent file deletes (transcript/fold/proactive/index-entry) with one
        atomic DELETE. Best-effort, matching every one of those four's individually
        swallowed exceptions."""
        try:
            with self._use_conn(None) as conn:
                # Messages live in their own table since revision 0051, so dropping the
                # session row no longer takes them with it — they would be orphans keyed to
                # an id nothing references.
                conn.execute(
                    sa_delete(session_messages).where(session_messages.c.session_id == session_id)
                )
                conn.execute(sa_delete(self._table).where(self._table.c.id == session_id))
        except Exception:
            pass

    # ── Context-fold sidecar (RFC D) ─────────────────────────────────────────────

    def get_fold(self, session_id: str) -> Optional[dict]:
        with self._eng().connect() as conn:
            row = self._row(conn, session_id)
        return row["fold"] if row is not None else None

    def set_fold(self, session_id: str, data: dict) -> None:
        with self._use_conn(None) as conn:
            self._upsert(conn, session_id, fold=data)

    def clear_fold(self, session_id: str) -> None:
        """No-op if the row doesn't exist — matches the old ``unlink(missing_ok=True)``."""
        with self._use_conn(None) as conn:
            if self._row(conn, session_id) is None:
                return
            conn.execute(
                sa_update(self._table).where(self._table.c.id == session_id).values(fold=None)
            )

    # ── Proactive-message sidecar ────────────────────────────────────────────────

    def get_extraction_cursor(self, session_id: str) -> Optional[dict]:
        """The raw ``extraction_cursor`` column value (``{"last_extracted_len": N}``), or
        ``None`` if the row doesn't exist OR no cursor has been saved yet — ``ai/
        memory.py`` treats both the same way (falls back to ``last_extracted_len=0``)."""
        with self._eng().connect() as conn:
            row = self._row(conn, session_id)
        return row["extraction_cursor"] if row is not None else None

    def set_extraction_cursor(self, session_id: str, data: dict) -> bool:
        """Persist the extraction cursor onto an EXISTING row; returns whether one was there.

        Never inserts: extraction runs as a background task that can finish after the
        session was deleted (a capture turn clears its throwaway session at once), and an
        upsert here resurrected it as an empty "New conversation" in the history list.
        Raises on failure, like ``set_fold``/``set_transcript`` — ``ai/memory.py``'s own
        ``_save_cursor`` is the best-effort layer, not this one."""
        with self._use_conn(None) as conn:
            if self._row(conn, session_id) is None:
                return False
            self._upsert(conn, session_id, extraction_cursor=data)
            return True

    # ── Session metadata (was index.json) ────────────────────────────────────────

    def get_meta(self, session_id: str) -> Optional[dict]:
        with self._eng().connect() as conn:
            row = self._row(conn, session_id)
        return row["meta"] if row is not None else None

    def all_meta(self) -> list[dict]:
        """Every session's meta dict, UNFILTERED (includes internal/scheduled sessions —
        callers that must hide those, e.g. ``assistant.list_sessions``, filter
        afterward, exactly as they did over ``_read_index()``'s full list). Ordered by
        ``id`` for determinism; nothing depends on insertion order (see ``db/tables.py``'s
        own comment on why this table carries no ``seq`` column).

        TOLERANT of a single corrupt row (parses ``meta`` itself from raw TEXT rather
        than letting SQLAlchemy's typed ``JSON`` column deserialize it) — mirrors
        ``db.session_io.export_all_sessions``'s own precedent and, more importantly,
        its own empirically-confirmed reason: catching the deserializer's exception
        around a plain ``conn.execute(select(...))`` iteration does NOT let iteration
        continue past the bad row (the cursor result raises ``StopIteration`` on the
        very next call instead), silently truncating this list to whatever sorted
        before the corrupt session — and this method backs ``assistant.list_sessions``,
        the conversation list every page load reads, so one corrupt session must not
        be able to hide every OTHER session behind it."""
        with self._eng().connect() as conn:
            rows = conn.exec_driver_sql("SELECT id, meta FROM sessions ORDER BY id")
            out: list[dict] = []
            for session_id, meta_raw in rows:
                try:
                    out.append(json.loads(meta_raw))
                except Exception:
                    logger.warning(
                        "sessions bulk meta read: skipping session %r — meta failed to parse",
                        session_id,
                        exc_info=True,
                    )
            return out

    def upsert_meta(self, session_id: str, patch: dict, *, fail_loud: bool = False) -> dict:
        """Merge ``patch`` onto the session's existing (or freshly-defaulted) meta dict
        and persist it — the store-level primitive behind ``assistant.upsert_session_meta``,
        which owns all the field-by-field business rules (which kwarg maps to which key,
        the ``last_turn_status``->``last_turn_at`` side effect) and passes the fully-formed
        patch dict straight through. ``fail_loud=False`` (the default) swallows any
        exception — mirrors ``_write_index``'s old contract: "Ordinary Chat listing
        metadata retains its historical best-effort behavior." Returns ``{}`` on a
        swallowed failure (the caller never inspected the return value pre-migration
        either — ``upsert_session_meta`` returns ``None``)."""
        try:
            with self._use_conn(None) as conn:
                return self._upsert(conn, session_id, meta_patch=patch)
        except Exception:
            if fail_loud:
                raise
            return {}

    def mark_read(self, session_ids: list[str], *, at: str) -> dict[str, str]:
        """Record that a person has read these conversations up to ``at`` and return the
        ``{id: last_read_at}`` actually stored.

        Only EXISTING rows are touched — an id that was never persisted (a still-unsent
        draft, a deleted conversation) is skipped rather than created, so marking a draft
        read can never mint a ghost session. The stamp only moves FORWARD: two clients
        marking the same conversation out of order must not un-read it. ``updated_at`` is
        deliberately left alone — reading is not activity, and that field sorts the list.
        """
        stored: dict[str, str] = {}
        with self._use_conn(None) as conn:
            for session_id in dict.fromkeys(session_ids):
                existing = self._row(conn, session_id)
                if existing is None:
                    continue
                current = (existing["meta"] or {}).get("last_read_at") or ""
                stamp = max(current, at)
                if stamp != current:
                    self._upsert(conn, session_id, meta_patch={"last_read_at": stamp})
                stored[session_id] = stamp
        return stored

    def write_title(self, session_id: str, title: str, *, source: str) -> bool:
        """Set the title, recording WHO wrote it in ``meta.title_source``, unless a stronger
        writer already owns it. Check and write share one locked transaction, because the
        client's automatic first-message title and the AI title race each other.

        ``user`` always wins. ``ai`` never overwrites ``user`` or an earlier ``ai`` and never
        CREATES a row (a conversation deleted mid-generation stays deleted). ``auto`` (the
        client's own derivation) yields to both. Returns whether the title was written."""
        if source not in ("user", "ai", "auto"):
            raise ValueError(f"unknown title source {source!r}")
        with self._use_conn(None) as conn:
            existing = self._row(conn, session_id)
            owner = ((existing or {}).get("meta") or {}).get("title_source")
            if source == "ai" and (existing is None or owner in ("user", "ai")):
                return False
            if source == "auto" and owner in ("user", "ai"):
                return False
            self._upsert(conn, session_id, meta_patch={"title": title, "title_source": source})
            return True
