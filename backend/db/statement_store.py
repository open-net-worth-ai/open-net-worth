"""
StatementStore — persistent storage for uploaded bank statement files and their parsed rows.

Each statement record keeps:
  - the original file bytes on disk (backend/data/files/statements/<id>.<ext>)
  - extracted text / page images (the upload cache entry, minus TTL)
  - parsed transaction rows once diff_statement has run
  - link to the import batch(es) created from this statement

Schema (as read/returned by every method below — see the PLAN 3 TASK 4 note for how
``parsed_rows`` is actually stored):
    id               : str   (UUID)
    created_at       : str   (ISO 8601 UTC)
    filename         : str   (original upload filename)
    mime             : str   (MIME type)
    size_bytes       : int
    file_hash        : str   (SHA-256 hex — used for dedup)
    file_path        : str   (relative path under DATA_DIR, e.g. "files/statements/abc123.pdf")
    kind             : str   ("text" | "pdf_pages" | "image")
    upload_entry     : dict  (full upload cache entry: text/images/media_type etc.)
    parsed_rows      : list | None   ({date, amount, direction, description} dicts)
    parsed_at        : str | None    (ISO 8601 when parsed_rows was last set)
    account_id       : str | None    (set when diff_statement associates it with an account)
    batch_ids : list[str]     (batch IDs created from this statement)
    source_attachment_id : str | None (the chat AttachmentStore id this statement was
                                        explicitly promoted from, when it was — cross-
                                        channel-attachments RFC item 22; never set by a raw
                                        upload/URL/paste import)

PLAN 3 TASK 4: DB-backed (Core, table ``statements_meta`` — backend/db/tables.py). Rows are
promoted-columns-over-verbatim-payload, EXCEPT ``parsed_rows``: the plan-3 task-3 absorb
extracts it to ``FILES_DIR/statements/<id>.parsed.json`` (``core.paths.statements_files_dir()``
— the ONE accessor every consumer calls, security review fix round 2 Fix 3; 2026-08-22
consolidate-derived-data-storage RFC retired the earlier, separate
``statements_parsed/`` directory) and keeps only the ``has_parsed_rows`` flag in the row —
every write here (``create``/``set_parsed_rows``/``add_batch_id``/…) continues that
extraction on EVERY write (not just at cutover), which is the whole point: a fat parsed-rows
list living inline in ``payload`` forever would just reintroduce the 21.6MB-file problem the
extraction solved, one row at a time. Every READ method
(``find_by_id``/``all``/``query``/``list_statements``/``find_by_hash``) therefore re-inlines
``parsed_rows`` from disk before returning — via ``money_io._reinline_statement``, shared with
``export_statements`` — so a caller sees EXACTLY the dict shape ``JsonStore``-backed
``StatementStore`` always returned; extraction is an internal storage detail no caller above
this store has ever had to know about. Every write that PATCHES (``update_by_id``/
``bulk_patch``) first reconstructs the full existing row (payload + reinlined ``parsed_rows``)
and merges ``changes`` on top of THAT — never the bare stored payload — so an unrelated patch
(e.g. ``add_batch_id``) that never mentions ``parsed_rows`` re-extracts the SAME bytes to the
SAME path (a redundant but harmless rewrite — not a loss of the path) instead of wiping it,
because the merge always sees the key already present. See ``money_io._statement_row``'s own
docstring for the full reasoning.

Hand-rolled, not a ``payload_store.PayloadTableStore`` subclass: every mutate here writes more
than the bare ``{seq, id, payload}`` shape ``PayloadTableStore`` writes, and reads need the
reinlining pass ``PayloadTableStore`` knows nothing about. The conn=/lock/hook plumbing mirrors
``PayloadTableStore``'s own (see that module's docstring for the CONN= COMPOSITION and
CHECKPOINTS contracts — honored identically here).
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, cast

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core import request_memo
from backend.core.errors import ValidationError
from backend.core.file_ext import file_ext as _file_ext
from backend.core.ids import new_id
from backend.core.io import atomic_write_bytes, fire_db_write_hook
from backend.core.paths import DATA_DIR, statements_files_dir
from backend.db.engine import _lock_for_table
from backend.db.money_io import _reinline_statement, _statement_row
from backend.db.tables import statement_parse_progress as _parse_progress_table
from backend.db.tables import statements_meta

logger = logging.getLogger(__name__)

# B20 async-preview lifecycle (statements_meta.status) — see tables.py's own comment.
_PARSE_STATUSES = frozenset({"parsing", "ready", "failed", "interrupted"})


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_statement_file(file_path: str, *, statement_id: str = "?") -> Path | None:
    """``DATA_DIR / file_path``, but only when it stays inside the statements directory.

    ``file_path`` has TWO writers and only one of them is trustworthy. ``create()`` builds it
    itself as ``files/statements/<server-minted-id><ext>``, so every statement the app stores
    is fine by construction. ``db.money_io._statement_row`` copies it VERBATIM out of an
    imported portable bundle, and ``validate_money_file_shape`` checks that bundle's row ``id``
    but never this field — so a hand-edited bundle can put any string here.

    That matters because of how the join behaves: ``Path("/data") / "/etc/passwd"`` is
    ``/etc/passwd`` — an absolute component silently DISCARDS everything to its left, no error
    raised — and ``../`` walks upward. Either escapes the directory the reader assumes it is
    confined to, which is why the old comment here ("file_path is stored relative to DATA_DIR")
    was an assumption rather than a guarantee.

    This mirrors ``AttachmentStore.blob_path`` and ``ReceiptStore.blob_path``, which already
    re-prove containment for exactly this reason, and the sibling ``_unlink_stored_file_path``
    in this file, which refuses to DELETE outside the directory. Reads simply never got the
    same treatment.

    The untrusted string is never JOINED onto a base. It is validated syntactically, and then
    the path is REBUILT from the trusted directory plus the row's own file name — so there is
    no join for a hostile value to win in the first place. (Validating and then joining would
    also have worked, but it keeps a sharp edge around for the next reader to grab; this shape
    has none.)

    Rebuilding also happens to be the only thing that behaves under the test harness, which
    redirects ``statements_files_dir()`` per-test while ``DATA_DIR`` stays at the pytest root.
    ``create()`` already documents that divergence and falls back to the production convention
    when it hits it; a ``DATA_DIR``-joined read cannot resolve that combination at all.

    Returns ``None`` (log + refuse) rather than raising: both call sites already treat "no
    readable file" as a normal outcome, so a refusal degrades to the missing-file path instead
    of surfacing an exception to the operator.
    """

    def _refuse(why: str) -> None:
        logger.warning(
            "statement %r: stored file_path %r %s — refusing to read it (a portable-bundle "
            "import can carry an arbitrary path here)",
            statement_id,
            file_path,
            why,
        )

    if not file_path or not isinstance(file_path, str):
        return None
    if file_path.startswith("/") or "\\" in file_path or ".." in Path(file_path).parts:
        _refuse("is absolute or walks upward")
        return None

    name = Path(file_path).name
    if not name or name in (".", ".."):
        _refuse("has no file name")
        return None

    base = statements_files_dir()
    resolved = (base / name).resolve()
    # Belt and braces: the rebuild above should make this unreachable, but a base directory
    # that is itself a symlink out of the tree would still be caught here.
    try:
        if not resolved.is_relative_to(base.resolve()):
            _refuse("resolves outside the statements directory")
            return None
    except OSError:
        return None
    return resolved


# Keys on the upload_entry that must not be stored in the JSON record.
# ``content`` carries raw bytes (not JSON-serializable) — the file is already
# persisted to disk under file_path, so keeping a second copy in the JSON would
# both break serialization and waste space.
# ``expires_at`` is a cache-only TTL that has no meaning in persistent storage.
# ``was_encrypted`` is a one-shot decryption flag from parse_upload; callers that
# need the format should read the stored ``format`` key instead.
_UPLOAD_ENTRY_STRIP = frozenset({"content", "expires_at", "was_encrypted"})


class StatementStore:
    def __init__(self, engine: Engine | None = None):
        self._table = statements_meta
        self._engine = engine
        self._lock = _lock_for_table(statements_meta.name)

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

    def _full(self, db_row: dict) -> dict:
        """The full statement dict — ``payload`` with ``parsed_rows`` re-inlined. Passes the
        row's own COLUMN ``id`` explicitly (never ``payload["id"]`` — see
        ``money_io._reinline_statement``'s Fix 6 docstring note)."""
        return _reinline_statement(db_row["id"], db_row["payload"], db_row["has_parsed_rows"])

    def _write_existing(self, conn: Connection, id_val: str, row: dict) -> None:
        conn.execute(
            sa_update(self._table)
            .where(self._table.c.id == id_val)
            .values(**{k: v for k, v in row.items() if k != "id"})
        )

    # ── Query API (lock-free, mirrors JsonStore's read methods; reinlines parsed_rows) ──

    def _plain(self, row: dict) -> dict:
        """`_full(row)` plus the two promoted columns a narrow write can leave `payload`
        stale on — `status` (via `set_parse_status`) and `has_parsed_rows` (a portable-bundle
        row's raw dict could smuggle a stale value under this key) — WITHOUT `parse_progress`,
        which costs its own SELECT (the side table, B20 phase 2 L5). Same override reasoning
        as `_enrich`'s own docstring; split out so a caller that only needs `status` (the
        common case: `all()`/`find_by_hash`, which check `status == "parsing"` far more often
        than they read progress) never pays for a query it doesn't use."""
        return {
            **self._full(row),
            "status": row["status"],
            "has_parsed_rows": bool(row["has_parsed_rows"]),
        }

    def _enrich(self, row: dict) -> dict:
        """`_plain(row)` plus `parse_progress` (one extra SELECT) — correct for a genuinely
        single-row read (`find_by_id`/`find_by_hash`'s own one match), where "one query" is
        exactly the cost of the caller's own ask. `all()` uses `_enrich` too, but never per
        row — see `_progress_by_id` below (code review round C follow-up: `all()` was doing
        this per row, an N+1 that also hit every `find_by_hash` call transitively, since it
        used to be implemented as an `all()` scan)."""
        return {**self._plain(row), "parse_progress": self.get_parse_progress(row["id"])}

    def _progress_by_id(self, ids: list[str]) -> dict[str, dict]:
        """Every side-table row for `ids`, in ONE query — `all()`'s batched alternative to
        calling `get_parse_progress` once per row."""
        if not ids:
            return {}
        with self._eng().connect() as conn:
            rows = conn.execute(
                select(_parse_progress_table).where(_parse_progress_table.c.statement_id.in_(ids))
            )
            return {r.statement_id: r.progress for r in rows}

    def all(self) -> list[dict]:
        return request_memo.memo_rows(("statements.all",), self._read_all)

    def _read_all(self) -> list[dict]:
        with self._eng().connect() as conn:
            rows = [
                dict(r._mapping)
                for r in conn.execute(select(self._table).order_by(self._table.c.seq))
            ]
        progress = self._progress_by_id([r["id"] for r in rows])
        return [{**self._plain(r), "parse_progress": progress.get(r["id"])} for r in rows]

    def find_by_id(self, id_val: str) -> dict | None:
        with self._eng().connect() as conn:
            row = self._row(conn, id_val)
        return self._enrich(row) if row is not None else None

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
            conn.execute(sa_insert(self._table).values(**_statement_row(seq, item)))
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
                [_statement_row(seq + i, item) for i, item in enumerate(new_items)],
            )
        return new_items

    def upsert(self, item: dict, *, conn: Connection | None = None) -> dict:
        """Insert or WHOLESALE-replace by id — matches JsonStore's ``items[i] = item``
        (not a merge with the existing row; the caller's ``item`` is the new truth,
        including its own ``parsed_rows``/absence-of-it)."""
        id_val = cast(str, item.get("id"))
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            if existing is not None:
                self._write_existing(conn, id_val, _statement_row(existing["seq"], item))
            else:
                seq = self._next_seq(conn)
                conn.execute(sa_insert(self._table).values(**_statement_row(seq, item)))
        return item

    def update_by_id(
        self, id_val: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return None
            # `_plain`, not `_full` (code review, BLOCKER): `_full` is payload-only, so a
            # merge base built from it has no `status` key at all — `_statement_row` then
            # derives the promoted `status` COLUMN from `merged.get("status")`, which is
            # always `None` there, silently resetting a `failed`/`interrupted`/`parsing`
            # record to legacy-NULL on ANY unrelated patch (e.g. `add_batch_id` on commit, or
            # the mid-parse cache write) that never meant to touch it at all. `_plain` carries
            # the LIVE column forward as the merge base's default, same as every other
            # promoted field here — `changes` can still override it explicitly if a caller
            # ever needs to (none does today; `set_parse_status`'s narrow UPDATE remains the
            # only intentional writer).
            merged = {**self._plain(existing), **changes}
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            self._write_existing(conn, id_val, _statement_row(existing["seq"], merged))
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
                    # Same `_plain`-not-`_full` fix as `update_by_id` above.
                    patched[iid] = {**self._plain(existing_rows[iid]), **changes}
                    hook_rows.append((iid, existing_rows[iid]))
            if patched:
                fire_db_write_hook(self._table.name, hook_rows, conn=conn)
                for iid, merged in patched.items():
                    self._write_existing(
                        conn, iid, _statement_row(existing_rows[iid]["seq"], merged)
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
                    [_statement_row(i, item) for i, item in enumerate(items)],
                )

    # ── Write ──────────────────────────────────────────────────────────────────

    def create(
        self,
        filename: str,
        mime: str,
        content: bytes,
        upload_entry: dict,
        *,
        source_attachment_id: str | None = None,
        conn: Connection | None = None,
    ) -> dict:
        """
        Persist a raw upload.  Returns the new statement record.
        upload_entry is the dict produced by parse_upload() (kind, text/images, summary, etc.)
        minus the expires_at key.

        Dedup is BY CONTENT HASH — the same bytes always resolve to the same statement
        record, regardless of caller. This is what makes explicit promotion from a chat
        attachment IDEMPOTENT (RFC item 22, ``STATEMENT-ATTACHMENT-BOUNDARY``): re-promoting
        the same attachment (same bytes) returns the existing statement rather than minting a
        second one. ``source_attachment_id``, when given, records which chat attachment (if
        any) this statement was explicitly promoted from — never set by a raw upload, only by
        the explicit import path (``services.statements.decisions.decision_preview``). A
        dedup hit backfills it onto the existing record when that record doesn't have one yet
        (e.g. the file was first imported by URL/paste and is now also being promoted from an
        attachment carrying the identical bytes) — it never overwrites an already-recorded
        provenance.
        """
        file_hash = _file_hash(content)

        safe_entry = {k: v for k, v in upload_entry.items() if k not in _UPLOAD_ENTRY_STRIP}

        # Dedup: return existing record if same file was uploaded before
        existing = self.find_by_hash(file_hash)
        if existing:
            # Update upload_entry in case the cached entry changed (e.g. re-upload)
            changes = {"upload_entry": safe_entry}
            if source_attachment_id and not existing.get("source_attachment_id"):
                changes["source_attachment_id"] = source_attachment_id
            self.update_by_id(existing["id"], changes, conn=conn)
            return self.find_by_id(existing["id"])

        sid = new_id("stmt")
        ext = _file_ext(filename, mime)
        files_dir = statements_files_dir()
        disk = files_dir / f"{sid}{ext}"
        # atomic_write_bytes self-heals STATEMENTS_FILES_DIR's existence (its own
        # `path.parent.mkdir(parents=True, exist_ok=True)`) instead of a bare
        # Path.write_bytes — found + fixed alongside the identical gap in
        # AttachmentStore.create/ReceiptStore.create (see either's own comment for the
        # concrete failure this closes: a caller that wipes and recreates DATA_DIR
        # in-process, e.g. the eval harness's bootstrap.reset_data_dir()).
        atomic_write_bytes(disk, content)
        # file_path is DATA_DIR-relative (read_file_bytes/delete_statement reconstruct via
        # DATA_DIR / record["file_path"]) — computed from where the file was ACTUALLY
        # written, not a hardcoded "files/statements/..." guess: production has
        # STATEMENTS_FILES_DIR nested under DATA_DIR/files/ (2026-08-21 — see
        # docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md), but a test that
        # isolates STATEMENTS_FILES_DIR to some OTHER subdirectory of its own overridden
        # DATA_DIR (several do) must not need to also match a hardcoded literal here. Falls
        # back to the production convention only when STATEMENTS_FILES_DIR isn't under
        # DATA_DIR at all (a test that isolates one but not the other — read_file_bytes
        # cannot resolve that combination correctly either way, so this is not a regression).
        try:
            rel = disk.relative_to(DATA_DIR).as_posix()
        except ValueError:
            rel = f"files/statements/{sid}{ext}"

        record = {
            "id": sid,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "filename": filename or "",
            "mime": mime or "",
            "size_bytes": len(content),
            "file_hash": file_hash,
            "file_path": rel,
            "kind": upload_entry.get("kind", ""),
            "upload_entry": safe_entry,
            "parsed_rows": None,
            "parsed_at": None,
            "account_id": None,
            "batch_ids": [],
            "source_attachment_id": source_attachment_id,
        }
        self.insert(record, conn=conn)
        # Not `record` itself — `find_by_id` enriches with `status` (a promoted column, not
        # in `payload` until the first real transition) and B20's `parse_progress` (a
        # separate side table), so this stays the SAME shape every other read returns.
        return cast(dict, self.find_by_id(sid))

    def set_parsed_rows(
        self,
        statement_id: str,
        rows: list[dict],
        account_id: str | None = None,
        *,
        meta: dict | None = None,
        reconcile: dict | None = None,
        tier: str | None = None,
        conn: Connection | None = None,
    ) -> dict | None:
        """``meta``/``reconcile`` (the ``diff_statement`` AI-tool cache — distinct from
        ``import_meta``/``reconcile``, the decision-preview pipeline's own fields on this
        same record) let a later cache-hit re-diff apply the same completeness gate the
        fresh parse just did, without re-parsing. ``tier`` shares the record's ONE ``tier``
        field with the decision-preview pipeline — both describe the same underlying file,
        so they should agree; it is what lets the completeness gate's tabular exception
        apply to a cached re-diff too."""
        changes: dict = {
            "parsed_rows": rows,
            "parsed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if account_id is not None:
            changes["account_id"] = account_id
        if meta is not None:
            changes["parsed_meta"] = meta
        if reconcile is not None:
            changes["parsed_reconcile"] = reconcile
        if tier is not None:
            changes["tier"] = tier
        return self.update_by_id(statement_id, changes, conn=conn)

    def get_parse_progress(self, statement_id: str) -> dict | None:
        """The B20 registry's snapshot for this statement, or ``None`` if it never parsed
        under B20 (a legacy record) or the side table has no row for it yet."""
        with self._eng().connect() as conn:
            row = conn.execute(
                select(_parse_progress_table.c.progress).where(
                    _parse_progress_table.c.statement_id == statement_id
                )
            ).first()
        return row[0] if row else None

    def upsert_parse_progress(
        self, statement_id: str, progress: dict, *, conn: Connection | None = None
    ) -> None:
        """Narrow per-page progress write (B20 phase 2, L5): one row per statement, always
        overwritten in place — never grows, never touches ``statements_meta`` — so a
        multi-minute scan's ~10 progress updates cost 10 tiny upserts instead of 10 full
        ``update_by_id`` read/reinline/re-extract/write cycles."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._use_conn(conn) as conn:
            existing_row = conn.execute(
                select(_parse_progress_table).where(
                    _parse_progress_table.c.statement_id == statement_id
                )
            ).first()
            existing = dict(existing_row._mapping) if existing_row else None
            fire_db_write_hook(_parse_progress_table.name, [(statement_id, existing)], conn=conn)
            if existing:
                conn.execute(
                    sa_update(_parse_progress_table)
                    .where(_parse_progress_table.c.statement_id == statement_id)
                    .values(progress=progress, updated_at=now)
                )
            else:
                conn.execute(
                    sa_insert(_parse_progress_table).values(
                        statement_id=statement_id, progress=progress, updated_at=now
                    )
                )

    def set_parse_status(
        self,
        statement_id: str,
        status: str,
        *,
        progress: dict | None = None,
        error: str | None = None,
        conn: Connection | None = None,
    ) -> dict | None:
        """B20 async-preview lifecycle: ``parsing`` | ``ready`` | ``failed`` | ``interrupted``.

        The persistence hook ``services.statements.parse_progress`` attaches at
        ``bind_statement`` time calls this at every stage boundary and on done/error — a
        handful of small writes per parse, so a browser that reopens a statement by id (or
        the boot sweep, on a restart) reads the SAME state a live poll would have.

        Two INDEPENDENT narrow writes, neither an ``update_by_id`` (L5): ``status`` moves via
        a single-column UPDATE, and ONLY when it's actually changing (most calls during one
        parse keep re-stamping the SAME ``parsing`` status as later pages report in — those
        touch ``statement_parse_progress`` only). ``progress`` — the registry's own snapshot,
        stored verbatim — upserts into that side table. ``error`` is a convenience for a
        caller with a message but no snapshot (e.g. a failure caught before ``bind_statement``
        ever ran): stored as ``{"error": <message>}``, never overwriting a real snapshot that's
        already there unless this call also passes one."""
        if status not in _PARSE_STATUSES:
            raise ValidationError(f"unknown parse status: {status!r}")
        with self._use_conn(conn) as conn:
            current = conn.execute(
                select(self._table.c.status).where(self._table.c.id == statement_id)
            ).first()
            if current is None:
                return None
            if current[0] != status:
                existing = self._row(conn, statement_id)
                fire_db_write_hook(self._table.name, [(statement_id, existing)], conn=conn)
                conn.execute(
                    sa_update(self._table)
                    .where(self._table.c.id == statement_id)
                    .values(status=status)
                )
            if progress is not None:
                self.upsert_parse_progress(statement_id, progress, conn=conn)
            elif error is not None:
                self.upsert_parse_progress(statement_id, {"error": error}, conn=conn)
        return self.find_by_id(statement_id)

    def sweep_interrupted_parses(self) -> int:
        """Boot sweep (B20): a record left ``parsing`` did not finish before the process that
        was reading it went away — a restart, a crash, a `kill -9`. There is no live
        registry entry to ask any more (the registry is in-process and gone with the old
        process), so a restart can never leave a ghost "still parsing" row: every one is
        flipped to ``interrupted`` here, unconditionally, once, at boot, after migrations.
        Returns the count flipped.

        SINGLE-WORKER ASSUMPTION: the shipped Makefile runs uvicorn with no ``--workers``
        flag, so ONE process ever owns the in-process registry (``parse_jobs``'s ``_running``
        set) — every ``parsing`` row at THIS process's boot is, by definition, dead: nothing
        alive could still be updating it. (A live QA run against 63784dccb hit this directly:
        an earlier revision skipped a row whose progress looked "recently updated" as a
        guard against a hypothetical sibling worker, which instead left a genuinely-dead row
        stuck ``parsing`` forever, since the sweep is this row's ONLY path out once its owning
        process is gone.) An operator-chosen ``--workers N`` would need each worker to own
        (and sweep) only ITS OWN rows via a per-worker identity stamped alongside ``parsing``
        — out of scope until multi-worker is actually supported."""
        stuck = self.query(status="parsing")
        for row in stuck:
            self.set_parse_status(row["id"], "interrupted")
        return len(stuck)

    def add_batch_id(
        self, statement_id: str, batch_id: str, *, conn: Connection | None = None
    ) -> dict | None:
        record = self.find_by_id(statement_id)
        if record is None:
            return None
        ids = list(record.get("batch_ids") or [])
        if batch_id not in ids:
            ids.append(batch_id)
        return self.update_by_id(statement_id, {"batch_ids": ids}, conn=conn)

    # ── Read ───────────────────────────────────────────────────────────────────

    def unlink_all_blobs(self, statement_id: str, *, file_path: str | None = None) -> None:
        """Remove EVERY file in ``statements_files_dir()`` named ``<statement_id>.*`` — the
        raw file, the ``.parsed.json`` derived-cache sidecar (when one was ever extracted),
        AND any migration trail (a revision-0046 ``.migrated-<ts>`` rename). Mirrors
        ``AttachmentStore``/``ReceiptStore``'s own ``unlink_all_blobs`` (2026-08-22
        consolidate-derived-data-storage RFC, Option A — one directory, one glob-delete, per
        store): the pre-RFC version of this method unlinked two hardcoded paths, which would
        have missed any trail file left by a future migration exactly the way the receipts/
        attachments delete-completeness gap did before it was closed (ADR
        2026-08-21's amendment item 2). ``Path.glob`` on a single-level pattern with no path
        separators is inherently scoped to ``statements_files_dir()`` itself — it cannot walk
        outside it.

        ``file_path`` (security review fix round 2, Fix 5): the row's OWN stored
        ``file_path``, passed by ``delete_statement`` — a row whose ``file_path`` was never
        rewritten onto the canonical ``<id><ext>`` shape (revision 0045's own
        ``_rewrite_old_relative_path`` deliberately leaves an unrecognised value alone; a
        portable-bundle import copies ``file_path`` verbatim, per-row, from untrusted JSON)
        has a raw file the id-based glob above can never find, since it isn't named
        ``<statement_id>.*`` at all — leaking it on disk forever. See
        ``_unlink_stored_file_path``'s own docstring for why that path is only ever unlinked
        when it resolves INSIDE the statements directory, never blindly followed outside it."""
        for path in statements_files_dir().glob(f"{statement_id}.*"):
            path.unlink(missing_ok=True)
        if file_path:
            self._unlink_stored_file_path(statement_id, file_path)

    def _unlink_stored_file_path(self, statement_id: str, file_path: str) -> None:
        """Best-effort removal of a NON-canonical ``file_path`` — see ``unlink_all_blobs``'s
        own docstring for why this exists. Only unlinks when the resolved path stays INSIDE
        ``statements_files_dir()``: ``file_path`` can be untrusted (a portable-bundle import
        copies it verbatim from bundle JSON), so blindly unlinking whatever
        ``DATA_DIR / file_path`` resolves to would be an ARBITRARY-UNLINK primitive — the
        exact, worse bug the pre-DB-migration unconditional unlink used to be. A path that
        resolves outside is logged as an orphan needing manual attention instead — a
        discoverable leak, never a silent one, and never a delete of something this store
        cannot prove it owns."""
        candidate = DATA_DIR / file_path
        try:
            resolved = candidate.resolve()
        except OSError:
            return
        try:
            contained = resolved.is_relative_to(statements_files_dir().resolve())
        except OSError:
            contained = False
        if not contained:
            if candidate.exists():
                logger.warning(
                    "statement %r: deleted the DB row but its stored file_path %r resolves "
                    "to %s, outside the statements files directory — refusing to unlink it; "
                    "needs manual cleanup",
                    statement_id,
                    file_path,
                    resolved,
                )
            return
        resolved.unlink(missing_ok=True)

    def delete_statement(self, statement_id: str, *, conn: Connection | None = None) -> bool:
        """Delete a statement RECORD and its stored file bytes. The transactions an import
        created are ledger data and are deliberately NOT touched (batch rollback owns that).
        Removing the record also forgets the sha-dedup — re-uploading the same file later
        re-parses fresh. File removal is best-effort; the record delete is authoritative.

        Also removes the extracted ``<id>.parsed.json`` sidecar (task-3 absorption's own
        artifact — a JsonStore-backed StatementStore never had one to clean up) and, when the
        row's stored ``file_path`` is non-canonical, the raw file at that path too (Fix 5) —
        via ``unlink_all_blobs``, so a deleted statement doesn't leave an orphaned,
        unreferenced file behind.

        ROLLBACK SAFETY (security review fix round 2, Fix 7 — mirrors
        ``ReceiptStore.delete_receipt``'s own documented contract exactly): when ``conn`` is
        given, this call is composed inside a CALLER's own transaction. A filesystem unlink
        is not part of that SQL transaction and is never undone by a rollback, so blob
        removal is DEFERRED — the caller is responsible for calling
        ``unlink_all_blobs(statement_id, file_path=record["file_path"])`` itself, strictly
        AFTER its own transaction has committed. A standalone call (``conn=None``, the
        default — every current caller, e.g. ``DELETE /api/statements/{id}``) unlinks
        immediately, same as before."""
        record = self.find_by_id(statement_id)
        if record is None:
            return False
        deleted = self.delete(statement_id, conn=conn)
        if conn is None:
            self.unlink_all_blobs(statement_id, file_path=record.get("file_path"))
        return deleted

    def find_by_hash(self, file_hash: str) -> dict | None:
        """Direct column lookup (code review round C follow-up) — this used to scan `all()`
        and filter in Python, which meant EVERY call (the sync/async M4 attach gate's own
        dedup check, fired on every upload) paid for a full-table read plus one `parse_progress`
        SELECT per row it never even needed. `file_hash` is a promoted, always-populated
        column (`_statement_row`), so a `WHERE` clause answers this in one row scan."""
        with self._eng().connect() as conn:
            row = conn.execute(
                select(self._table).where(self._table.c.file_hash == file_hash)
            ).first()
        return self._enrich(dict(row._mapping)) if row is not None else None

    def list_statements(self) -> list[dict]:
        items = self.all()
        return sorted(items, key=lambda s: s.get("created_at", ""), reverse=True)

    _CATALOG_COLUMNS = (
        "id",
        "created_at",
        "filename",
        "title",
        "note",
        "tags",
        "mime",
        "size_bytes",
        "account_id",
        "import_meta",
        "status",
    )

    def list_catalog(self, statement_id: str | None = None) -> list[dict]:
        """Listing fields for every statement (or the one ``statement_id``), newest first,
        read from the promoted COLUMNS only — no ``payload`` (whose ``upload_entry`` can carry
        a scanned statement's page images) and no parsed-rows sidecar. For callers that show a
        statement rather than read it (the Vault's list and its @-mention search)."""
        cols = [self._table.c[name] for name in self._CATALOG_COLUMNS]
        query = select(*cols).order_by(self._table.c.created_at.desc())
        if statement_id is not None:
            query = query.where(self._table.c.id == statement_id)
        with self._eng().connect() as conn:
            return [dict(r._mapping) for r in conn.execute(query)]

    def read_file_bytes(self, statement_id: str) -> bytes | None:
        record = self.find_by_id(statement_id)
        if record is None:
            return None
        path = resolve_statement_file(record["file_path"], statement_id=statement_id)
        if path is None or not path.exists():
            return None
        return path.read_bytes()
