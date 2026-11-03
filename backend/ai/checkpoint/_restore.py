"""Checkpoint restore — reverting to a checkpoint, and previewing what a restore would do.

Restore takes a `pre_restore` safety snapshot first (so any restore is itself
undoable — its own DB half is FAIL-CLOSED, see `_capture_db_pre_restore`), refuses to run
while a turn is active for the session, then STAGES every file's restored bytes to a
temp path (durably) BEFORE the DB replay commits, and only RENAMES each staged temp into
place — the one remaining split-world window — after that replay succeeds (Plan 3 task 1
restore hardening; see `_stage_restore_files`/`_commit_staged_restore_files`).

DB-backed stores (spec D7) use a parallel row-level mechanism instead of file blobs: restore
replays `write_log`'s before-images in one transaction (`_replay_write_log`), so one turn
that wrote both a JSON store and a DB store reverts both — DB replay first, files
staged-then-committed last — or neither (a DB replay failure raises before any file is
touched, and any already-staged temp is cleaned up).

Split out of the former flat ``backend/ai/checkpoint.py`` (plan 4e, task 2) — pure code
motion, no logic changed. See ``backend/ai/checkpoint/__init__.py`` for the package's own
mechanism docstring and the compatibility re-export shim.
"""

from __future__ import annotations

import gzip
import json
import logging
from collections import defaultdict
from pathlib import Path

from backend.core.io import _atomic_write_bytes

from . import _context, _index, _replay

logger = logging.getLogger("backend.ai.checkpoint")


def _restore_dest(name: str) -> Path:
    """All checkpointable files live in DATA_DIR; restore writes back there."""
    # DATA_DIR is a monkeypatchable seam (tests point cp.DATA_DIR at a tmp dir so a test
    # never touches live/shared data) — read it via the package's OWN attribute, not a
    # bare top-level import, so a patch applied to the package is observed.
    from backend.ai import checkpoint as _ckpt_pkg

    return _ckpt_pkg.DATA_DIR / name


def _blob_path(digest: str) -> Path:
    # CHECKPOINTS_BLOBS is a monkeypatchable seam (same reasoning as DATA_DIR above) —
    # read it via the package's OWN attribute for the same reason.
    from backend.ai import checkpoint as _ckpt_pkg

    return _ckpt_pkg.CHECKPOINTS_BLOBS / f"{digest}.gz"


def _blob_bytes(blob_ref: str) -> bytes:
    digest = blob_ref.split(":", 1)[1]
    return gzip.decompress(_blob_path(digest).read_bytes())


def _session_label(session_id: str | None) -> str:
    """Human label for a session: 'Telegram', 'web', etc. Restore is point-in-time
    and reverts GLOBAL data files, so changes from other channels matter.
    """
    if not session_id:
        return "another session"
    try:
        from backend.connectors import session_map

        connector = session_map.connector_of_session(session_id)
        if connector:
            return connector.capitalize()
    except Exception:
        pass
    return "web"


def _count_rows(raw: bytes) -> int:
    """Best-effort row count for a data file (JSON list/dict, or CSV)."""
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            return len(obj)
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list):
                    return len(v)
            return len(obj)
    except Exception:
        # CSV: count non-empty lines minus header
        try:
            lines = [ln for ln in raw.decode("utf-8").splitlines() if ln.strip()]
            return max(0, len(lines) - 1)
        except Exception:
            return 0
    return 0


def _single_pk_col(table):
    cols = list(table.primary_key.columns)
    if len(cols) != 1:
        raise RuntimeError(
            f"write_log checkpoints support single-column primary keys only; "
            f"table {table.name!r} has {len(cols)} — extend write_log before "
            "checkpoint-including this table (composite pks would corrupt "
            "unrelated rows on replay)"
        )
    return cols[0]


def _pk_param(pk_col, pk: str):
    """A write_log pk is stored as text; bind it as the column's own type. SQLite coerces
    ``seq = '7'`` by affinity, Postgres refuses ``integer = varchar``."""
    from sqlalchemy import Integer

    return int(pk) if isinstance(pk_col.type, Integer) else pk


def preview(checkpoint_id: str) -> dict:
    """What would change if this checkpoint were restored (per-file row deltas).

    Also surfaces cross_session_changes: turns from OTHER sessions made after this
    checkpoint. Because restore is point-in-time over global data files, restoring
    here also undoes those — e.g. transactions you later added from Telegram.
    """
    target = _index.find_checkpoint(checkpoint_id)
    if target is None:
        raise FileNotFoundError(checkpoint_id)
    cp = _restore_scope(_restore_chain(target))
    changes: list[dict] = []
    for name, blob_ref in cp.get("files", {}).items():
        dest = _restore_dest(name)
        current_n = _count_rows(dest.read_bytes()) if dest.exists() else 0
        snapshot_n = _count_rows(_blob_bytes(blob_ref)) if blob_ref else 0
        changes.append(
            {
                "file": name,
                "current_count": current_n,
                "restored_count": snapshot_n,
                "delta": snapshot_n - current_n,
            }
        )
    changes.extend(_db_preview_rows(cp))

    # Newer turns from a different session that this restore would also unwind.
    this_session = cp.get("session_id")
    cutoff = cp.get("created_at", "")
    cross: list[dict] = []
    for e in _index._read_index():
        if (
            e.get("kind") == "turn"
            and e.get("status") == "active"
            and e.get("created_at", "") > cutoff
            and e.get("session_id") != this_session
        ):
            cross.append(
                {
                    "session_label": _session_label(e.get("session_id")),
                    "message_preview": e.get("message_preview", ""),
                    "created_at": e.get("created_at", ""),
                }
            )
    cross.sort(key=lambda c: c["created_at"])

    return {
        "checkpoint_id": checkpoint_id,
        "changes": changes,
        "cross_session_changes": cross,
    }


def _db_preview_rows(cp: dict) -> list[dict]:
    """Per-table {"file": "db:<table>", ...} preview rows (spec D7): live row count vs
    the count implied by this checkpoint's write_log before-images. No actual replay —
    each recorded pk's CURRENT existence is checked (a read) to classify its implied
    op as a no-op, an insert, or a delete.

    A table this checkpoint touched but which no longer exists in the current schema
    (dropped/renamed since the checkpoint was taken) RAISES (Minor fold-in, I2) —
    aligned with ``_replay_write_log``, which already fails loud the same way
    (``metadata.tables[table_name]`` raises ``KeyError`` rather than skipping it);
    silently omitting the row here used to under-report the panel instead of
    surfacing that a restore of this checkpoint would fail outright."""
    table_names = cp.get("db_tables") or []
    if not table_names:
        return []
    from sqlalchemy import func, select

    from backend.db import tables as db_tables
    from backend.db.engine import get_engine

    out: list[dict] = []
    with get_engine().connect() as conn:
        for table_name in table_names:
            table = db_tables.metadata.tables.get(table_name)
            if table is None:
                raise RuntimeError(
                    f"checkpoint {cp['id']!r} touched table {table_name!r}, which no "
                    "longer exists (dropped or renamed since this checkpoint was "
                    "taken) — refusing to compute a partial/misleading preview; a "
                    "restore of this checkpoint would fail the same way"
                )
            current_n = conn.execute(select(func.count()).select_from(table)).scalar_one()
            pk_col = _single_pk_col(table)
            existing = {str(v) for v in conn.execute(select(pk_col)).scalars()}
            delta = 0
            images = _chain_images(conn, _scope_ids(cp), table_names=[table_name])
            for pk, before_payload in images.get(table_name, []):
                existed_now = pk in existing
                if before_payload is None and existed_now:
                    delta -= 1  # restore would DELETE this pk
                elif before_payload is not None and not existed_now:
                    delta += 1  # restore would re-INSERT this pk
            out.append(
                {
                    "file": f"db:{table_name}",
                    "current_count": current_n,
                    "restored_count": current_n + delta,
                    "delta": delta,
                }
            )
    return out


# ── Restore ───────────────────────────────────────────────────────────────────


def restore(checkpoint_id: str, mode: str = "data") -> dict:
    """Restore to a checkpoint.

    mode:
      "data"          revert data files only, keep chat history (default)
      "conversation"  rewind chat to before the turn only, keep data
      "both"          revert data files AND rewind chat
    """
    if mode not in ("data", "conversation", "both"):
        raise ValueError(f"invalid mode {mode!r}")
    cp = _index.find_checkpoint(checkpoint_id)
    if cp is None:
        raise FileNotFoundError(checkpoint_id)
    session_id = cp.get("session_id")
    # Concurrency guard. _restore_dest + _atomic_write_bytes below overwrite GLOBAL data
    # files (transactions.json, accounts.json, ...) wholesale — the write is not scoped to
    # `session_id` in any way. A per-session check ("only block if a *different* concurrent
    # turn is running") used to be correct because at most one interactive turn could ever
    # be in flight at a time, so "per-session" and "global" were indistinguishable.
    # Concurrent conversations broke that: two sessions can now both be mid-turn, and a
    # restore issued from/for one of them would silently clobber the other's in-flight
    # writes with no way for either checkpoint to undo the resulting mixture. So the guard
    # must be global — refuse whenever ANY session other than the one making THIS call has
    # a turn in flight. The one exception stays: the model calling restore_checkpoint as a
    # tool mid-its-own-turn is a sequential write (the turn is paused on this very call, not
    # running concurrently with it), so that turn must not count against itself.
    current_ctx = _context._active_ctx.get()
    caller_session_id = current_ctx.session_id if current_ctx is not None else None
    if any(n > 0 for sid, n in _context._active_turns.items() if sid != caller_session_id):
        raise RuntimeError(
            "Another conversation is currently making changes — try restoring again in a moment."
        )

    # new_id/_replay_write_log are monkeypatchable seams (tests pin the pre-restore id, or
    # make replay raise to exercise the cleanup path) — read them via the package's OWN
    # attribute, not a bare import, so a patch applied to the package is observed (this
    # submodule defines _replay_write_log itself below, but a bare intra-module call would
    # NOT see a patch applied to the package's separate re-exported copy).
    from backend.ai import checkpoint as _ckpt_pkg

    restored_files: list[str] = []
    pre_restore_id: str | None = None
    superseded: list[str] = []
    revived: list[str] = []

    # "Before this message" means before EVERY turn of it (a card approved after its turn
    # paused is a checkpoint of its own on the same message) and undoes every later turn of
    # the conversation too — `_restore_chain`. `head` is that message's first checkpoint.
    chain = _restore_chain(cp)
    head = chain[0]
    scope = _restore_scope(chain)
    chain_ids = _scope_ids(scope)

    if mode in ("data", "both"):
        # Pin every replayed checkpoint (and, below, the pre_restore snapshot's own id) for
        # the whole data-restore sequence (I2, shape a) — see _context._pinned_ids' module
        # comment for the self-destruct this closes: capturing the pre_restore
        # snapshot a few lines down finalises a NEW checkpoint, whose own prune must
        # never be allowed to evict the very checkpoints being replayed right after.
        for pinned in chain_ids:
            _context._pin_checkpoint(pinned)
        try:
            pre_restore_ctx_id = _ckpt_pkg.new_id("ckpt")
            _context._pin_checkpoint(pre_restore_ctx_id)
            try:
                pre_restore_id = _capture_pre_restore_snapshot(
                    scope, checkpoint_id=pre_restore_ctx_id
                )
                # Restore hardening (Plan 3 task 1): stage every file's restored
                # bytes to a temp path BEFORE the DB replay commits — see
                # _stage_restore_files. This shrinks the split-world window from
                # "however long the read+decompress+write loop takes" down to just
                # the fast rename/unlink loop in _commit_staged_restore_files.
                staged = _stage_restore_files(scope, checkpoint_id)
                try:
                    # DB half — aborts before any file touch
                    _ckpt_pkg._replay_write_log(checkpoint_id, chain_ids=chain_ids)
                except BaseException:
                    _cleanup_staged_restore_files(staged)
                    raise
                restored_files = _commit_staged_restore_files(staged)
                # Only a "data" restore keeps the later turns revivable: "both" rewinds
                # their messages out of the transcript, so nothing could show them again.
                superseded = _index._mark_orphaned_after(
                    head, revive_holder=pre_restore_id if mode == "data" else None
                )
                revived = _record_restore_mark(head, mode, pre_restore_id)
            finally:
                _context._unpin_checkpoint(pre_restore_ctx_id)
        finally:
            for pinned in chain_ids:
                _context._unpin_checkpoint(pinned)

    rewound_to: int | None = None
    if mode in ("conversation", "both"):
        anchor = head.get("anchor_history_len", 0)
        rewound_to = _truncate_session_history(session_id, anchor)
        if rewound_to is not None and head.get("kind", "turn") == "turn":
            _index._mark_rewound(session_id, anchor)

    logger.info(
        "restore checkpoint=%s mode=%s files=%d rewound_to=%s",
        checkpoint_id,
        mode,
        len(restored_files),
        rewound_to,
    )
    return {
        "checkpoint_id": checkpoint_id,
        "mode": mode,
        "restored_files": restored_files,
        "rewound_to": rewound_to,
        "undo_checkpoint_id": pre_restore_id,
        # Later turns of this conversation this restore rewound past (their messages lose
        # Restore while it stands), and the turns an Undo brought back.
        "superseded_checkpoint_ids": superseded,
        "revived_checkpoint_ids": revived,
    }


def _restore_chain(cp: dict) -> list[dict]:
    """The checkpoints a restore of ``cp`` replays, oldest first.

    Restoring a message puts the data back as it was before it, so the chain is every turn
    checkpoint of that message — an approved card's write and the turn it resumed are their
    own checkpoints, anchored on the same message — plus every later turn of the conversation
    still on the live timeline. Replaying only ``cp`` left every later message's writes in
    place behind a dialog that says "undoes this and any later changes" (owner, 2026-11-03).
    A rewound message's checkpoints still hold live data, so they stay in a later chain, but
    never group with the new message now sitting at their index. A pre_restore (Undo)
    snapshot already covers everything its restore touched — it replays alone."""
    if cp.get("kind", "turn") != "turn":
        return [cp]
    sid, anchor = cp.get("session_id"), cp.get("anchor_history_len")
    live = [
        e
        for e in _index._read_index()
        if e["id"] != cp["id"]
        and e.get("session_id") == sid
        and e.get("kind", "turn") == "turn"
        and e.get("status", "active") == "active"
    ]
    same_message = [
        e
        for e in live
        if not cp.get("rewound") and not e.get("rewound") and e.get("anchor_history_len") == anchor
    ]
    cutoff = min([cp.get("created_at", "")] + [e.get("created_at", "") for e in same_message])
    chain = [cp] + [e for e in live if e.get("created_at", "") >= cutoff]
    chain.sort(key=lambda e: e.get("created_at", ""))
    return chain


def _restore_scope(chain: list[dict]) -> dict:
    """One manifest for the whole chain, labelled as its first checkpoint: each file at the
    OLDEST snapshot taken of it (its state before the chain began), every DB table any of
    them touched, and the ids whose write_log rows the replay merges (``chain_ids``)."""
    files: dict[str, str | None] = {}
    tables: set[str] = set()
    for e in chain:
        for name, blob_ref in (e.get("files") or {}).items():
            files.setdefault(name, blob_ref)
        tables.update(e.get("db_tables") or [])
    return {
        **chain[0],
        "files": files,
        "db_tables": sorted(tables),
        "chain_ids": [e["id"] for e in chain],
    }


def _scope_ids(cp: dict) -> list[str]:
    return list(cp.get("chain_ids") or [cp["id"]])


def _chain_images(
    conn, chain_ids: list[str], table_names: list[str] | None = None
) -> dict[str, list[tuple[str, dict | None]]]:
    """Per table, each touched pk's before-image from the OLDEST checkpoint in ``chain_ids``
    (oldest first) that recorded it — the row as it was before the chain began. A single
    checkpoint records each pk once, so with one id this is exactly its own write_log."""
    from sqlalchemy import select

    from backend.db import tables as db_tables

    wl = db_tables.write_log
    rank = {cid: i for i, cid in enumerate(chain_ids)}
    query = select(
        wl.c.checkpoint_id, wl.c.table_name, wl.c.pk, wl.c.before_payload, wl.c.seq
    ).where(wl.c.checkpoint_id.in_(chain_ids))
    if table_names is not None:
        query = query.where(wl.c.table_name.in_(table_names))
    rows = sorted(conn.execute(query).all(), key=lambda r: (rank[r[0]], -r[4]))
    seen: set[tuple[str, str]] = set()
    out: dict[str, list[tuple[str, dict | None]]] = {}
    for _cid, table_name, pk, before_payload, _seq in rows:
        if (table_name, pk) in seen:
            continue
        seen.add((table_name, pk))
        out.setdefault(table_name, []).append((pk, before_payload))
    return out


def _record_restore_mark(cp: dict, mode: str, pre_restore_id: str | None) -> list[str]:
    """Persist "restored to before this message" on the turn checkpoint so a reload — on any
    device — shows the restored state and its Undo (``_index.turn_restores``). Only a "data"
    restore marks: "both" rewinds the anchor message out of the transcript, leaving nothing
    to show it on. Restoring a pre_restore checkpoint IS an Undo: it clears that mark and
    revives the later turns the undone restore had superseded (returned). Best-effort: the
    data is already restored, so a failed mark must not turn a successful restore into a
    reported failure."""
    try:
        if cp.get("kind") == "pre_restore":
            _index._clear_restores_undone_by(cp["id"])
            return _index._revive_superseded(cp)
        if mode == "data" and cp.get("kind", "turn") == "turn":
            _index._mark_restored(cp["id"], pre_restore_id)
    except Exception:
        logger.exception("could not record restore mark for checkpoint=%s", cp.get("id"))
    return []


def _capture_pre_restore_snapshot(cp: dict, *, checkpoint_id: str | None = None) -> str:
    """Snapshot the CURRENT content of the files this restore will touch, plus the
    CURRENT state of any DB rows this checkpoint's write_log recorded (spec D7), so
    the restore itself can be undone — same guarantee for DB rows as for files.
    Returns the new pre_restore checkpoint id.

    ``checkpoint_id``, when given, lets the caller (``restore``) mint and PIN this
    context's id BEFORE it exists (I2) — so it survives its own finalise()->prune
    call even in the edge case where its ``created_at`` ties with an older entry's.
    Defaults to a fresh id for any other/direct caller."""
    if checkpoint_id is None:
        from backend.ai import checkpoint as _ckpt_pkg  # see restore()'s own comment

        checkpoint_id = _ckpt_pkg.new_id("ckpt")
    ctx = _context.CheckpointContext(
        checkpoint_id,
        cp.get("session_id"),
        cp.get("anchor_history_len", 0),
        f"Before restore: {cp.get('message_preview', '')}"[:120],
        kind="pre_restore",
    )
    for name in cp.get("files", {}):
        ctx.snapshot_before_write(_restore_dest(name))
    _capture_db_pre_restore(cp, ctx)
    entry = ctx.finalise()
    return entry["id"] if entry else ctx.id


def _capture_db_pre_restore(cp: dict, ctx: "_context.CheckpointContext") -> None:
    """DB half of _capture_pre_restore_snapshot: for each (table, pk) touched by `cp`'s
    write_log, record the CURRENT row state into `ctx` (None if currently absent) —
    so restoring `cp` is itself undoable for DB rows.

    FAIL-CLOSED, entirely (Plan 3 task 1 — restore hardening; this used to be only
    PARTLY true, see below). The SELECT reads below (and the single-PK-column
    check) were already UNWRAPPED, so a read failure or a composite-PK table
    already propagated and aborted the whole restore — replaying blind when we
    can't even read the current state, or can't identify a row uniquely, would be
    worse than refusing. The gap was the write_log INSERT itself: `record_db_before`
    is best-effort BY DEFAULT (a real in-turn write must never be blocked by a
    failing checkpoint hook), so an image that failed to record here used to be
    silently SWALLOWED — the pre-restore snapshot would finalise "successfully"
    missing a row it claimed to protect, and `restore()` would still go on to
    replay the target checkpoint, mutating live data out from under an incomplete
    undo. Passing `fail_closed=True` below closes that: a failing image capture now
    raises OUT of this function, out of `_capture_pre_restore_snapshot`, and out of
    `restore()` itself — BEFORE `_replay_write_log` or any file write runs — so the
    only failure mode left is "restore refuses, nothing touched," never "restore
    succeeds behind an undo that can't undo it." `record_db_before`'s DEFAULT
    (used by every real in-turn write, via the registered hook) is UNCHANGED — this
    is the one caller that opts out of best-effort, not a change to the contract
    every other caller relies on."""
    table_names = cp.get("db_tables") or []
    if not table_names:
        return
    from sqlalchemy import select

    from backend.db import tables as db_tables
    from backend.db.engine import get_engine

    wl = db_tables.write_log
    by_table: dict[str, list[str]] = defaultdict(list)
    with get_engine().connect() as conn:
        touched = conn.execute(
            select(wl.c.table_name, wl.c.pk)
            .where(wl.c.checkpoint_id.in_(_scope_ids(cp)), wl.c.table_name.in_(table_names))
            .distinct()
        ).all()
        for table_name, pk in touched:
            by_table[table_name].append(pk)
        for table_name, pks in by_table.items():
            table = db_tables.metadata.tables.get(table_name)
            if table is None:
                continue
            pk_col = _single_pk_col(table)
            current = {
                str(row._mapping[pk_col.name]): dict(row._mapping)
                for row in conn.execute(
                    select(table).where(pk_col.in_([_pk_param(pk_col, pk) for pk in pks]))
                )
            }
            ctx.record_db_before(
                table_name, [(pk, current.get(pk)) for pk in pks], fail_closed=True
            )


# ── Staged file restore (Plan 3 task 1 — restore hardening) ─────────────────────
# Splits the file half of a restore into STAGE (expensive: read the blob, decompress,
# write it out — done BEFORE the DB replay even starts) and COMMIT (cheap: one rename
# or unlink per file — done only AFTER the DB replay has committed). Before this split,
# the whole read+decompress+write sequence ran AFTER the DB commit, so a crash midway
# left the two worlds inconsistent (DB reverted, some-but-not-all files reverted) for as
# long as that sequence took. Staging first shrinks that window down to just the commit
# loop below — a handful of fast filesystem renames.


def _stage_restore_files(cp: dict, checkpoint_id: str) -> list[tuple[str, Path, Path | None]]:
    """Stage every file this restore will touch: a file that is being REPLACED (a
    non-None blob_ref) gets its restored bytes written NOW, durably, to a sibling
    temp path ``<dest>.restore-tmp-<checkpoint_id[:8]>`` (via ``_atomic_write_bytes``
    — fsync'd, so the staged bytes themselves survive a crash right after staging);
    a file that is being DELETED this restore (blob_ref is None — it did not exist
    before the turn `cp` captured) has nothing to stage, so its triple carries
    ``None`` for the temp path and the actual unlink happens in the commit phase,
    same as before staging existed (a single unlink was never the expensive half).

    Returns ``(name, dest, tmp_or_none)`` triples in ``cp["files"]`` order, for
    ``_commit_staged_restore_files`` to apply AFTER ``_replay_write_log`` succeeds.
    A staging failure (a missing/corrupt blob) propagates — cleaning up whatever
    this call had already staged first, via ``_cleanup_staged_restore_files`` — so
    a bad checkpoint aborts the restore at the SAME point a DB capture failure now
    does: before the DB replay ever runs, nothing touched."""
    staged: list[tuple[str, Path, Path | None]] = []
    try:
        for name, blob_ref in cp.get("files", {}).items():
            dest = _restore_dest(name)
            if blob_ref is None:
                staged.append((name, dest, None))
                continue
            tmp = dest.parent / f"{dest.name}.restore-tmp-{checkpoint_id[:8]}"
            _atomic_write_bytes(tmp, _blob_bytes(blob_ref))
            staged.append((name, dest, tmp))
    except BaseException:
        _cleanup_staged_restore_files(staged)
        raise
    return staged


def _assert_restorable_accounts(images: list[tuple[str, dict | None]]) -> None:
    """Refuse an `accounts` before-image whose `type` this build cannot classify.

    A checkpoint taken before the seven-type migration holds images typed `brokerage`,
    `savings`, `mortgage`… Replaying one puts a row back that `band_of` raises on, so every
    net-worth read fails afterwards with nothing pointing at the cause. Checked BEFORE the
    replay changes anything, so the whole restore aborts with both worlds untouched — the file phase
    runs only after this transaction commits."""
    from backend.core.errors import DataIntegrityError
    from backend.models.account import ACCOUNT_BAND

    for pk, before_payload in images:
        if before_payload is None:
            continue
        type_ = before_payload.get("type")
        if type_ not in ACCOUNT_BAND:
            raise DataIntegrityError(
                f"checkpoint holds account {pk!r} with unknown type {type_!r}; "
                f"this build knows {', '.join(sorted(ACCOUNT_BAND))}. This checkpoint predates "
                "the seven-type account schema (2026-09-07) and cannot be restored onto this "
                "build — nothing was changed."
            )


def _replay_write_log(cp_id: str, chain_ids: list[str] | None = None) -> None:
    """Replay `cp_id`'s write_log rows (DB pre-turn images, spec D7) inside ONE
    transaction — or, given ``chain_ids`` (oldest first), every one of those checkpoints'
    rows merged, each pk back to its image from the oldest that touched it (`_chain_images`),
    so a restore of a message and every later turn commits or fails as one. The row steps live in ``_replay.apply_images``: park unique columns,
    insert rows the turn deleted, UPDATE rows it edited IN PLACE, delete rows it created.
    It used to delete every touched pk and re-insert its image — which fired every FK
    from an UNTOUCHED row at an edited one: RESTRICT failed the restore (a transaction
    on an edited account), CASCADE silently took the children with it (2026-11-01).
    Parking keeps the permutation guarantee the old two-phase delete gave (I2): a turn
    whose write PERMUTED row order (``PayloadTableStore.replace_all([b, a])`` swapping
    two rows' ``seq``) restores without a unique-index collision.

    TABLE ORDER, ACROSS tables, is NOT arbitrary either (plan 3 task 4 — found the
    hard way, by a real cross-table restore, once the money core gave this mechanism
    its first REAL inter-table FK to contend with: ``transactions.to/from_account_id``
    -> ``accounts.id`` ON DELETE RESTRICT). Naive per-ROW ``seq DESC`` order (a single
    global sort mixing rows from every touched table) can delete a parent row (e.g.
    ``accounts``) while a not-yet-deleted child row (``transactions``) still RESTRICTs
    it — and, symmetric on the insert side, can re-insert a child row before the
    parent it FKs to exists again. Rows are grouped by table (preserving each
    table's own seq-desc row order — the permutation proof above still holds), and
    the TABLES themselves are ordered by ``metadata.sorted_tables`` — SQLAlchemy's
    own FK-dependency topological sort (parents before children; the order a fresh
    ``create_all`` would use). Inserts and updates walk it FORWARD (parents first, so
    no child can point at a not-yet-restored parent); deletes walk it in REVERSE
    (children first, so no parent delete is blocked by a still-live child). No schema in this app has a real FK cycle (the two
    circular-shaped relationships — ``accounts.opening_txn_id`` and
    ``transactions.merged_into`` — are deliberately NOT modeled as FKs for exactly
    this reason, per tables.py's own comments), so ``sorted_tables`` always succeeds.

    Raises on failure — restore() calls this BEFORE any file write, so a DB replay
    failure aborts the whole restore with nothing partially reverted across the two
    worlds."""
    from sqlalchemy import select

    from backend.db import tables as db_tables
    from backend.db.engine import locked_unit_of_work, unit_of_work

    wl = db_tables.write_log
    ids = chain_ids or [cp_id]
    # FR-M4 — hold the per-table write locks for exactly the tables this checkpoint touched
    # while the replay runs, so a restore is mutually excluded against a
    # live turn's locked_unit_of_work money choke (and any other single-writer table choke).
    # Discover the touched-table set with a short read FIRST — a committed checkpoint's
    # write_log rows are immutable, so the set can't change before the locked replay below
    # re-reads the full rows — then compose through locked_unit_of_work, which sorts the
    # tables into the one global (alphabetical) order every multi-table choke uses, so this
    # can never invert lock order against a concurrent money write. restore() holds no table
    # lock here (only checkpoint pins), so acquiring these fresh cannot self-deadlock.
    with unit_of_work() as conn:
        touched = {
            name
            for (name,) in conn.execute(
                select(wl.c.table_name).where(wl.c.checkpoint_id.in_(ids)).distinct()
            )
        }
    if not touched:
        return
    lock_tables = [t for t in db_tables.metadata.sorted_tables if t.name in touched]

    with locked_unit_of_work(*lock_tables) as conn:
        by_table = _chain_images(conn, ids)
        # Parents-before-children order; reversed for deletes, as-is otherwise — see
        # the TABLE ORDER docstring section above.
        table_order = [t.name for t in db_tables.metadata.sorted_tables if t.name in by_table]

        _assert_restorable_accounts(by_table.get("accounts") or [])
        txn_ids = [pk for pk, _ in by_table.get("transactions") or []]
        txn_images = _txn_images(txn_ids, conn)

        _replay.apply_images(conn, by_table, table_order)
        if txn_ids:
            _record_txn_reverts(cp_id, txn_images, _txn_images(txn_ids, conn), conn)


def _txn_images(txn_ids: list[str], conn) -> dict[str, dict | None]:
    from backend.db.transaction_store import TransactionStore

    store = TransactionStore()
    return {tid: store.find_by_id(tid, conn=conn) for tid in txn_ids}


def _record_txn_reverts(cp_id: str, current: dict, restored: dict, conn) -> None:
    """``txn_audit`` is checkpoint-excluded, so the replay leaves the reverted edit as each
    row's last audited word — append a ``revert`` per changed transaction, inside the
    replay's own transaction so the trail and the data land together."""
    from backend.services import audit

    cp = _index.find_checkpoint(cp_id) or {}
    note = "Undid a restore" if cp.get("kind") == "pre_restore" else "Restored to before a change"
    audit.record_txn_reverts(
        {tid: (current.get(tid), restored.get(tid)) for tid in current}, note=note, conn=conn
    )


def _commit_staged_restore_files(staged: list[tuple[str, Path, Path | None]]) -> list[str]:
    """Commit phase: swap every staged temp into place (``durable_replace`` — same
    fsync'd-rename durability the pre-staging single-step write already had), or
    delete the dest for a file that was new-this-turn (``tmp is None``). Called ONLY
    after ``_replay_write_log`` has committed, so this loop — not the whole stage
    sequence — is the entire remaining split-world window.

    RECOVERY if interrupted here (proven by
    ``test_staged_file_restore_survives_crash_between_db_commit_and_replace``): the
    DB half is already reverted; any file whose rename hasn't run yet still has its
    fully-staged temp sitting beside its untouched destination — nothing is lost or
    ambiguous. Simply calling ``restore()`` again for the same checkpoint finishes
    the job: `_replay_write_log` is naturally idempotent against a DB already in the
    reverted state (deleting an already-absent pk is a no-op, before-images
    re-insert to the same values), and re-staging overwrites the stale temp with the
    identical bytes before this loop re-runs to completion. An abandoned temp file
    is never read by anything else and is always safe to delete by hand."""
    # durable_replace is a monkeypatchable seam (a test makes it raise once, to simulate a
    # crash between the DB commit and the file replace) — read it via the package's OWN
    # attribute, not a bare top-level import, so a patch applied to the package is observed.
    from backend.ai import checkpoint as _ckpt_pkg

    restored_files: list[str] = []
    for name, dest, tmp in staged:
        if tmp is None:
            dest.unlink(missing_ok=True)
        else:
            _ckpt_pkg.durable_replace(tmp, dest)
        restored_files.append(name)
    return restored_files


def _cleanup_staged_restore_files(staged: list[tuple[str, Path, Path | None]]) -> None:
    """Unlink every staged temp. Called when staging itself fails partway (cleans up
    what that same call already staged) or when the DB replay fails AFTER staging
    completed (Plan-2 spec D7 amendment's hardening item: "an image that cannot be
    recorded aborts the restore before any mutation" — this is the file-side twin,
    so a DB-replay failure leaves no temp files behind either, only the destination
    files, completely untouched)."""
    for _name, _dest, tmp in staged:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _truncate_session_history(session_id: str, anchor_len: int) -> int | None:
    """Rewind a session's chat history to `anchor_len` messages. Lazy-imports
    assistant to avoid a circular import."""
    try:
        from backend.ai import assistant as asst

        sid, history = asst.get_or_create_session(session_id)
        new_history = history[:anchor_len]
        asst.SESSIONS[sid] = new_history
        asst._save_session(sid, new_history)
        return len(new_history)
    except Exception:
        logger.exception("conversation rewind failed for %s", session_id)
        return None


# ── Maintenance: blob + write_log GC ─────────────────────────────────────────


def _gc_blobs(index: list[dict]) -> None:
    """Delete blob files no surviving manifest references.

    Skips entirely while any PINNED id (I2) has no entry in ``index`` yet — an
    in-flight turn's own blobs are not discoverable here until its OWN finalise()
    adds its ``files`` map to the index, so there is no per-blob way to protect
    them individually. Deferring the whole pass is the safe, conservative choice:
    it costs at most a delayed GC (the next prune retries once nothing is
    ambiguous), never a wrongly-deleted blob a not-yet-finalized pinned turn
    already wrote.

    Logs the blocking id(s) at WARNING (Plan 3 task 1 routed fix) — a defer that
    resolves on the next prune is normal and expected, but a defer that NEVER
    resolves (a pin leaked by a caller that forgot to unpin) used to be
    indistinguishable from routine deferral: both silently skipped GC forever. The
    log line is what makes a stuck pin discoverable instead of a slow blob-pool leak
    nobody notices until disk usage does.
    """
    # CHECKPOINTS_BLOBS is a monkeypatchable seam (see _blob_path above) — read it via
    # the package's OWN attribute for the same reason.
    from backend.ai import checkpoint as _ckpt_pkg

    indexed_ids = {e["id"] for e in index}
    blocking = sorted(
        pinned_id for pinned_id in _context._pinned_ids if pinned_id not in indexed_ids
    )
    if blocking:
        logger.warning("blob GC deferred: checkpoint id(s) %s pinned but not yet indexed", blocking)
        return
    referenced: set[str] = set()
    for e in index:
        for blob_ref in e.get("files", {}).values():
            if blob_ref:
                referenced.add(blob_ref.split(":", 1)[1] + ".gz")
    try:
        for blob in _ckpt_pkg.CHECKPOINTS_BLOBS.iterdir():
            if blob.is_file() and blob.name not in referenced:
                blob.unlink(missing_ok=True)
    except Exception:
        logger.exception("blob GC failed")


def _gc_write_log(index: list[dict]) -> None:
    """Delete write_log rows for checkpoints no surviving manifest entry references
    (spec D7). Same posture as _gc_blobs: best-effort, must never break pruning.

    PINNED ids (I2) are kept even with NO index entry at all — an in-flight turn's
    write_log rows are durable from the moment ``record_db_before`` first fires,
    well before its own finalise() ever adds it to the index; without this, a
    different session's finalise()/prune at cap could delete them out from under a
    turn that is still being written (shape b)."""
    keep_ids = {e["id"] for e in index} | set(_context._pinned_ids)
    try:
        from backend.db import tables as db_tables
        from backend.db.engine import get_engine

        wl = db_tables.write_log
        with get_engine().begin() as conn:
            conn.execute(wl.delete().where(wl.c.checkpoint_id.notin_(keep_ids)))
    except Exception:
        logger.exception("write_log GC failed")
