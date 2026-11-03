"""Checkpoint index — the manifest of every checkpoint (spec D7), on
``CheckpointIndexStore`` (plan 4e, task 2).

Replaces the old ``CHECKPOINTS_INDEX`` JSON file (``DATA_DIR/checkpoints/index.json``):
``_read_index``/``_write_index``/``_append_index``/the module-level path are gone from the
call sites that actually mutate the index — ``finalise()`` (_context.py) inserts one row,
``find_checkpoint`` looks up one row by id, ``_mark_orphaned_after`` patches matching rows
in place, ``_prune_old_checkpoints``/``delete_checkpoint`` bulk-delete by id-set — instead of
every mutation reading the WHOLE list, editing it in Python, and writing the WHOLE list back.
``_read_index``/``_write_index`` themselves still exist (thin wrappers over
``CheckpointIndexStore.all()``/``.replace_all()``) — every remaining READ-ALL call site in
this module keeps using them, and a couple of existing tests poke them directly to simulate
an out-of-band index edit.

``payload`` is the whole entry dict verbatim — no promoted columns (the table is hard-capped
at ``MAX_CHECKPOINTS`` active checkpoints plus a bounded pinned/orphaned tail, so
``PayloadTableStore``'s Python-side ``query()``/``all()`` filtering is the right call here,
not a shortcut).

``_index_lock`` (the ``threading.Lock``, unchanged) still serialises the *compound*
sequences below (`finalise()`'s insert-then-prune; `_prune_old_checkpoints`'s
read-all→compute-keep-set→bulk-delete) against concurrent turns — the store's own per-table
lock (``CheckpointIndexStore._lock``, taken fresh inside EACH individual call) only ever
covers ONE call, not a multi-call sequence.

Split out of the former flat ``backend/ai/checkpoint.py`` (plan 4e, task 2) — pure code
motion for the query/prune/orphan surface; the index-to-DB swap is the one real behavior
change (module docstring above). See ``backend/ai/checkpoint/__init__.py`` for the
package's own mechanism docstring and the compatibility re-export shim.
"""

from __future__ import annotations

import threading

from sqlalchemy import delete as sa_delete

from backend.db.checkpoint_index_store import CheckpointIndexStore
from backend.db.tables import checkpoints as _checkpoints_table

from . import _context, _restore

_store = CheckpointIndexStore()

# Serialises all read-modify-write cycles on the checkpoint index. Concurrent turns
# (user + background agent, or two headless agents) can both call finalise() ->
# _append_index at the same time; without the lock the second write could interleave
# with the first's own insert-then-prune sequence. CheckpointIndexStore's own per-table
# lock (taken inside each individual store call) prevents a torn ROW; this lock is what
# prevents a torn SEQUENCE of calls.
_index_lock: threading.Lock = threading.Lock()


def _read_index() -> list[dict]:
    """Every current index entry, in insertion order. A thin wrapper over
    ``CheckpointIndexStore.all()`` — kept as a named function because every read-all call
    site in this module already called it before the DB cutover, and one existing test
    (``test_checkpoint_hook.py``) pokes it directly."""
    return _store.all()


def _write_index(index: list[dict]) -> None:
    """Whole-list overwrite. Nothing in this module's OWN logic calls this anymore — every
    real mutator below uses a targeted ``CheckpointIndexStore`` call instead (insert /
    update_by_id / bulk-delete) — but it stays as a real, working compat surface: an
    existing test (``test_checkpoint_hook.py``) uses it to simulate an out-of-band index
    edit (marking a checkpoint orphaned without going through a real restore)."""
    _store.replace_all(index)


def _append_index(entry: dict) -> None:
    """Insert one new manifest entry. Must be called while `_index_lock` is held. Called
    by `CheckpointContext.finalise()` (_context.py)."""
    _store.insert(entry)


# ── Query ─────────────────────────────────────────────────────────────────────


def _public(entry: dict) -> dict:
    """Manifest as exposed to API/UI (file list + change counts, no blob hashes)."""
    return {
        "id": entry["id"],
        "session_id": entry.get("session_id"),
        "created_at": entry.get("created_at"),
        "message_preview": entry.get("message_preview"),
        "files": list(entry.get("files", {}).keys()),
        "status": entry.get("status", "active"),
        "kind": entry.get("kind", "turn"),
    }


def _filtered_checkpoints(
    session_id: str | None = None, include_orphaned: bool = False
) -> list[dict]:
    """The shared, unlimited filter pass behind ``list_checkpoints``/``count_checkpoints`` —
    one implementation so a reported total can never disagree with the page it describes."""
    items = _read_index()
    if session_id is not None:
        items = [e for e in items if e.get("session_id") == session_id]
    if not include_orphaned:
        items = [e for e in items if e.get("status") == "active"]
    items.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return items


def list_checkpoints(
    session_id: str | None = None, limit: int = 50, include_orphaned: bool = False
) -> list[dict]:
    items = _filtered_checkpoints(session_id, include_orphaned)
    return [_public(e) for e in items[:limit]]


def count_checkpoints(session_id: str | None = None, include_orphaned: bool = False) -> int:
    """How many checkpoints match, ignoring limit — the denominator a capped
    ``list_checkpoints`` page must report. Active checkpoints are hard-capped at
    ``MAX_CHECKPOINTS`` (oldest pruned), so this is never an unbounded read."""
    return len(_filtered_checkpoints(session_id, include_orphaned))


def find_checkpoint(checkpoint_id: str) -> dict | None:
    return _store.find_by_id(checkpoint_id)


def turn_anchors(session_id: str) -> dict[int, str]:
    """Map anchor_history_len -> checkpoint id for this session's active "turn"
    checkpoints. Used to re-attach Restore anchors when a conversation is rebuilt
    from its transcript (assistant.rehydrate_messages); anchor_history_len is the
    raw transcript index of the user message that opened the turn. One message can hold
    several (an approved card's write and the turn it resumed); any of them restores the
    whole message. A message rewound out of the chat no longer owns its index."""
    out: dict[int, str] = {}
    for e in _read_index():
        if (
            e.get("session_id") == session_id
            and e.get("kind", "turn") == "turn"
            and e.get("status", "active") == "active"
            and not e.get("rewound")
        ):
            anchor = e.get("anchor_history_len")
            if isinstance(anchor, int):
                out[anchor] = e["id"]
    return out


def turn_receipts(session_id: str) -> dict[int, dict]:
    """Map anchor_history_len -> the turn's write-receipt chip payload, for the turns of
    this session that wrote to the ledger. The reload-safe half of the chip: it is rebuilt
    from here by ``assistant.rehydrate_messages`` exactly as the Restore anchor already is,
    so undo survives a reload and reaches other devices. A turn that wrote nothing has no
    entry (and renders no chip).

    Undo is DURABLE, not eternal — do not state otherwise. Receipts are read off the
    checkpoint index, which ``_prune_old_checkpoints`` caps at ``MAX_CHECKPOINTS``; once a
    turn's checkpoint is evicted its receipt disappears from this map (so a rehydrated chip
    simply stops offering an undo it cannot perform), and a chip still on screen from a live
    turn discovers the eviction as a 404 from the restore route and degrades to "Undo window
    passed" rather than presenting a button that fails. Undo is what stands in for consent
    in this design, so a promise it cannot keep is the one thing it must not make."""
    out: dict[int, dict] = {}
    for e in _read_index():
        if (
            e.get("session_id") == session_id
            and e.get("kind", "turn") == "turn"
            and e.get("status", "active") == "active"
            and not e.get("rewound")
        ):
            anchor = e.get("anchor_history_len")
            receipt = e.get("receipt")
            if isinstance(anchor, int) and isinstance(receipt, dict):
                out[anchor] = {**receipt, "checkpoint_id": e["id"]}
    return out


def turn_restores(session_id: str) -> dict[str, str | None]:
    """Map turn checkpoint id -> its undo (pre_restore) checkpoint id, for the turns of this
    session whose data is currently restored to before them. The reload-safe half of
    "Restored to before this message · Undo", read beside ``turn_anchors``. The undo id is
    None once that pre_restore checkpoint has been pruned: the message still reads as
    restored, but offers no Undo it cannot perform."""
    index = _read_index()
    present = {e["id"] for e in index}
    out: dict[str, str | None] = {}
    for e in index:
        mark = e.get("restored")
        if (
            isinstance(mark, dict)
            and e.get("session_id") == session_id
            and e.get("kind", "turn") == "turn"
            and e.get("status", "active") == "active"
        ):
            undo = mark.get("undo_checkpoint_id")
            out[e["id"]] = undo if undo in present else None
    return out


def _mark_restored(checkpoint_id: str, undo_checkpoint_id: str | None) -> None:
    """Record on a turn checkpoint that its data was restored, and which checkpoint undoes it."""
    with _index_lock:
        _store.update_by_id(checkpoint_id, {"restored": {"undo_checkpoint_id": undo_checkpoint_id}})


def _mark_rewound(session_id: str, anchor_len: int) -> None:
    """The chat was rewound to ``anchor_len``: every turn of this session anchored at or after
    it lost its message, and the next message reuses that index. Flag them so no Restore
    button, receipt or same-message restore chain (`_restore._restore_chain`) mistakes them
    for the new message's turns — they keep their status, so the data they still hold stays
    reachable by restoring an earlier message."""
    with _index_lock:
        _store.bulk_patch(
            {
                e["id"]: {"rewound": True}
                for e in _read_index()
                if e.get("session_id") == session_id
                and e.get("kind", "turn") == "turn"
                and isinstance(e.get("anchor_history_len"), int)
                and e["anchor_history_len"] >= anchor_len
            }
        )


def _clear_restores_undone_by(undo_checkpoint_id: str) -> None:
    """Restoring a pre_restore checkpoint IS the undo of the restore that took it — the turn
    it was taken for reads as un-restored again."""
    with _index_lock:
        _store.bulk_patch(
            {
                e["id"]: {"restored": None}
                for e in _read_index()
                if (e.get("restored") or {}).get("undo_checkpoint_id") == undo_checkpoint_id
            }
        )


# ── Maintenance: pruning + orphaning + blob/write_log GC ──────────────────────


def delete_checkpoint(checkpoint_id: str) -> bool:
    with _index_lock:
        deleted = _store.delete(checkpoint_id)
        if not deleted:
            return False
        remaining = _read_index()
        _restore._gc_blobs(remaining)
        _restore._gc_write_log(remaining)
    return True


def _prune_old_checkpoints() -> None:
    """Keep the newest MAX_CHECKPOINTS active checkpoints; drop older ones and any
    orphaned ones beyond the cap. Then GC unreferenced blobs/write_log rows.

    Must be called while ``_index_lock`` is held (called from ``finalise``).

    PINNED ids (``_context._pinned_ids``, I2) are ALWAYS kept, regardless of recency — this
    is what stops restoring the OLDEST of MAX_CHECKPOINTS active checkpoints from
    self-destructing: without it, capturing the pre_restore snapshot appended one
    more active checkpoint, and this sort+cap dropped the restore TARGET (now
    (MAX_CHECKPOINTS + 1)-th newest) from the index before the replay that follows
    ever got a chance to read its write_log rows.

    Bulk-deletes the dropped id-set in ONE statement (``_use_conn(None)`` + ``delete()
    .where(id.in_(...))`` — the same idiom Task 1's ``delete_for_session``/
    ``prune_unreferenced`` established), not a whole-table ``replace_all`` — the table can
    hold pinned/orphaned entries beyond the active cap, so this is a genuine subset
    delete, not a full rewrite.
    """
    # MAX_CHECKPOINTS is a monkeypatchable seam (tests set cp.MAX_CHECKPOINTS to shrink
    # the cap) — read it via the package's OWN attribute, not a bare import, so a patch
    # applied to the package is observed (this submodule's own copy would be a separate,
    # un-patched binding, since _policy.MAX_CHECKPOINTS is never itself reassigned).
    from backend.ai import checkpoint as _ckpt_pkg

    index = _read_index()
    active = [e for e in index if e.get("status") == "active"]
    active.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    keep_ids = {e["id"] for e in active[: _ckpt_pkg.MAX_CHECKPOINTS]}
    # keep pinned entries that are still in the index; drop the rest
    keep_ids |= {e["id"] for e in index if e["id"] in _context._pinned_ids}
    # keep the turns a kept undo checkpoint would revive — see `_revive_superseded`
    keep_ids |= _revivable_ids(index, keep_ids)
    to_drop = [e for e in index if e["id"] not in keep_ids]
    if not to_drop:
        return
    drop_ids = [e["id"] for e in to_drop]
    with _store._use_conn(None) as conn:
        # _fire_hook wants the FULL {seq, id, payload} row shape (what a replay would
        # `.values(**before_payload)` back in), not the bare payload _read_index() above
        # returns — _rows_map gets that, inside the SAME transaction as the delete below
        # so it sees the pre-delete state. Always a no-op here (checkpointed=False), but
        # uniform with every other bulk-delete's hook call rather than a special case.
        before = _store._rows_map(conn, drop_ids)
        _store._fire_hook(conn, [(i, before[i]) for i in drop_ids])
        conn.execute(sa_delete(_checkpoints_table).where(_checkpoints_table.c.id.in_(drop_ids)))
    new_index = [e for e in index if e["id"] in keep_ids]
    _restore._gc_blobs(new_index)
    _restore._gc_write_log(new_index)


def _mark_orphaned_after(cp: dict, revive_holder: str | None = None) -> list[str]:
    """Mark checkpoints from the same session newer than `cp` as orphaned —
    their captured state no longer lies on the live timeline. A targeted
    ``bulk_patch`` of just the matching rows, in ONE transaction — not a
    read-all/mutate/write-all-back over the whole table, but still every
    orphaned row lands together (matching the old whole-file
    ``atomic_write_text``'s all-or-nothing guarantee), not one independent
    transaction per row.

    ``revive_holder`` is the restore's own pre_restore (undo) checkpoint: the orphaned ids
    are recorded on it as ``revives`` IN THE SAME TRANSACTION, so undoing the restore can
    put exactly these turns back (`_revive_superseded`). Returns the orphaned ids."""
    with _index_lock:
        cutoff = cp.get("created_at", "")
        sid = cp.get("session_id")
        to_orphan = {
            e["id"]: {"status": "orphaned"}
            for e in _read_index()
            if (
                e.get("session_id") == sid
                and e.get("kind") == "turn"
                and e.get("status") == "active"
                and e.get("created_at", "") > cutoff
            )
        }
        orphaned = list(to_orphan)
        if revive_holder and orphaned:
            to_orphan[revive_holder] = {"revives": orphaned}
        _store.bulk_patch(to_orphan)
    return orphaned


def _revive_superseded(undo_checkpoint: dict) -> list[str]:
    """Restoring a pre_restore checkpoint undoes the restore that took it, so the turns that
    restore orphaned are on the live timeline again: set exactly those back to active. A
    turn already pruned, or no longer orphaned, is left alone. Returns the revived ids."""
    wanted = set(undo_checkpoint.get("revives") or [])
    if not wanted:
        return []
    with _index_lock:
        revived = {
            e["id"]: {"status": "active"}
            for e in _read_index()
            if e["id"] in wanted and e.get("status") == "orphaned"
        }
        _store.bulk_patch(revived)
    return list(revived)


def _revivable_ids(index: list[dict], holder_ids: set[str]) -> set[str]:
    """Orphaned turns some checkpoint in ``holder_ids`` would revive if restored. Pruning
    keeps them for as long as that undo checkpoint lives — the undo's OWN pre_restore
    finalise() prunes before the revive runs — and lets them go with it."""
    referenced = {rid for e in index if e["id"] in holder_ids for rid in (e.get("revives") or [])}
    return {e["id"] for e in index if e["id"] in referenced and e.get("status") == "orphaned"}


def turn_superseded(session_id: str) -> dict[int, str]:
    """Map anchor_history_len -> checkpoint id for this session's turns that a later restore
    orphaned but whose undo would revive. Rehydration shows their message as superseded —
    no Restore while the restore stands — instead of dropping the checkpoint outright, so
    every device agrees with the tab that clicked Restore, and Undo brings it back."""
    index = _read_index()
    revivable = _revivable_ids(index, {e["id"] for e in index})
    out: dict[int, str] = {}
    for e in index:
        if e["id"] in revivable and e.get("session_id") == session_id and not e.get("rewound"):
            anchor = e.get("anchor_history_len")
            if isinstance(anchor, int):
                out[anchor] = e["id"]
    return out
