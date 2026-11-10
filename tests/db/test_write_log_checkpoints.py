"""write_log — row-level checkpoints for DB stores (spec D7, plan 2 task 2).

Replaces whole-file blob snapshots for DB-backed stores: a checkpoint records the
BEFORE image of each (table, pk) a turn touches, and restore replays those images
in one transaction. Must coexist with the file-blob mechanism during the hybrid
period (one turn can write both a JSON store and a DB store; a single restore must
revert both or neither).
"""

from __future__ import annotations


def _turn(session="s1"):
    from backend.ai import checkpoint as cp

    return cp.begin_turn(session, 0, "test turn")


def test_db_write_recorded_and_restored(db_isolated):
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(id="book_x", name="Before", base_currency="", created_at="")
        )
    ctx, token = _turn()
    fire_db_write_hook(
        "books",
        [("book_x", {"id": "book_x", "name": "Before", "base_currency": "", "created_at": ""})],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.update().where(t.books.c.id == "book_x").values(name="After"))
    entry = cp.end_turn(ctx, token)
    assert "books" in entry["db_tables"]
    cp.restore(entry["id"], mode="data")
    with eng.get_engine().connect() as c:
        assert (
            c.execute(select(t.books.c.name).where(t.books.c.id == "book_x")).scalar() == "Before"
        )


def test_insert_restores_to_absent(db_isolated):
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    ctx, token = _turn()
    fire_db_write_hook("books", [("book_new", None)])  # row did not exist pre-turn
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_new", name="N", base_currency="", created_at=""))
    entry = cp.end_turn(ctx, token)
    cp.restore(entry["id"], mode="data")
    with eng.get_engine().connect() as c:
        assert c.execute(select(t.books).where(t.books.c.id == "book_new")).fetchall() == []


def test_excluded_table_not_captured(db_isolated):
    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook

    ctx, token = _turn()
    fire_db_write_hook("evidence", [("ev1", None)])
    entry = cp.end_turn(ctx, token)
    assert entry is None or "evidence" not in entry.get("db_tables", [])


def test_mixed_turn_file_and_db_restore_together(db_isolated, tmp_path, monkeypatch):
    """The hybrid contract: one turn writes a JSON store AND a DB row; restore reverts both."""
    import json

    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook, fire_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    f = cp.DATA_DIR / "mixed_probe.json"
    f.write_text(json.dumps([{"id": "old"}]))
    ctx, token = _turn()
    fire_write_hook(f)  # file half
    f.write_text(json.dumps([{"id": "new"}]))
    fire_db_write_hook("books", [("book_m", None)])  # db half
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_m", name="M", base_currency="", created_at=""))
    entry = cp.end_turn(ctx, token)
    cp.restore(entry["id"], mode="data")
    assert json.loads(f.read_text()) == [{"id": "old"}]
    with eng.get_engine().connect() as c:
        assert c.execute(select(t.books).where(t.books.c.id == "book_m")).fetchall() == []
    f.unlink()


def test_a_transactions_write_also_captures_the_opening_pointer_row_no_companions_map_needed(
    db_isolated,
):
    """The DB-era proof of the invariant ``test_checkpoint_hook.py``'s three now-retired
    ``_COMPANIONS`` tests used to characterize at the file level (plan 3 task 4): an
    account's opening is a POINTER (``opening_txn_id``) into a transaction row, so a
    restore that reverts the transaction without also reverting the account leaves a
    dangling pointer — every money read then raises. ``ai.checkpoint._COMPANIONS`` is now
    permanently empty (accounts/transactions are DB-backed; row-level write_log images
    capture every touched row in every table a turn touches, with no table-name pairing
    map needed) — this proves that claim directly: a SINGLE turn that composes an
    ``accounts`` write and a ``transactions`` write via ``locked_unit_of_work`` (the exact
    shape ``services.ledger.set_account_opening`` uses) captures BOTH tables' before-images,
    and restoring reverts both together, leaving no dangling pointer."""
    from backend.ai import checkpoint as cp
    from backend.db.account_store import AccountStore
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import accounts as accounts_table
    from backend.db.tables import transactions as transactions_table
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    transactions = TransactionStore()

    # Setup (outside any turn): an account with no opening yet.
    acct = accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "SGD"})

    # One turn: mint the Starting Balance transaction AND point the account at it — the
    # same two-table shape set_account_opening uses, composed through locked_unit_of_work.
    ctx, token = _turn()
    with locked_unit_of_work(accounts_table, transactions_table) as conn:
        opening = transactions.append(
            {
                "date": "2026-01-01",
                "kind": "adjustment",
                "description": "Starting Balance",
                "to_account_id": acct["id"],
                "to_amount": 100.0,
                "to_currency": "SGD",
                "cleared": True,
            },
            conn=conn,
        )
        accounts.update_by_id(acct["id"], {"opening_txn_id": opening["id"]}, conn=conn)
    entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert {"accounts", "transactions"} <= set(entry["db_tables"])
    # Live (pre-restore): the pointer resolves.
    assert accounts.find_by_id(acct["id"])["opening_txn_id"] == opening["id"]
    assert transactions.find_by_id(opening["id"]) is not None

    cp.restore(entry["id"], mode="data")

    reverted_acct = accounts.find_by_id(acct["id"])
    pointer = reverted_acct.get("opening_txn_id")
    live_txn_ids = {t["id"] for t in transactions.all()}
    assert pointer is None or pointer in live_txn_ids, f"dangling pointer after restore: {pointer}"
    # The stronger, exact claim for this fixture: the account reverts to no opening at
    # all (it had none before the turn) and the freshly-minted transaction is gone too —
    # not merely "not dangling" but genuinely the pre-turn state, on BOTH tables.
    assert pointer is None
    assert transactions.find_by_id(opening["id"]) is None


def test_before_images_durable_without_finalise(db_isolated):
    """Crash parity with file blobs: hook-time images are in write_log even if the
    turn never finalises."""
    from sqlalchemy import func, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    ctx, token = _turn()
    fire_db_write_hook("books", [("book_c", None)])
    with eng.get_engine().connect() as c:
        n = c.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.checkpoint_id == ctx.id)
        ).scalar_one()
    assert n == 1
    cp.end_turn(ctx, token)


def test_pre_restore_makes_db_restore_undoable(db_isolated):
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    ctx, token = _turn()
    fire_db_write_hook("books", [("book_u", None)])
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_u", name="U", base_currency="", created_at=""))
    entry = cp.end_turn(ctx, token)
    result = cp.restore(entry["id"], mode="data")  # row deleted
    cp.restore(result["undo_checkpoint_id"], mode="data")  # restore the restore
    with eng.get_engine().connect() as c:
        assert c.execute(select(t.books.c.name).where(t.books.c.id == "book_u")).scalar() == "U"


# ── write_log GC (Produces block: "same lock, best-effort ... like blob GC") ───
# Not among the brief's 7, but the GC contract is binding and otherwise unverified.


def test_delete_checkpoint_gcs_write_log(db_isolated):
    from sqlalchemy import func, insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_gc", name="G", base_currency="", created_at=""))
    ctx, token = _turn()
    fire_db_write_hook("books", [("book_gc", None)])
    entry = cp.end_turn(ctx, token)
    assert entry is not None

    assert cp.delete_checkpoint(entry["id"]) is True

    with eng.get_engine().connect() as c:
        n_after = c.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.checkpoint_id == entry["id"])
        ).scalar_one()
    assert n_after == 0, "delete_checkpoint must GC the write_log rows too"


def test_prune_old_checkpoints_gcs_write_log(db_isolated, monkeypatch):
    from sqlalchemy import func, insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    monkeypatch.setattr(cp, "MAX_CHECKPOINTS", 1)

    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_gc2", name="G", base_currency="", created_at=""))
    ctx1, tok1 = _turn()
    fire_db_write_hook("books", [("book_gc2", None)])
    entry1 = cp.end_turn(ctx1, tok1)
    assert entry1 is not None

    # A second checkpoint pushes the first one past MAX_CHECKPOINTS=1 -> pruned.
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_gc3", name="G3", base_currency="", created_at=""))
    ctx2, tok2 = _turn()
    fire_db_write_hook("books", [("book_gc3", None)])
    entry2 = cp.end_turn(ctx2, tok2)
    assert entry2 is not None

    assert cp.find_checkpoint(entry1["id"]) is None, "sanity: first checkpoint pruned from index"

    with eng.get_engine().connect() as c:
        n1 = c.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.checkpoint_id == entry1["id"])
        ).scalar_one()
        n2 = c.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.checkpoint_id == entry2["id"])
        ).scalar_one()
    assert n1 == 0, "pruned checkpoint's write_log rows must be GC'd"
    assert n2 == 1, "surviving checkpoint's write_log rows must remain"


# ── Review fix round 1: 3 Important findings, all reproduced against real code ──


def test_hook_inside_open_transaction_with_conn_is_atomic(db_isolated):
    """Finding 1: a conn-less hook fired inside an already-open write transaction
    stalls on SQLite's single-writer slot and the swallow then silently drops the
    image (no checkpoint minted for a real change). Fix: the caller passes its own
    open connection through `conn=`, so the image commits atomically with the
    caller's write and there is no second transaction to stall on. Also proves
    rollback symmetry: if the caller's transaction rolls back, neither its write
    nor the write_log image survives."""
    import time

    import pytest
    from sqlalchemy import func, insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    # Commit path: fire the hook AFTER the write statement, on the SAME open txn
    # (the previously-hazardous shape) — must not stall and must commit together.
    ctx, token = _turn()
    started = time.monotonic()
    with eng.unit_of_work() as conn:
        conn.execute(
            insert(t.books).values(id="book_atomic", name="A", base_currency="", created_at="")
        )
        fire_db_write_hook("books", [("book_atomic", None)], conn=conn)
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"hook-in-open-txn stalled: {elapsed:.2f}s"
    entry = cp.end_turn(ctx, token)
    assert entry is not None and "books" in entry["db_tables"]
    with eng.get_engine().connect() as c:
        n = c.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.checkpoint_id == ctx.id)
        ).scalar_one()
    assert n == 1

    # Rollback symmetry: same shape, but the caller's transaction rolls back ->
    # neither the books row nor the write_log image should persist.
    ctx2, token2 = _turn()
    with pytest.raises(RuntimeError):
        with eng.unit_of_work() as conn:
            conn.execute(
                insert(t.books).values(id="book_rb", name="R", base_currency="", created_at="")
            )
            fire_db_write_hook("books", [("book_rb", None)], conn=conn)
            raise RuntimeError("simulated failure inside the caller's transaction")
    cp.end_turn(ctx2, token2)
    with eng.get_engine().connect() as c:
        book_rows = c.execute(select(t.books).where(t.books.c.id == "book_rb")).fetchall()
        wl_rows = c.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.checkpoint_id == ctx2.id)
        ).scalar_one()
    assert book_rows == [], "the caller's rolled-back write must not persist"
    assert wl_rows == 0, "a write_log image inserted through a rolled-back conn must not persist"


def test_write_log_insert_failure_does_not_poison_the_callers_transaction(db_isolated):
    """Plan-2 final review item 1: the FIRST genuine `tests/db` run against real
    Postgres surfaced this — `record_db_before`'s write_log insert, given the
    caller's own OPEN transaction, ran as a bare statement. On SQLite a failed
    statement inside an open transaction is harmless to what follows; on Postgres
    ANY statement failure aborts the WHOLE transaction, so the very next statement
    on the same `conn` (the caller's own real, otherwise-valid data write) died
    too, with an unrelated-looking `InFailedSqlTransaction` — silently breaking
    the "a failing hook must never block a real write" guarantee, Postgres-only.

    Reproduced here with the REAL `record_db_before` path (not raw SQL): pre-seed
    a write_log row at the exact (checkpoint_id, seq) this turn's first
    `record_db_before` call will use, forcing ITS insert to collide and fail; then
    prove a subsequent, unrelated statement on the SAME connection still succeeds.
    SAVEPOINT-isolation (`conn.begin_nested()`) is the fix."""
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    ctx, token = _turn()
    # ctx's own _db_seq starts at 0 and increments to 1 on its first touched pk —
    # pre-seed exactly that (checkpoint_id, seq) so record_db_before's own insert
    # collides on write_log's primary key.
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.write_log).values(
                checkpoint_id=ctx.id,
                seq=1,
                table_name="books",
                pk="collider",
                before_payload=None,
                created_at="",
            )
        )

    with eng.unit_of_work() as conn:
        conn.execute(
            insert(t.books).values(id="book_poison", name="Z", base_currency="", created_at="")
        )
        # Fires record_db_before(conn=conn) internally; its write_log insert
        # collides with the pre-seeded row above and must be swallowed WITHOUT
        # poisoning `conn` for the statement that follows.
        fire_db_write_hook("books", [("book_poison", None)], conn=conn)
        # Must succeed — proof the caller's transaction survived the swallow.
        conn.execute(t.books.update().where(t.books.c.id == "book_poison").values(name="Z2"))

    cp.end_turn(ctx, token)
    with eng.get_engine().connect() as c:
        assert (
            c.execute(select(t.books.c.name).where(t.books.c.id == "book_poison")).scalar() == "Z2"
        ), "the caller's own valid write must survive a swallowed write_log collision"


def test_composite_pk_table_raises_loud_guard_on_replay(db_isolated):
    """Finding 2: the replay/preview/pre-restore helpers all assumed a single-column
    PK — on a composite-PK table (e.g. book_members) this DELETEs every row sharing
    the recorded pk fragment, not just the intended one. Fix: _single_pk_col raises
    LOUDLY instead. Seeds two book_members rows sharing a user_id (the reviewer's
    exact scenario) and proves restore() refuses rather than deleting either."""
    import pytest
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    # A second book_members row sharing the default user's user_id — a
    # pk-column-only DELETE (without the guard) would remove both.
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_002", name="B2", base_currency="", created_at=""))
        c.execute(
            insert(t.book_members).values(
                user_id=t.DEFAULT_USER_ID, book_id="book_002", role="member"
            )
        )
    with eng.get_engine().connect() as c:
        before = c.execute(select(t.book_members)).fetchall()
    assert len(before) == 2, "sanity: two book_members rows share the default user_id"

    ctx, token = _turn()
    fire_db_write_hook(
        "book_members",
        [
            (
                t.DEFAULT_USER_ID,
                {"user_id": t.DEFAULT_USER_ID, "book_id": t.DEFAULT_BOOK_ID, "role": "owner"},
            )
        ],
    )
    entry = cp.end_turn(ctx, token)
    assert entry is not None and "book_members" in entry["db_tables"]

    with pytest.raises(RuntimeError, match="single-column"):
        cp.restore(entry["id"], mode="data")

    with eng.get_engine().connect() as c:
        after = c.execute(select(t.book_members)).fetchall()
    assert after == before, "a failed replay must not delete any book_members row"


def test_preview_db_deltas(db_isolated):
    """Finding 3: preview()'s db-delta arithmetic had no test. Covers the three
    real cases through the actual turn/hook/preview path: an inserted row still
    present (restore would delete it -> delta -1), an updated row still present
    (restore only changes content -> delta 0), and a deleted row now absent
    (restore would re-insert it -> delta +1). Each case is its own conversation: a restore
    also undoes the later turns of the SAME one, which would fold the cases together."""
    from sqlalchemy import insert

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    def _db_row(pv):
        return next(r for r in pv["changes"] if r["file"] == "db:books")

    # Case 1: insert, still present -> restore DELETEs it -> delta -1.
    ctx1, tok1 = _turn(session="s-preview-1")
    fire_db_write_hook("books", [("book_ins", None)])
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_ins", name="I", base_currency="", created_at=""))
    entry1 = cp.end_turn(ctx1, tok1)
    row1 = _db_row(cp.preview(entry1["id"]))
    assert row1 == {
        "file": "db:books",
        "current_count": row1["current_count"],
        "restored_count": row1["current_count"] - 1,
        "delta": -1,
    }

    # Case 2: update an existing row, still present -> restore only changes
    # content -> delta 0.
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(id="book_upd", name="Before", base_currency="", created_at="")
        )
    ctx2, tok2 = _turn(session="s-preview-2")
    fire_db_write_hook(
        "books",
        [
            (
                "book_upd",
                {"id": "book_upd", "name": "Before", "base_currency": "", "created_at": ""},
            )
        ],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.update().where(t.books.c.id == "book_upd").values(name="After"))
    entry2 = cp.end_turn(ctx2, tok2)
    row2 = _db_row(cp.preview(entry2["id"]))
    assert row2 == {
        "file": "db:books",
        "current_count": row2["current_count"],
        "restored_count": row2["current_count"],
        "delta": 0,
    }

    # Case 3: delete a row -> restore would re-INSERT it -> delta +1.
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_del", name="D", base_currency="", created_at=""))
    ctx3, tok3 = _turn(session="s-preview-3")
    fire_db_write_hook(
        "books",
        [("book_del", {"id": "book_del", "name": "D", "base_currency": "", "created_at": ""})],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.delete().where(t.books.c.id == "book_del"))
    entry3 = cp.end_turn(ctx3, tok3)
    row3 = _db_row(cp.preview(entry3["id"]))
    assert row3 == {
        "file": "db:books",
        "current_count": row3["current_count"],
        "restored_count": row3["current_count"] + 1,
        "delta": 1,
    }


def test_preview_raises_loud_on_table_dropped_since_checkpoint(db_isolated):
    """Minor fold-in (Plan-2 final review, item 3): `_db_preview_rows` used to
    silently `continue` past a write_log table_name no longer registered on
    metadata (dropped/renamed since the checkpoint was taken) — under-reporting the
    panel instead of surfacing that a restore of this checkpoint would fail the
    same way `_replay_write_log` already does (a bare KeyError there, since it
    indexes `metadata.tables[table_name]` directly). Preview must now fail loud
    too, with a clear message naming the table."""
    import pytest

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook

    ctx, token = _turn()
    fire_db_write_hook("ghost_table_xyz", [("row1", None)])
    entry = cp.end_turn(ctx, token)
    assert entry is not None and entry.get("db_tables") == ["ghost_table_xyz"]

    with pytest.raises(RuntimeError, match="ghost_table_xyz"):
        cp.preview(entry["id"])


# ── Plan-2 final review (I2): checkpoint GC pinning ───────────────────────────
#
# Two reproduced silent-false-success /rewind failure modes: (a) restoring the
# OLDEST of MAX_CHECKPOINTS active checkpoints used to self-destruct — capturing
# the pre_restore snapshot pruned the restore TARGET out of the index before its
# own write_log rows were ever replayed, and restore reported SUCCESS having
# reverted nothing; (b) a different session's end_turn hitting the cap could GC
# an IN-FLIGHT (begin_turn'd, not yet ended) turn's already-durable write_log
# rows. Both close via a module-level pinned-ids set (`ai.checkpoint._pinned_ids`).


def test_restoring_oldest_checkpoint_at_cap_actually_reverts(db_isolated, monkeypatch):
    """Shape (a), reproduced pre-fix: fill the checkpoint index to MAX_CHECKPOINTS
    with db-touching turns, keeping the very first (oldest) one as the restore
    target, then restore it. Pre-fix, the pre_restore snapshot's own finalise()
    prunes the target out of the index and GCs its write_log rows before the
    replay call ever runs — restore() returns cleanly, but `book_cap` is never
    reverted. Post-fix, the target stays pinned for the restore's duration."""
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    monkeypatch.setattr(cp, "MAX_CHECKPOINTS", 3)

    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(id="book_cap", name="Before", base_currency="", created_at="")
        )

    ctx0, tok0 = _turn(session="s_cap")
    fire_db_write_hook(
        "books",
        [("book_cap", {"id": "book_cap", "name": "Before", "base_currency": "", "created_at": ""})],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.update().where(t.books.c.id == "book_cap").values(name="After"))
    oldest = cp.end_turn(ctx0, tok0)
    assert oldest is not None

    # Exactly MAX_CHECKPOINTS - 1 more active checkpoints: AT the cap, oldest still
    # indexed — a plain over-cap fill would prune it before restore() even starts,
    # which would raise FileNotFoundError instead of reproducing the self-destruct.
    for i in range(cp.MAX_CHECKPOINTS - 1):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.books).values(
                    id=f"book_filler_{i}", name="F", base_currency="", created_at=""
                )
            )
        ctx, tok = _turn(session="s_cap")
        fire_db_write_hook("books", [(f"book_filler_{i}", None)])
        entry = cp.end_turn(ctx, tok)
        assert entry is not None

    assert cp.find_checkpoint(oldest["id"]) is not None, "sanity: still AT cap, not yet pruned"

    cp.restore(oldest["id"], mode="data")

    with eng.get_engine().connect() as c:
        assert (
            c.execute(select(t.books.c.name).where(t.books.c.id == "book_cap")).scalar() == "Before"
        ), "restoring the oldest checkpoint at cap must actually revert its row"


def test_in_flight_turn_write_log_survives_a_concurrent_session_prune_at_cap(
    db_isolated, monkeypatch
):
    """Shape (b), reproduced pre-fix: session A begins a turn and writes a durable
    write_log image but does NOT end it (mid-turn). Session B's own, unrelated turn
    then ends and pushes the index over MAX_CHECKPOINTS=1, so ITS finalise() prunes
    for real. Pre-fix, `_gc_write_log`'s keep-set came ONLY from the index — and
    session A's in-flight turn has no index entry yet (it hasn't called end_turn) —
    so its write_log row was swept away as "unreferenced" along with the genuinely
    stale entry. Post-fix, `begin_turn` pins session A's ctx.id for its whole
    in-flight span, so the row survives and A's own eventual restore still works."""
    from sqlalchemy import func, insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    monkeypatch.setattr(cp, "MAX_CHECKPOINTS", 1)

    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(
                id="book_inflight", name="Before", base_currency="", created_at=""
            )
        )
        c.execute(
            insert(t.books).values(id="book_prior", name="P", base_currency="", created_at="")
        )

    # A prior, already-finalized checkpoint occupies the one slot MAX_CHECKPOINTS=1
    # allows, so the NEXT finalise() below is guaranteed to actually prune.
    ctx_prior, tok_prior = _turn(session="s_prior")
    fire_db_write_hook("books", [("book_prior", None)])
    entry_prior = cp.end_turn(ctx_prior, tok_prior)
    assert entry_prior is not None

    # Session A begins a turn and writes an image — durable immediately — but does
    # NOT end it yet (mid-turn: begin_turn'd, image written, still active).
    ctx_a, tok_a = _turn(session="s_a")
    fire_db_write_hook(
        "books",
        [
            (
                "book_inflight",
                {"id": "book_inflight", "name": "Before", "base_currency": "", "created_at": ""},
            )
        ],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.update().where(t.books.c.id == "book_inflight").values(name="Mid-turn"))

    # Session B's own, unrelated turn ends -> 2 active checkpoints > MAX_CHECKPOINTS=1,
    # so THIS finalise()'s prune actually runs, while ctx_a is still mid-turn.
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_b", name="B", base_currency="", created_at=""))
    ctx_b, tok_b = _turn(session="s_b")
    fire_db_write_hook("books", [("book_b", None)])
    entry_b = cp.end_turn(ctx_b, tok_b)
    assert entry_b is not None
    assert cp.find_checkpoint(entry_prior["id"]) is None, "sanity: prune actually ran"

    with eng.get_engine().connect() as c:
        n = c.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.checkpoint_id == ctx_a.id)
        ).scalar_one()
    assert n == 1, "an in-flight turn's write_log row must survive a concurrent session's GC at cap"

    # Session A finally ends its turn, and its own eventual restore must still work.
    entry_a = cp.end_turn(ctx_a, tok_a)
    assert entry_a is not None
    cp.restore(entry_a["id"], mode="data")
    with eng.get_engine().connect() as c:
        assert (
            c.execute(select(t.books.c.name).where(t.books.c.id == "book_inflight")).scalar()
            == "Before"
        )


# ── Plan 3 task 1: restore hardening (fail-closed pre-capture, staged files) ────


def test_capture_db_pre_restore_fail_closed_aborts_restore_before_any_mutation(
    db_isolated, monkeypatch
):
    """Restore hardening (Plan 3 task 1, Plan-2 rec 5): _capture_db_pre_restore's own
    write_log image write must be FAIL-CLOSED, not best-effort — an image that fails
    to record must abort the WHOLE restore before _replay_write_log (or any file
    write) ever runs, not let restore() go on to mutate live data behind an undo
    snapshot that silently failed to protect one of its rows.

    Injects a REAL failure, not a mocked one: pins restore()'s fresh pre-restore
    checkpoint id to a known value (it is normally minted fresh each call) and
    pre-seeds a write_log row at the exact (checkpoint_id, seq=1) its first
    record_db_before call will use — the same collision technique
    test_write_log_insert_failure_does_not_poison_the_callers_transaction already
    proves forces a genuine write_log primary-key violation."""
    import pytest
    from sqlalchemy import insert, select
    from sqlalchemy.exc import IntegrityError

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(id="book_fc", name="Before", base_currency="", created_at="")
        )
    ctx, token = _turn()
    fire_db_write_hook(
        "books",
        [("book_fc", {"id": "book_fc", "name": "Before", "base_currency": "", "created_at": ""})],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.update().where(t.books.c.id == "book_fc").values(name="After"))
    entry = cp.end_turn(ctx, token)
    assert entry is not None and "books" in entry["db_tables"]

    # Force restore()'s fresh pre-restore checkpoint id to a known value, then
    # pre-seed the exact (checkpoint_id, seq) collision its first record_db_before
    # call will hit.
    monkeypatch.setattr(cp, "new_id", lambda *_a, **_kw: "ckpt_fc_pre_restore")
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.write_log).values(
                checkpoint_id="ckpt_fc_pre_restore",
                seq=1,
                table_name="books",
                pk="collider",
                before_payload=None,
                created_at="",
            )
        )

    with pytest.raises(IntegrityError):
        cp.restore(entry["id"], mode="data")

    # Nothing mutated: the DB replay never ran (the target row still says "After").
    with eng.get_engine().connect() as c:
        assert (
            c.execute(select(t.books.c.name).where(t.books.c.id == "book_fc")).scalar() == "After"
        ), "a failed pre-restore capture must abort BEFORE the target checkpoint is replayed"


def test_replay_failure_after_staging_cleans_temps_and_leaves_dest_untouched(
    db_isolated, monkeypatch
):
    """Review fix round 1 (Plan 3 task 1): the ONE branch the shipped suite never
    exercised with REAL staged content — restore()'s
    `except BaseException: _cleanup_staged_restore_files(staged); raise` wrapping
    `_replay_write_log`. None of the existing restore-hardening tests reach it with
    a genuinely staged temp on disk:
    `test_capture_db_pre_restore_fail_closed_aborts_restore_before_any_mutation`
    fails BEFORE `_stage_restore_files` ever runs (the pre-restore capture itself
    aborts first); `test_staged_file_restore_survives_crash_between_db_commit_and_
    replace` fails AFTER `_replay_write_log` already succeeded, inside the commit
    loop; the pre-existing `test_composite_pk_table_raises_loud_guard_on_replay`
    hits this exact except clause but that turn touched no files, so `staged` is an
    empty list there — cleanup is a no-op regardless of whether it runs.

    Mechanism (least invasive, chosen deliberately): monkeypatch `_replay_write_log`
    itself to unconditionally raise, exercised against a REAL mixed turn (one file
    write via fire_write_hook + one DB row) that has ALREADY been staged for real
    by the time the monkeypatched replay raises. This isolates exactly the except
    clause under test from WHY replay might fail — that question (a composite-PK
    table, a write_log collision) is already covered by other tests; the DB-rollback
    guarantee on a real mid-replay failure is SQLAlchemy's `Engine.begin()`
    contract, not something this test needs to re-prove."""
    import json

    import pytest
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook, fire_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    f = cp.DATA_DIR / "replay_fail_probe.json"
    f.write_text(json.dumps([{"id": "old"}]))
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(id="book_rf", name="Before", base_currency="", created_at="")
        )

    ctx, token = _turn()
    fire_write_hook(f)  # file half — staged for real below
    f.write_text(json.dumps([{"id": "new"}]))
    fire_db_write_hook(
        "books",
        [("book_rf", {"id": "book_rf", "name": "Before", "base_currency": "", "created_at": ""})],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.update().where(t.books.c.id == "book_rf").values(name="After"))
    entry = cp.end_turn(ctx, token)
    assert entry is not None and "books" in entry["db_tables"] and entry["files"], (
        "sanity: this must be a MIXED turn (file + DB) or the branch under test "
        "never gets a real staged file to clean up"
    )

    def _boom(_cp_id, **_kw):
        raise RuntimeError("simulated replay failure after staging")

    monkeypatch.setattr(cp, "_replay_write_log", _boom)

    # (a) the exception propagates.
    with pytest.raises(RuntimeError, match="simulated replay failure"):
        cp.restore(entry["id"], mode="data")

    # (b) no staged temp file remains — cleanup ran.
    tmp = f.parent / f"{f.name}.restore-tmp-{entry['id'][:8]}"
    assert not tmp.exists(), "a staged temp must be cleaned up when replay fails"
    leftover = list(f.parent.glob(f"{f.name}.restore-tmp-*"))
    assert leftover == [], f"no restore-tmp files of any kind must remain: {leftover}"

    # (c) the dest file's bytes are untouched (post-turn content) — commit never ran.
    assert json.loads(f.read_text()) == [{"id": "new"}]

    # (d) the DB row is untouched (post-turn value) — replay never actually ran.
    with eng.get_engine().connect() as c:
        assert (
            c.execute(select(t.books.c.name).where(t.books.c.id == "book_rf")).scalar() == "After"
        )

    f.unlink()


def test_staged_file_restore_survives_crash_between_db_commit_and_replace(db_isolated, monkeypatch):
    """Restore hardening (Plan 3 task 1): file bytes are staged to a temp file
    BEFORE the DB replay commits, so the only window left where a crash can
    produce a 'split world' is the fast rename loop that follows — not the whole
    read+decompress+write sequence. Simulates a crash exactly there
    (durable_replace raises on its first call — necessarily AFTER the DB half has
    already committed, since _replay_write_log always runs before any replace):
    the staged temp must survive, the destination must stay untouched, and — the
    documented recovery — calling restore() again must finish the job cleanly."""
    import json

    import pytest
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook, fire_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    f = cp.DATA_DIR / "staged_probe.json"
    f.write_text(json.dumps([{"id": "old"}]))
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(id="book_stage", name="Before", base_currency="", created_at="")
        )

    ctx, token = _turn()
    fire_write_hook(f)
    f.write_text(json.dumps([{"id": "new"}]))
    fire_db_write_hook(
        "books",
        [
            (
                "book_stage",
                {"id": "book_stage", "name": "Before", "base_currency": "", "created_at": ""},
            )
        ],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.update().where(t.books.c.id == "book_stage").values(name="After"))
    entry = cp.end_turn(ctx, token)
    assert entry is not None

    real_durable_replace = cp.durable_replace

    def _boom_once(_source, _destination):
        raise OSError("simulated crash between DB commit and file replace")

    monkeypatch.setattr(cp, "durable_replace", _boom_once)

    with pytest.raises(OSError, match="simulated crash"):
        cp.restore(entry["id"], mode="data")

    # DB half already reverted — _replay_write_log always runs BEFORE any replace.
    with eng.get_engine().connect() as c:
        assert (
            c.execute(select(t.books.c.name).where(t.books.c.id == "book_stage")).scalar()
            == "Before"
        )

    # File half: dest untouched, staged temp still present with the right bytes.
    assert json.loads(f.read_text()) == [{"id": "new"}], "dest must be untouched by the crash"
    tmp = f.parent / f"{f.name}.restore-tmp-{entry['id'][:8]}"
    assert tmp.exists(), "the staged temp must survive an interrupted replace"
    assert json.loads(tmp.read_bytes()) == [{"id": "old"}]

    # Documented recovery: restoring again (with the real durable_replace back)
    # finishes the interrupted job cleanly.
    monkeypatch.setattr(cp, "durable_replace", real_durable_replace)
    cp.restore(entry["id"], mode="data")
    assert json.loads(f.read_text()) == [{"id": "old"}]
    assert not tmp.exists()
    f.unlink()


# ── Plan 3 task 1: routed fixes ──────────────────────────────────────────────


def test_pinned_ids_refcount_two_pins_one_unpin_still_pinned():
    """Routed fix (Plan 3 task 1): _pinned_ids is a collections.Counter, not a
    set — concurrent holders of the SAME checkpoint id (e.g. two overlapping
    restores of one checkpoint) each hold their own pin, and the id must stay
    pinned until EVERY holder has unpinned, not just the first. Two pins then one
    unpin must still read as pinned; the matching second unpin fully releases it;
    unpinning past zero must not raise or leave a phantom entry."""
    from backend.ai import checkpoint as cp

    cid = "ckpt_refcount_test_xyz"
    assert cid not in cp._pinned_ids  # sanity: a fresh id starts unpinned

    cp._pin_checkpoint(cid)
    cp._pin_checkpoint(cid)
    cp._unpin_checkpoint(cid)
    assert cid in cp._pinned_ids, "two pins then one unpin must still be pinned"

    cp._unpin_checkpoint(cid)
    assert cid not in cp._pinned_ids, "the matching second unpin must fully release the pin"

    # Unpinning past zero (no matching pin left) must not raise or resurrect the id.
    cp._unpin_checkpoint(cid)
    assert cid not in cp._pinned_ids


def test_gc_blobs_defer_logs_blocking_pin_id_at_warning(caplog):
    """Routed fix (Plan 3 task 1): _gc_blobs used to defer SILENTLY whenever a
    pinned checkpoint id has no index entry yet — indistinguishable from routine,
    self-resolving deferral. It now logs the blocking id at WARNING, so a pin that
    never resolves (a leaked pin) is discoverable instead of a slow, silent GC
    stall nobody notices until disk usage does."""
    import logging

    from backend.ai import checkpoint as cp

    cp._pin_checkpoint("ckpt_ghost_pin_xyz")
    try:
        with caplog.at_level(logging.WARNING, logger="backend.ai.checkpoint"):
            cp._gc_blobs([])  # empty index -> the pinned id has no entry -> defer
        assert "ckpt_ghost_pin_xyz" in caplog.text
        assert any(r.levelno == logging.WARNING for r in caplog.records)
    finally:
        cp._unpin_checkpoint("ckpt_ghost_pin_xyz")


def test_restore_refuses_an_accounts_before_image_with_a_legacy_type(db_isolated):
    """A checkpoint taken before the seven-type migration holds `accounts` before-images typed
    `brokerage`, `savings`, … Replaying one puts a row back that `band_of` cannot classify, so
    net worth 500s afterwards and the user has no idea why. The replay refuses instead, names
    the account, and leaves the table exactly as it was — the whole restore, both worlds,
    aborts (the file phase runs only after this commits)."""
    import pytest
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.errors import DataIntegrityError
    from backend.core.io import fire_db_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t

    legacy = {
        "id": "acc_legacy",
        "book_id": "book_default",
        "seq": 900,
        "name": "IBKR",
        "kind": "asset",
        "type": "brokerage",
        "currency": "SGD",
        "archived": 0,
        "opening_txn_id": None,
        "data": {"id": "acc_legacy", "type": "brokerage"},
    }
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(id="book_default", name="B", base_currency="SGD", created_at="")
        )
        c.execute(insert(t.accounts).values(**{**legacy, "type": "investment"}))

    ctx, token = _turn()
    fire_db_write_hook("accounts", [("acc_legacy", legacy)])
    with eng.get_engine().begin() as c:
        c.execute(t.accounts.update().where(t.accounts.c.id == "acc_legacy").values(name="Renamed"))
    entry = cp.end_turn(ctx, token)

    with pytest.raises(DataIntegrityError, match="acc_legacy"):
        cp.restore(entry["id"], mode="data")

    with eng.get_engine().connect() as c:
        row = c.execute(
            select(t.accounts.c.name, t.accounts.c.type).where(t.accounts.c.id == "acc_legacy")
        ).one()
    assert row.name == "Renamed" and row.type == "investment"
