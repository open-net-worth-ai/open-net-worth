"""Checkpoint context — the per-turn capture mechanism, active for the duration of one
``assistant.run()`` turn (or a headless/automation turn).

A ``CheckpointContext`` is bound to a ContextVar (``_active_ctx``) while a turn is in
flight. The single write choke point ``JsonStore._save_raw()`` (and the data.csv writer)
calls ``ctx.snapshot_before_write(path)`` *before* overwriting a file — the pre-write-hook
registration at the bottom of this module is what wires that in (RFC P1-A4: the
checkpoint→storage dependency is INVERTED, so ``db`` never imports ``ai``). DB-backed
stores use the parallel row-level mechanism instead (spec D7): ``core.io.fire_db_write_hook``
fires unconditionally on every DB-store write; ``record_db_before`` records the BEFORE image
of each ``(table, pk)`` into the ``write_log`` table, immediately and durably.

Split out of the former flat ``backend/ai/checkpoint.py`` (plan 4e, task 2) — pure code
motion, no logic changed. See ``backend/ai/checkpoint/__init__.py`` for the package's own
mechanism docstring and the compatibility re-export shim.
"""

from __future__ import annotations

import contextvars
import gzip
import hashlib
import logging
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.io import (
    _atomic_write_bytes,
    register_db_write_hook,
    register_write_hook,
)

from . import _policy, _restore

logger = logging.getLogger("backend.ai.checkpoint")

_stamp_lock = threading.Lock()
_last_stamp: datetime | None = None


def _ordered_stamp() -> str:
    """A checkpoint's created_at, strictly after the previous one this process issued.

    Restore decides which turns are "later" by comparing these stamps; two turns inside one
    tick of a coarse clock (Windows' is ~16 ms) would compare equal and a restore could
    treat a later turn as earlier. Microseconds are always written so the strings compare
    in time order."""
    global _last_stamp
    with _stamp_lock:
        now = datetime.now(timezone.utc)
        if _last_stamp is not None and now <= _last_stamp:
            now = _last_stamp + timedelta(microseconds=1)
        _last_stamp = now
        return now.isoformat(timespec="microseconds")


_active_ctx: contextvars.ContextVar["CheckpointContext | None"] = contextvars.ContextVar(
    "checkpoint_ctx", default=None
)

# Per-session count of in-flight turns. Restore refuses while > 0 so we never
# revert files out from under an active write. Single-process app → plain dict.
_active_turns: dict[str, int] = defaultdict(int)

# Checkpoint ids that must survive pruning/GC regardless of recency or index
# presence (I2, reproduced — Plan-2 final review). Two callers pin:
#
# - `begin_turn` pins a turn's own ctx.id for its FULL in-flight duration. A
#   turn's write_log rows are durable from the moment `record_db_before` first
#   fires — well before ITS OWN `end_turn`/finalise() ever adds it to the index —
#   so a completely different session's finalise()/prune hitting the cap in that
#   window used to `_gc_write_log` those rows out from under a turn that was
#   still being written (shape b).
# - `restore` pins its target checkpoint id, plus its pre_restore snapshot's own
#   id, for the duration of the restore. Capturing the pre_restore snapshot
#   finalises a NEW checkpoint, and that finalise's own prune must never be what
#   evicts the very checkpoint restore is about to replay: at MAX_CHECKPOINTS
#   active checkpoints, restoring the OLDEST one used to self-destruct — the
#   pre_restore append pushed the target past the cap, `_prune_old_checkpoints`
#   dropped it from the index, `_gc_write_log` deleted its write_log rows, and
#   the replay that followed found zero rows and reported SUCCESS having
#   reverted nothing (shape a).
#
# Every mutation goes through `_pin_checkpoint`/`_unpin_checkpoint`, which take
# `_index._index_lock`. `_prune_old_checkpoints`/`_gc_write_log`/`_gc_blobs` read this
# set directly, WITHOUT re-acquiring the lock — they are only ever called from a
# context that already holds it (`finalise()`, `delete_checkpoint()`), which by
# itself serializes them against any concurrent pin/unpin.
#
# A Counter, not a set (Plan 3 task 1 routed fix): `restore()` pins its target
# checkpoint id itself, but the SAME id can ALSO be `begin_turn`'s own pin (e.g. the
# model calls `restore_checkpoint` as a tool mid-its-own-turn — see restore()'s
# concurrency-guard comment) or a second, concurrent restore of that same id. A
# plain set's `discard` would let the FIRST unpin release a checkpoint a second
# caller is still actively relying on; the refcount means "still pinned" survives
# for as long as ANY holder has not yet unpinned. `in`/iteration over a Counter
# already behaves exactly like a set (dict-shaped: keys only) for every read below,
# so only the two mutators change shape.
_pinned_ids: Counter[str] = Counter()


def _pin_checkpoint(checkpoint_id: str) -> None:
    from . import _index

    with _index._index_lock:
        _pinned_ids[checkpoint_id] += 1


def _unpin_checkpoint(checkpoint_id: str) -> None:
    from . import _index

    with _index._index_lock:
        remaining = _pinned_ids.get(checkpoint_id, 0) - 1
        if remaining <= 0:
            # Counter.__contains__ checks KEY presence, not truthiness — a
            # zero-count key left in place would still read as "pinned" to every
            # `in _pinned_ids` check below, so it must be removed, not just
            # decremented to 0. `.pop(..., None)` (not `del`) so an unpin with no
            # matching prior pin — should never happen, but this is restore-path
            # money-adjacent bookkeeping — can't raise KeyError either.
            _pinned_ids.pop(checkpoint_id, None)
        else:
            _pinned_ids[checkpoint_id] = remaining


# ── Checkpoint context (active during a turn) ────────────────────────────────


class CheckpointContext:
    def __init__(
        self,
        checkpoint_id: str,
        session_id: str,
        anchor_history_len: int,
        message_preview: str,
        kind: str = "turn",
    ):
        self.id = checkpoint_id
        self.session_id = session_id
        self.anchor_history_len = anchor_history_len
        self.message_preview = message_preview
        self.kind = kind
        self.created_at = _ordered_stamp()
        self._seen: set[Path] = set()
        self.files: dict[str, str | None] = {}  # name -> "sha256:…" | None (didn't exist)
        self.has_writes = False
        # DB half (spec D7): first-touch-per-(table, pk) dedup, a per-context monotonic
        # write_log seq, and the set of tables touched (-> manifest "db_tables").
        self._db_seen: set[tuple[str, str]] = set()
        self._db_seq = 0
        self._db_tables: set[str] = set()
        # The write-then-verify chip for this turn (services.write_receipt), attached by
        # the caller just before finalise. Stored ON the checkpoint deliberately: the chip's
        # Undo IS a restore of this exact checkpoint, and persisting them together is what
        # makes the chip survive a reload (turn_receipts, below) without a second store.
        self.receipt: dict | None = None

    def snapshot_before_write(self, path: Path) -> None:
        """Capture pre-write content of `path` (once per turn), plus any COMPANION file it
        forms a referential unit with. Best-effort."""
        self._capture(path)
        for name in _policy._COMPANIONS.get(path.name, ()):
            self._capture(path.parent / name)

    def _capture(self, path: Path) -> None:
        try:
            rp = path.resolve()
            if rp in self._seen:
                return
            if rp in _policy._EXCLUDED_PATHS:
                return
            if any(rp == pre or pre in rp.parents for pre in _policy._EXCLUDED_PREFIXES):
                return  # under an excluded tree (e.g. skill_bundles/) — never captured
            self._seen.add(rp)
            self.has_writes = True
            if not path.exists():
                self.files[path.name] = None  # restore = delete (file is new this turn)
                return
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            blob = _restore._blob_path(digest)
            if not blob.exists():  # dedup
                _atomic_write_bytes(blob, gzip.compress(data))
            self.files[path.name] = f"sha256:{digest}"
        except Exception:
            logger.exception("checkpoint snapshot failed for %s", path)

    def record_db_before(
        self,
        table_name: str,
        rows: list[tuple[str, dict | None]],
        conn: object | None = None,
        *,
        fail_closed: bool = False,
    ) -> None:
        """Pre-write hook for DB stores (spec D7): capture the before-image of each
        (table_name, pk) the first time it's touched this turn (self._db_seen dedup).

        TRANSACTION CONTRACT (review-hardened — do not weaken without re-reading the
        incident this closes): if `conn` is given (the caller's own OPEN WRITE
        transaction), the write_log rows are inserted THROUGH IT — no independent
        begin(), so the image commits atomically with the caller's write. If `conn`
        is None, this opens its OWN short transaction immediately — durability
        parity with file blobs (images exist even if the turn crashes before
        finalise()) — which is safe ONLY when the caller holds no in-flight write
        transaction of its own: firing conn-less from inside an open write
        transaction stalls on SQLite's single-writer slot, and since this method is
        best-effort, the eventual failure is SWALLOWED — no checkpoint minted for a
        real change. Callers that already hold a write transaction MUST pass it.

        POSTGRES DIVERGENCE (found by the first genuine `tests/db` run against real
        Postgres, Plan-2 final review item 1): the `conn`-given insert runs inside a
        SAVEPOINT (`conn.begin_nested()`), not as a bare statement on the caller's
        transaction. On SQLite a failed statement inside an open transaction leaves
        it perfectly usable for whatever runs next; on Postgres ANY statement
        failure aborts the WHOLE transaction — every later statement on that same
        connection raises `InFailedSqlTransaction` until a ROLLBACK, even one that
        has nothing to do with write_log. A bare `except Exception: log and
        continue` around the insert was therefore silently SQLite-only: on
        Postgres, the very next statement the CALLER issued on this same `conn`
        (its own real data write, e.g. `PayloadTableStore.insert`'s row insert)
        died too — with an unrelated-looking error, not the transparent swallow
        this method exists to guarantee. The SAVEPOINT confines the failure to
        just this insert, identically on both dialects, so the caller's
        transaction is exactly as usable afterward as if the hook had never fired.

        FAIL_CLOSED (Plan 3 task 1 — restore hardening): defaults to False, and
        every IN-TURN caller (the registered hook, fired on every real DB write via
        `_snapshot_db_active`) keeps that default — a failing image must never
        block or fail the real write it precedes, full stop. Pass
        `fail_closed=True` ONLY from a caller for whom a silently incomplete image
        is worse than aborting outright — today, exclusively
        `_capture_db_pre_restore`, where a swallowed failure would let `restore()`
        go on to mutate live data behind an undo snapshot that cannot actually undo
        it. With `fail_closed=True` the exception below is still logged (as
        always) and then RE-RAISED instead of swallowed.
        """
        if table_name in _policy._EXCLUDED_TABLES:
            return
        to_insert = []
        for pk, before in rows:
            key = (table_name, pk)
            if key in self._db_seen:
                continue
            self._db_seen.add(key)
            self._db_seq += 1
            to_insert.append(
                {
                    "checkpoint_id": self.id,
                    "seq": self._db_seq,
                    "table_name": table_name,
                    "pk": pk,
                    "before_payload": before,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        if not to_insert:
            return
        try:
            from backend.db import tables as db_tables

            if conn is not None:
                # SAVEPOINT-isolated — see the POSTGRES DIVERGENCE docstring note
                # above. A bare `conn.execute(...)` here would let a failure poison
                # the caller's whole transaction on Postgres (never on SQLite).
                with conn.begin_nested():
                    conn.execute(db_tables.write_log.insert(), to_insert)
            else:
                from backend.db.engine import get_engine

                with get_engine().begin() as own_conn:
                    own_conn.execute(db_tables.write_log.insert(), to_insert)
            self.has_writes = True
            self._db_tables.add(table_name)
        except Exception:
            logger.exception("checkpoint db snapshot failed for table %s", table_name)
            if fail_closed:
                raise

    def finalise(self) -> dict | None:
        from . import _index

        if not self.has_writes:
            return None
        entry = {
            "id": self.id,
            "session_id": self.session_id,
            "anchor_history_len": self.anchor_history_len,
            "created_at": self.created_at,
            "message_preview": self.message_preview,
            "files": self.files,
            "status": "active",
            "kind": self.kind,
        }
        if self._db_tables:
            entry["db_tables"] = sorted(self._db_tables)
        if self.receipt:
            entry["receipt"] = self.receipt
        try:
            with _index._index_lock:
                _index._append_index(entry)
                _index._prune_old_checkpoints()
        except Exception:
            logger.exception("checkpoint finalise failed")
            return None
        return entry


# ── Turn lifecycle ───────────────────────────────────────────────────────────


def get_active() -> "CheckpointContext | None":
    return _active_ctx.get()


# ── Pre-write hook registration (RFC P1-A4) ──────────────────────────────────
# Invert the checkpoint→storage dependency: instead of db/json_store importing
# this module, we register a snapshot callback into the core.io write-hook seam.
# db calls core.io.fire_write_hook(path) before each write; that fires this hook,
# which snapshots the file into the active turn checkpoint (no-op when no turn is
# active). Registration is a load-time side effect of importing ai.checkpoint —
# which assistant.run() already does before any turn write occurs.


def _snapshot_active(path: Path) -> None:
    """Pre-write hook: snapshot `path` into the active turn checkpoint, if any.
    No active turn → no-op. snapshot_before_write is itself best-effort."""
    ctx = get_active()
    if ctx is not None:
        ctx.snapshot_before_write(path)


register_write_hook(_snapshot_active)


def _snapshot_db_active(
    table_name: str, rows: list[tuple[str, dict | None]], conn: object | None = None
) -> None:
    """Pre-write hook: record DB `rows`' before-images into the active turn checkpoint,
    if any (spec D7). `conn` passes straight through from `fire_db_write_hook` — see
    `CheckpointContext.record_db_before`'s docstring for the transaction contract
    (a caller with an open write transaction MUST pass it). No active turn → no-op;
    record_db_before is itself best-effort."""
    ctx = get_active()
    if ctx is not None:
        ctx.record_db_before(table_name, rows, conn)


register_db_write_hook(_snapshot_db_active)


def begin_turn(
    session_id: str,
    anchor_history_len: int,
    message_preview: str,
    kind: str = "turn",
) -> tuple["CheckpointContext", object]:
    # new_id is a monkeypatchable seam (tests set cp.new_id, e.g. to pin a minted id) —
    # read it via the package's OWN attribute, not a bare top-level import, so a patch
    # applied to the package is observed (a bare import here would bind a separate,
    # un-patched copy in this submodule's own namespace).
    from backend.ai import checkpoint as _ckpt_pkg

    ctx = CheckpointContext(
        _ckpt_pkg.new_id("ckpt"), session_id, anchor_history_len, message_preview, kind
    )
    token = _active_ctx.set(ctx)
    _active_turns[session_id] += 1
    _pin_checkpoint(ctx.id)  # protects its write_log rows until end_turn unpins (I2)
    return ctx, token


def end_turn(ctx: "CheckpointContext", token: object) -> dict | None:
    try:
        _active_ctx.reset(token)
    except Exception:
        pass
    _active_turns[ctx.session_id] = max(0, _active_turns[ctx.session_id] - 1)
    try:
        return ctx.finalise()
    finally:
        _unpin_checkpoint(ctx.id)


def _session_turn_active(session_id: str) -> bool:
    return _active_turns.get(session_id, 0) > 0
