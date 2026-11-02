import os

import pytest
from sqlalchemy import create_engine, insert, select, text


def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/onw.db")
    # BACKUP_DIR is now patched by the autouse _isolated_backup_dir fixture (tests/db/conftest.py).
    from backend.db import engine as eng

    eng.dispose_engine()
    return tmp_path / "onw.db"


def _spawn_lock_holder(lock_path):
    """A REAL second process that flocks ``lock_path`` — mirrors
    tests/test_instance_lock.py's convention: flock is per-open-file-description, so a
    same-process second handle would also conflict, but a real subprocess is what the
    guard actually has to detect in production, and it is what every existing
    instance-lock test uses to prove it. Caller must ``.kill()`` + ``.wait()`` it."""
    import subprocess
    import sys

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from backend.core.file_lock import lock_exclusive;import json,time;"
            f"h=open({str(lock_path)!r},'a+');"
            f"lock_exclusive(h);"
            f"h.seek(0);h.truncate();h.write(json.dumps({{'pid':1,'port':9999}}));h.flush();"
            f"print('locked',flush=True);time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "locked"
    return holder


def _delegating_restore_spy(_migrate):
    """A `_restore` spy that still performs the REAL restore (security-review finding
    4a) — a spy that just swallows the call would let the inode/bytes/sidecar
    assertions pass EVEN IF a regression let `ensure_migrated` wrongly reach
    `_restore`, since nothing real would have happened either way. Delegating makes
    those assertions load-bearing: if the guard being tested ever regresses, the
    REAL restore fires and the file is actually touched, so the value assertions
    (checked BEFORE `restore_calls == []`) fail with a meaningful diff."""
    real_restore = _migrate._restore
    calls: list[tuple] = []

    def spy(*a, **k):
        calls.append((a, k))
        return real_restore(*a, **k)

    return spy, calls


def _rebuild_at_an_intermediate_revision(url: str) -> str:
    """Put the database at a REAL, KNOWN, SINGLE-HEAD revision behind head -- never "base".
    Second security review, HIGH 1: downgrading all the way to "base" clears
    `alembic_version` to `current=None`, and combined with a same-test `sentinel`
    table that makes the database read as POPULATED-BUT-UNSTAMPED -- `_UNSTAMPED`
    now refuses it before `_sqlite_backup` ever runs, so `run_upgrade` and `_restore`
    never fire at all. The reviewer proved this made the inode/sentinel assertions in
    every `_restore`-exercising test pass EVEN WITH THE OLD, DESTRUCTIVE `_restore`
    reinstated, because the test never reached it.

    Once the chain grew a multi-parent merge (e.g.
    202609071500_merge_external_ids_and_seven_account_types), a fixed-index pick off
    `ScriptDirectory.walk_revisions()` can land on a revision that exists on only ONE
    branch of that fork -- downgrading there leaves the sibling branch's tip stamped
    too (real Alembic semantics: that genuinely is the pre-merge state of the world),
    so `alembic_version` ends up with two rows/heads instead of one. Find a true
    common ancestor of every branch instead, by growing each branch's ancestor set one
    step at a time until they intersect -- safe regardless of how many merges the
    chain accumulates."""
    from alembic import command
    from alembic.script import ScriptDirectory

    from backend.db.alembic_config import make_config

    cfg = make_config(url)
    script = ScriptDirectory.from_config(cfg)

    def parents(rev_id: str) -> tuple[str, ...]:
        down = script.get_revision(rev_id).down_revision
        if down is None:
            return ()
        return down if isinstance(down, tuple) else (down,)

    head = script.get_heads()[0]
    branch_tips = list(parents(head)) or [head]
    frontiers = [{tip} for tip in branch_tips]
    ancestors = [set(f) for f in frontiers]
    target = None
    for _ in range(30):
        common = set.intersection(*ancestors)
        if common:
            target = sorted(common)[0]
            break
        for i in range(len(frontiers)):
            nxt: set[str] = set()
            for r in frontiers[i]:
                nxt.update(parents(r))
            frontiers[i] = nxt
            ancestors[i] |= nxt
        if all(not f for f in frontiers):
            break
    assert target, "no common single-head ancestor found in the migration chain"
    # Head's own downgrade() raises (the connectors table is upgrade-in-place only), so the
    # file is rebuilt at `target` instead of being rewound to it.
    from backend.db import engine as eng

    eng.dispose_engine()
    path = url.removeprefix("sqlite:///")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    command.upgrade(cfg, target)
    return target


def test_fresh_db_migrates_without_backup(tmp_path, monkeypatch):
    _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    assert (
        not list((tmp_path / "backups" / "db").glob("*.db"))
        if (tmp_path / "backups" / "db").exists()
        else True
    )
    e = create_engine(f"sqlite:///{tmp_path}/onw.db")
    assert e.connect().execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    e.dispose()


def test_pending_migration_takes_verified_backup_first(tmp_path, monkeypatch):
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()  # to head once
    # Rewound by REBUILDING at an intermediate revision, not by downgrading: head's chain
    # holds revisions that refuse a downgrade on purpose (the connectors table), and a
    # downgrade to base would also leave a populated-but-unstamped file.
    _rebuild_at_an_intermediate_revision(f"sqlite:///{db}")
    ensure_migrated()
    backups = list((tmp_path / "backups" / "db").glob("pre-migrate-*.db"))
    assert len(backups) == 1
    e = create_engine(f"sqlite:///{db}")
    assert e.connect().execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    e.dispose()


def test_failing_upgrade_restores_backup_and_raises(tmp_path, monkeypatch):
    db = _boot(tmp_path, monkeypatch)
    from backend.core.errors import MigrationError
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    # Second security review, HIGH 1: rewind to a real INTERMEDIATE revision, not
    # "base" -- see `_rebuild_at_an_intermediate_revision`'s docstring. THEN create
    # the sentinel table so it is present when the pre-migrate backup is taken (and
    # thus restorable from it).
    _rebuild_at_an_intermediate_revision(f"sqlite:///{db}")
    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("CREATE TABLE sentinel (a INTEGER)"))
    e.dispose()

    def exploding_upgrade(cfg):
        e2 = create_engine(f"sqlite:///{db}")
        with e2.begin() as c:
            c.execute(text("DROP TABLE sentinel"))  # damage the db mid-"migration"
        e2.dispose()
        raise RuntimeError("simulated migration failure")

    with pytest.raises(MigrationError):
        ensure_migrated(upgrade=exploding_upgrade)
    e = create_engine(f"sqlite:///{db}")
    names = [
        r[0] for r in e.connect().execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    ]
    e.dispose()
    assert "sentinel" in names, "backup was not restored"


def test_restore_after_failed_upgrade_preserves_the_inode_for_another_open_reader(
    tmp_path, monkeypatch
):
    """Security-review finding 1b: `_restore` used to copy the backup to a temp file,
    unlink any `-wal`/`-shm` sidecars, and `os.replace` the temp file over the live
    db — swapping the INODE out from under any other process that still has the file
    open, exactly the shape of damage the 2026-09-06 incident's restore did to a live
    server. It must instead restore IN PLACE through SQLite's own online backup API,
    so a reader that already has the file open keeps working and sees the restored
    content on its next read, with no sidecar unlinked out from under it."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    # Second security review, HIGH 1: rewind to a real INTERMEDIATE revision, not
    # "base" -- see `_rebuild_at_an_intermediate_revision`'s docstring.
    _rebuild_at_an_intermediate_revision(f"sqlite:///{db}")
    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("CREATE TABLE sentinel (a INTEGER)"))
        c.execute(text("INSERT INTO sentinel VALUES (1)"))
    e.dispose()

    import sqlite3

    # Another process/live server holding the file open, in WAL mode, for the whole
    # test -- the exact posture `_restore` must not disturb.
    reader = sqlite3.connect(db)
    try:
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("PRAGMA wal_autocheckpoint=0")
        assert list(reader.execute("SELECT * FROM sentinel")) == [(1,)]

        wal = db.with_name(db.name + "-wal")
        shm = db.with_name(db.name + "-shm")
        assert wal.exists() and shm.exists(), "setup sanity: WAL sidecars must exist"

        before_ino = db.stat().st_ino

        def exploding_upgrade(cfg):
            e2 = create_engine(f"sqlite:///{db}")
            with e2.begin() as c:
                c.execute(text("DROP TABLE sentinel"))  # damage the db mid-"migration"
            e2.dispose()
            raise RuntimeError("simulated migration failure")

        from backend.core.errors import MigrationError

        with pytest.raises(MigrationError):
            ensure_migrated(upgrade=exploding_upgrade)

        assert db.stat().st_ino == before_ino, "restore must write INTO the file, not replace it"
        assert wal.exists(), "a WAL sidecar must not be unlinked by the restore"
        assert shm.exists(), "a SHM sidecar must not be unlinked by the restore"
        # The reader's connection -- open the WHOLE time -- must still work and see the
        # RESTORED content (the sentinel row the backup captured) on its next read.
        assert list(reader.execute("SELECT * FROM sentinel")) == [(1,)]
    finally:
        reader.close()
        # LOW 11: this test's own migration attempt fails and restores onto an
        # intermediate (not head) schema, so the process-wide engine singleton -- if
        # left bound -- would point the autouse Tier-2 cleanup fixture at a db missing
        # tables it expects, same class of warning as ~:471 already guards against.
        from backend.db import engine as eng

        eng.dispose_engine()


def test_restore_falls_back_to_copy_replace_when_destination_header_is_corrupted(
    tmp_path, monkeypatch
):
    """Security-review finding MEDIUM 2: the in-place `sqlite3.Connection.backup()`
    restore cannot recover a destination whose own HEADER the failed upgrade
    corrupted -- `src.backup(dst)` itself raises `sqlite3.DatabaseError: file is not
    a database` before it can copy anything in. `_restore` falls back to the OLD
    copy-to-temp -> unlink sidecars -> `os.replace` path ONLY on that specific
    failure, so the restore still succeeds and the database is readable
    afterwards."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    _rebuild_at_an_intermediate_revision(f"sqlite:///{db}")
    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("CREATE TABLE sentinel (a INTEGER)"))
        c.execute(text("INSERT INTO sentinel VALUES (1)"))
    e.dispose()

    def exploding_upgrade(cfg):
        # Corrupt the DESTINATION's own header mid-"migration" -- a fresh
        # sqlite3.connect().execute(...) against this file now raises DatabaseError
        # until the file is fully replaced (in-place backup() cannot recover it).
        with open(db, "r+b") as f:
            f.seek(0)
            f.write(b"\x00" * 4096)
        raise RuntimeError("simulated migration failure that corrupted the header")

    from backend.core.errors import MigrationError

    try:
        with pytest.raises(MigrationError):
            ensure_migrated(upgrade=exploding_upgrade)

        e = create_engine(f"sqlite:///{db}")
        assert e.connect().execute(text("SELECT * FROM sentinel")).fetchall() == [(1,)]
        e.dispose()
    finally:
        from backend.db import engine as eng

        eng.dispose_engine()


def test_database_ahead_of_this_codes_alembic_scripts_is_refused_untouched(tmp_path, monkeypatch):
    """Regression test for the 2026-09-06 incident: a stale checkout's launchd
    service found the owner's ``alembic_version`` stamped by NEWER code (a
    revision this checkout's ScriptDirectory has never heard of), and
    ``_pending()`` — which only compares ``current != head`` — treated that as
    ordinary pending work. It took a pre-migrate backup, ``command.upgrade``
    raised ``CommandError: Can't locate revision identified by
    '202609061000_owned_assets'``, and the ``except`` branch "restored" the
    backup, unlinking the live server's real, still-open ``-wal``/``-shm``
    sidecars and swapping its database file out from under it — even though
    nothing had actually been migrated. ``ensure_migrated`` must now detect an
    unrecognized current revision and raise BEFORE any backup, dispose_engine,
    or upgrade attempt — the file must not move, and a WAL sidecar belonging to
    a connection that is still open must survive untouched.

    A raw sqlite3 connection is held open for the probe below to stand in for
    that live server: only with a connection still open does SQLite leave real
    ``-wal``/``-shm`` files in place instead of auto-checkpointing (and
    deleting) them the instant this test's OWN probe connections close —
    exactly the multi-process reality of the incident, where the stale
    checkout's short-lived migration probe is a different process from the
    live server holding the file open."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()  # real migration once, to get a valid head schema on disk
    from backend.db import engine as eng

    eng.dispose_engine()

    import sqlite3

    live_conn = sqlite3.connect(db)
    try:
        live_conn.execute("PRAGMA journal_mode=WAL")
        live_conn.execute("PRAGMA wal_autocheckpoint=0")
        live_conn.execute(
            "UPDATE alembic_version SET version_num = ?", ("209912310000_from_the_future",)
        )
        live_conn.commit()

        wal = db.with_name(db.name + "-wal")
        shm = db.with_name(db.name + "-shm")
        assert wal.exists() and shm.exists(), "setup sanity: WAL sidecars must exist"

        before_ino = db.stat().st_ino
        before_bytes = db.read_bytes()

        from backend.core.errors import MigrationError
        from backend.db import migrate as _migrate

        spy, restore_calls = _delegating_restore_spy(_migrate)
        monkeypatch.setattr(_migrate, "_restore", spy)

        with pytest.raises(MigrationError) as exc_info:
            ensure_migrated()

        message = str(exc_info.value)
        assert "209912310000_from_the_future" in message
        # Not just the revision id: the OLD (pre-fix) failure mode was alembic's own
        # `CommandError: Can't locate revision identified by '...'`, which also names
        # the id -- asserting a phrase unique to THIS raise proves it is the new,
        # early refusal and not that message leaking through unmodified.
        assert "do not recognize" in message
        assert not list(_migrate.BACKUP_DIR.glob("pre-migrate-*.db")), (
            "no backup may be taken of a database ahead of this code"
        )
        assert db.stat().st_ino == before_ino
        assert db.read_bytes() == before_bytes
        assert wal.exists(), "the live server's WAL sidecar must not be unlinked"
        assert shm.exists(), "the live server's SHM sidecar must not be unlinked"
        assert restore_calls == [], "_restore must never run for a database ahead of this code"
    finally:
        live_conn.close()


def test_existing_populated_database_with_no_alembic_stamp_is_refused_untouched(
    tmp_path, monkeypatch
):
    """Security-review finding 2: `_unknown_current_revision` used to read a `None`
    current revision as "fresh, safe to migrate" UNCONDITIONALLY — but a database
    that already has real tables and simply never got an alembic_version stamp (hand
    -created, restored from some other tool, whatever) is not the same thing as a
    genuinely empty new install. `ensure_migrated` must refuse it untouched: no
    backup, no upgrade attempt, no restore."""
    db = tmp_path / "onw.db"
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{db}")
    from backend.db import engine as eng

    eng.dispose_engine()

    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("CREATE TABLE some_real_table (a INTEGER)"))
        c.execute(text("INSERT INTO some_real_table VALUES (1)"))
    e.dispose()

    before_bytes = db.read_bytes()

    from backend.core.errors import MigrationError
    from backend.db import migrate as _migrate
    from backend.db.migrate import ensure_migrated

    spy, restore_calls = _delegating_restore_spy(_migrate)
    monkeypatch.setattr(_migrate, "_restore", spy)

    try:
        with pytest.raises(MigrationError) as exc_info:
            ensure_migrated()

        message = str(exc_info.value)
        assert "no alembic_version stamp" in message
        assert not list(_migrate.BACKUP_DIR.glob("pre-migrate-*.db")), (
            "no backup may be taken of a populated, unstamped database"
        )
        assert db.read_bytes() == before_bytes
        assert restore_calls == [], "_restore must never run for a populated, unstamped database"
    finally:
        eng.dispose_engine()


def test_genuinely_fresh_empty_database_file_still_migrates_normally(tmp_path, monkeypatch):
    """The other half of finding 2: a zero-byte or newly created but genuinely EMPTY
    sqlite file (no tables at all, e.g. left behind by a probe connection that never
    completed a migration) must still migrate normally — only a database that already
    has real tables without a stamp is refused."""
    db = _boot(tmp_path, monkeypatch)
    db.touch()  # a real, pre-existing, but completely empty file -- db_existed=True
    assert db.stat().st_size == 0
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    e = create_engine(f"sqlite:///{db}")
    assert e.connect().execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    e.dispose()


def test_empty_string_version_num_is_refused_not_a_bare_assertion_error(tmp_path, monkeypatch):
    """Security-review finding 3: alembic's own `ScriptDirectory.get_revision("")`
    raises a bare `AssertionError`, not a `CommandError` -- the old lookup would let
    that escape `ensure_migrated` entirely uncaught instead of a `MigrationError`.
    Switching to an exact-match set-membership check sidesteps `get_revision`
    altogether for this case."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = ''"))
    e.dispose()

    from backend.core.errors import MigrationError
    from backend.db import migrate as _migrate

    spy, restore_calls = _delegating_restore_spy(_migrate)
    monkeypatch.setattr(_migrate, "_restore", spy)

    with pytest.raises(MigrationError) as exc_info:
        ensure_migrated()

    message = str(exc_info.value)
    assert "do not recognize" in message
    assert not list(_migrate.BACKUP_DIR.glob("pre-migrate-*.db"))
    assert restore_calls == []


def test_truncated_revision_prefix_is_refused_not_treated_as_known(tmp_path, monkeypatch):
    """Security-review finding 3: `ScriptDirectory.get_revision` accepts a unique
    PREFIX of a real revision id and resolves it, so the old `known = get_revision(...)
    is not None` check misread a truncated id as "known" — silently letting a
    corrupted/truncated stamp through as safe to migrate. An exact-match check against
    every real revision id closes this."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    from alembic.script import ScriptDirectory

    from backend.db.alembic_config import make_config

    head = ScriptDirectory.from_config(make_config(f"sqlite:///{db}")).get_current_head()
    prefix = head.split("_")[0]
    assert prefix != head, "sanity: this must be a genuine truncation, not the full id"

    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": prefix})
    e.dispose()

    from backend.core.errors import MigrationError
    from backend.db import migrate as _migrate

    spy, restore_calls = _delegating_restore_spy(_migrate)
    monkeypatch.setattr(_migrate, "_restore", spy)

    with pytest.raises(MigrationError) as exc_info:
        ensure_migrated()

    message = str(exc_info.value)
    assert "do not recognize" in message
    assert not list(_migrate.BACKUP_DIR.glob("pre-migrate-*.db"))
    assert restore_calls == []


def test_ahead_of_code_revision_is_refused_before_the_lock_wait(tmp_path, monkeypatch):
    """Security-review finding MEDIUM 5: order matters. A database that is BOTH ahead
    of this checkout's code AND has its working folder locked by another process must
    report "this checkout is older than the database" IMMEDIATELY — not burn the
    (possibly 30s) lock-wait first and blame "another process" for what is really a
    wrong-checkout problem. `_refuse_if_revision_unsafe` is a cheap read-only probe;
    it must run before `_refuse_if_locked_by_other`'s poll loop ever starts."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    import sqlite3
    import time

    from backend.db import migrate as _migrate

    live_conn = sqlite3.connect(db)
    try:
        live_conn.execute("PRAGMA journal_mode=WAL")
        live_conn.execute(
            "UPDATE alembic_version SET version_num = ?", ("209912310000_from_the_future",)
        )
        live_conn.commit()

        lock_folder = tmp_path / "lockfolder"
        monkeypatch.setattr(_migrate, "DATA_DIR", lock_folder / "data")
        # Entering the poll loop sleeps a whole interval first, so "faster than one interval"
        # proves it never ran; a wide one keeps a loaded CI runner from looking like a wait.
        monkeypatch.setattr(_migrate, "_LIVE_LOCK_POLL_TIMEOUT_S", 30.0)
        monkeypatch.setattr(_migrate, "_LIVE_LOCK_POLL_INTERVAL_S", 10.0)

        from backend.core.instance_lock import LOCK_NAME

        holder = _spawn_lock_holder(lock_folder / LOCK_NAME)
        try:
            from backend.core.errors import MigrationError

            start = time.monotonic()
            with pytest.raises(MigrationError) as exc_info:
                ensure_migrated()
            elapsed = time.monotonic() - start

            message = str(exc_info.value)
            assert "do not recognize" in message
            assert "209912310000_from_the_future" in message
            assert elapsed < _migrate._LIVE_LOCK_POLL_INTERVAL_S, (
                "must refuse immediately -- never enter the lock-wait poll loop at all"
            )
        finally:
            holder.kill()
            holder.wait(timeout=10)
    finally:
        live_conn.close()


def test_locked_by_other_probe_failure_is_a_migration_error_not_a_raw_oserror(
    tmp_path, monkeypatch
):
    """Security-review finding MEDIUM 6: `held_by_other`'s own probe (``mkdir`` +
    ``open``) can raise a raw `OSError` -- e.g. something already sits where the
    working folder should be, or the folder is read-only -- and that must not escape
    `ensure_migrated` as an unhandled crash. Reproduced concretely: a plain FILE
    already occupies the path `DATA_DIR.parent` resolves to, so `folder.mkdir(...)`
    raises `FileExistsError` (an `OSError` subclass)."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    _rebuild_at_an_intermediate_revision(f"sqlite:///{db}")  # genuinely pending

    from backend.db import migrate as _migrate

    blocked_folder = tmp_path / "blocked"
    blocked_folder.write_text("a plain file sits where the working folder should be")
    monkeypatch.setattr(_migrate, "DATA_DIR", blocked_folder / "data")

    from backend.core.errors import MigrationError

    try:
        with pytest.raises(MigrationError) as exc_info:
            ensure_migrated()

        message = str(exc_info.value)
        assert str(blocked_folder) in message
        assert "refusing to migrate" in message
    finally:
        from backend.db import engine as eng

        eng.dispose_engine()


def test_pending_migration_refuses_while_another_process_holds_the_working_folder(
    tmp_path, monkeypatch
):
    """Security-review finding 1a: a database merely BEHIND head (genuinely pending,
    the ordinary case) is NOT safe to migrate if another process — even a same-port
    "sibling" that `acquire()` itself would wave through — still has the working
    folder's instance lock. Backing up and upgrading, then hitting an unrelated
    upgrade failure and restoring, would swap the inode and unlink -wal/-shm out from
    under that other process exactly like the original incident. `ensure_migrated`
    must refuse BEFORE any backup once its bounded wait for the lock to free up (or
    the db to reach head) times out."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()  # to head once
    _rebuild_at_an_intermediate_revision(f"sqlite:///{db}")  # genuinely BEHIND head

    from backend.db import migrate as _migrate

    before_ino = db.stat().st_ino
    before_bytes = db.read_bytes()

    lock_folder = tmp_path / "lockfolder"
    monkeypatch.setattr(_migrate, "DATA_DIR", lock_folder / "data")
    monkeypatch.setattr(_migrate, "_LIVE_LOCK_POLL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(_migrate, "_LIVE_LOCK_POLL_INTERVAL_S", 0.01)

    from backend.core.instance_lock import LOCK_NAME

    holder = _spawn_lock_holder(lock_folder / LOCK_NAME)
    try:
        spy, restore_calls = _delegating_restore_spy(_migrate)
        monkeypatch.setattr(_migrate, "_restore", spy)

        from backend.core.errors import MigrationError

        with pytest.raises(MigrationError) as exc_info:
            ensure_migrated()

        message = str(exc_info.value)
        assert str(lock_folder) in message
        assert "refusing to migrate" in message
        assert not list(_migrate.BACKUP_DIR.glob("pre-migrate-*.db")), (
            "no backup may be taken while another process holds the working folder"
        )
        assert db.stat().st_ino == before_ino
        assert db.read_bytes() == before_bytes
        assert restore_calls == [], "_restore must never run while another process holds the lock"
    finally:
        holder.kill()
        holder.wait(timeout=10)
        # This test deliberately leaves the db downgraded (the whole point is that
        # `ensure_migrated` refused to touch it) -- dispose the process-wide engine
        # singleton so the autouse Tier-2 cleanup fixture doesn't try to query a
        # schema this test intentionally left without its tables.
        from backend.db import engine as eng

        eng.dispose_engine()


def test_pending_migration_proceeds_once_the_lock_holder_finishes_migrating(tmp_path, monkeypatch):
    """The other half of finding 1a: a legitimate sibling worker of the SAME instance,
    racing another worker to boot, must not be permanently refused — it waits for
    `_pending` to clear (the owner worker finished migrating) and then returns
    normally, taking no backup and never calling `run_upgrade` itself."""
    _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()  # to head once -- stays there; only the FAKE `_pending` below
    # claims otherwise, so `_finish_cutovers` (reached once it "clears") has a real,
    # genuinely-at-head schema to query rather than a db this test itself downgraded.

    from backend.db import migrate as _migrate

    lock_folder = tmp_path / "lockfolder"
    monkeypatch.setattr(_migrate, "DATA_DIR", lock_folder / "data")
    monkeypatch.setattr(_migrate, "_LIVE_LOCK_POLL_TIMEOUT_S", 5.0)
    monkeypatch.setattr(_migrate, "_LIVE_LOCK_POLL_INTERVAL_S", 0.01)

    from backend.core.instance_lock import LOCK_NAME

    holder = _spawn_lock_holder(lock_folder / LOCK_NAME)
    try:
        calls = {"n": 0}

        def flaky_then_resolved(url):
            calls["n"] += 1
            # ensure_migrated's own initial check (call 1) and one poll iteration
            # (call 2) see it still pending; by call 3 the "owner" worker is done.
            return calls["n"] < 3

        monkeypatch.setattr(_migrate, "_pending", flaky_then_resolved)

        def never_called(cfg):
            raise AssertionError("run_upgrade must never be called by the waiting sibling")

        ensure_migrated(upgrade=never_called)  # must NOT raise

        assert not list(_migrate.BACKUP_DIR.glob("pre-migrate-*.db")), (
            "the waiting sibling must not take its own backup"
        )
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_pending_migration_proceeds_once_the_lock_holder_releases_mid_wait(tmp_path, monkeypatch):
    """Security-review finding MEDIUM 7: `_await_pending_clears` must re-check
    `held_by_other` every iteration, not just once up front — a holder that exits
    WITHOUT ever finishing its own migration (crash, kill -9, whatever) must not cost
    the waiting process the full poll timeout. The database is still genuinely
    pending when the lock frees up mid-wait; `ensure_migrated` must notice within
    roughly one poll interval and proceed to migrate the database ITSELF."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    _rebuild_at_an_intermediate_revision(f"sqlite:///{db}")  # genuinely BEHIND head

    from backend.db import migrate as _migrate

    lock_folder = tmp_path / "lockfolder"
    monkeypatch.setattr(_migrate, "DATA_DIR", lock_folder / "data")
    monkeypatch.setattr(_migrate, "_LIVE_LOCK_POLL_TIMEOUT_S", 5.0)
    monkeypatch.setattr(_migrate, "_LIVE_LOCK_POLL_INTERVAL_S", 0.05)

    from backend.core.instance_lock import LOCK_NAME

    holder = _spawn_lock_holder(lock_folder / LOCK_NAME)
    import threading
    import time

    # Kills the holder shortly after ensure_migrated (main thread) starts polling --
    # it never finishes its own migration; the lock simply becomes free mid-wait.
    killer = threading.Timer(0.2, holder.kill)
    killer.start()
    try:
        start = time.monotonic()
        ensure_migrated()  # must NOT raise -- proceeds itself once the lock frees
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, "must notice the freed lock well before the full poll timeout elapses"
        e = create_engine(f"sqlite:///{db}")
        assert e.connect().execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
        e.dispose()
    finally:
        killer.cancel()
        holder.wait(timeout=10)


def test_custom_url_existing_db_gets_verified_backup(tmp_path, monkeypatch):
    """Item 1 regression: a sqlite ONW_DATABASE_URL pointing somewhere OTHER
    than the app's default DB_FILE must still get a verified backup before an
    upgrade runs against an already-existing custom database — the pre-fix
    code always backed up/restored the fixed DB_FILE path, ignoring the URL."""
    custom_db = tmp_path / "custom" / "location" / "onw.db"
    url = f"sqlite:///{custom_db}"
    backups_dir = tmp_path / "backups" / "db"
    monkeypatch.setenv("ONW_DATABASE_URL", url)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", backups_dir)
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()  # fresh custom-path db, migrated to head — no backup yet
    _rebuild_at_an_intermediate_revision(url)  # so the next run is pending again
    ensure_migrated()
    backups = list(backups_dir.glob("pre-migrate-*.db"))
    assert len(backups) == 1
    e = create_engine(url)
    assert e.connect().execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    e.dispose()
    eng.dispose_engine()


def test_custom_url_fresh_nested_dir_migrates(tmp_path, monkeypatch):
    """Item 1 regression: before the fix, `_pending()`'s throwaway probe
    connection against a custom URL whose parent directories don't exist yet
    crashed with a raw sqlite3.OperationalError ('unable to open database
    file') — nothing mkdir'd the RESOLVED path's parent inside migrate.py,
    only get_engine()'s (task 2's fix, a different code path)."""
    custom_db = tmp_path / "a" / "b" / "c" / "onw.db"
    assert not custom_db.parent.exists()
    url = f"sqlite:///{custom_db}"
    monkeypatch.setenv("ONW_DATABASE_URL", url)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    e = create_engine(url)
    assert e.connect().execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    e.dispose()
    eng.dispose_engine()


def test_failing_upgrade_restores_the_custom_file(tmp_path, monkeypatch):
    """Item 1 regression: mirrors test_failing_upgrade_restores_backup_and_raises
    but on a custom ONW_DATABASE_URL — the restore must target the CUSTOM file,
    not the fixed DB_FILE (which pre-fix would have been the WRONG file, or not
    existed at all, silently failing to restore anything)."""
    custom_db = tmp_path / "custom" / "onw.db"
    url = f"sqlite:///{custom_db}"
    backups_dir = tmp_path / "backups" / "db"
    monkeypatch.setenv("ONW_DATABASE_URL", url)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", backups_dir)
    from backend.core.errors import MigrationError
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    # Second security review, HIGH 1: rewind to a real INTERMEDIATE revision, not
    # "base" -- see `_rebuild_at_an_intermediate_revision`'s docstring.
    _rebuild_at_an_intermediate_revision(url)
    e = create_engine(url)
    with e.begin() as c:
        c.execute(text("CREATE TABLE sentinel (a INTEGER)"))
    e.dispose()

    def exploding_upgrade(cfg):
        e2 = create_engine(url)
        with e2.begin() as c:
            c.execute(text("DROP TABLE sentinel"))  # damage the db mid-"migration"
        e2.dispose()
        raise RuntimeError("simulated migration failure")

    with pytest.raises(MigrationError):
        ensure_migrated(upgrade=exploding_upgrade)
    e = create_engine(url)
    names = [
        r[0] for r in e.connect().execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    ]
    e.dispose()
    eng.dispose_engine()
    assert "sentinel" in names, "backup was not restored onto the CUSTOM file"


def test_postgres_refuses_without_backup_ack(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_DATABASE_URL", "postgresql+psycopg://u:p@localhost:1/nope")
    monkeypatch.delenv("ONW_MIGRATE_ASSUME_BACKUP", raising=False)
    from backend.core.errors import MigrationError
    from backend.db.migrate import _require_backup_or_refuse

    with pytest.raises(MigrationError):
        _require_backup_or_refuse("postgresql+psycopg://u:p@localhost:1/nope")


def test_finish_cutovers_renames_idempotently(tmp_path, monkeypatch, db_engine):
    import hashlib

    src = tmp_path / "old_store.json"
    src.write_text("[]")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    from backend.db.migrate import _finish_cutovers
    from backend.db.tables import json_cutovers

    with db_engine.begin() as c:
        c.execute(
            insert(json_cutovers).values(
                store="old_store",
                source_path=str(src),
                source_hash=digest,
                migrated_at="t",
                renamed=0,
            )
        )
    _finish_cutovers(db_engine)
    assert not src.exists()
    assert len(list(tmp_path.glob("old_store.json.migrated-*"))) == 1
    _finish_cutovers(db_engine)  # second run: no-op, no second rename
    assert len(list(tmp_path.glob("old_store.json.migrated-*"))) == 1
    with db_engine.connect() as c:
        assert c.execute(select(json_cutovers.c.renamed)).scalar() == 1


def test_finish_cutovers_refuses_when_source_changed_after_absorb(tmp_path, monkeypatch, db_engine):
    import hashlib

    import pytest as _pytest

    src = tmp_path / "old_store.json"
    src.write_text("[]")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    from backend.core.errors import MigrationError
    from backend.db.migrate import _finish_cutovers
    from backend.db.tables import json_cutovers

    with db_engine.begin() as c:
        c.execute(
            insert(json_cutovers).values(
                store="old_store",
                source_path=str(src),
                source_hash=digest,
                migrated_at="t",
                renamed=0,
            )
        )
    src.write_text('[{"id": "written-after-absorb"}]')  # a concurrent writer landed a row
    with _pytest.raises(MigrationError) as exc_info:
        _finish_cutovers(db_engine)
    assert src.exists(), "file must NOT be renamed when its content drifted"
    with db_engine.connect() as c:
        assert c.execute(select(json_cutovers.c.renamed)).scalar() == 0
    # Item 2 regression: this scenario (db_engine's fresh per-test tmp db, no
    # ensure_migrated() call, migrate.BACKUP_DIR left at its shared session
    # default) has NO pre-migrate backup on disk — the classic first-boot
    # drift moment. The recovery text must not strand the operator pointing at
    # a backup that doesn't exist.
    assert "delete the database file" in str(exc_info.value)


def test_finish_cutovers_recovery_hint_points_at_backup_when_one_exists(
    tmp_path, monkeypatch, db_engine
):
    """Item 2 regression: the sibling of the no-backup case above — when a
    pre-migrate backup DOES exist, the recovery hint keeps pointing at it
    (the original guidance) instead of the first-boot 'delete and re-absorb'
    text."""
    import hashlib

    backups_dir = tmp_path / "backups" / "db"
    backups_dir.mkdir(parents=True)
    (backups_dir / "pre-migrate-20260101T000000Z.db").write_bytes(b"")
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", backups_dir)

    src = tmp_path / "old_store.json"
    src.write_text("[]")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    from backend.core.errors import MigrationError
    from backend.db.migrate import _finish_cutovers
    from backend.db.tables import json_cutovers

    with db_engine.begin() as c:
        c.execute(
            insert(json_cutovers).values(
                store="old_store",
                source_path=str(src),
                source_hash=digest,
                migrated_at="t",
                renamed=0,
            )
        )
    src.write_text('[{"id": "written-after-absorb"}]')  # a concurrent writer landed a row
    with pytest.raises(MigrationError) as exc_info:
        _finish_cutovers(db_engine)
    assert "pre-migrate-" in str(exc_info.value)


def test_finish_cutovers_multi_row_partial_failure_keeps_earlier_progress(tmp_path, db_engine):
    import hashlib

    from backend.core.errors import MigrationError
    from backend.db.migrate import _finish_cutovers
    from backend.db.tables import json_cutovers

    a = tmp_path / "a_store.json"
    a.write_text("[]")
    b = tmp_path / "b_store.json"
    b.write_text("[]")
    ha = hashlib.sha256(a.read_bytes()).hexdigest()
    hb = hashlib.sha256(b.read_bytes()).hexdigest()
    with db_engine.begin() as c:
        c.execute(
            insert(json_cutovers).values(
                store="a_store", source_path=str(a), source_hash=ha, migrated_at="t", renamed=0
            )
        )
        c.execute(
            insert(json_cutovers).values(
                store="b_store", source_path=str(b), source_hash=hb, migrated_at="t", renamed=0
            )
        )
    b.write_text('[{"drifted": true}]')  # b changes AFTER absorb; a is clean
    with pytest.raises(MigrationError):
        _finish_cutovers(db_engine)
    assert not a.exists() and len(list(tmp_path.glob("a_store.json.migrated-*"))) == 1
    assert b.exists(), "drifted file must not be renamed"
    with db_engine.connect() as c:
        renamed = dict(c.execute(select(json_cutovers.c.store, json_cutovers.c.renamed)).fetchall())
    assert renamed == {"a_store": 1, "b_store": 0}


def test_finish_cutovers_refuses_missing_hash(tmp_path, db_engine):
    """Owner ruling (no-compat cleanup, plan 2 task 6 spec Addendum): the old
    'legacy rows with no hash rename freely' allowance is a branch for a state
    that cannot exist — every real cutover revision stamps a sha256 hash
    (0002/0004/0005). A row with a missing/empty source_hash is malformed data,
    not a legacy format, and must fail closed instead of renaming."""
    from backend.core.errors import MigrationError
    from backend.db.migrate import _finish_cutovers
    from backend.db.tables import json_cutovers

    src = tmp_path / "old_store.json"
    src.write_text("[]")
    with db_engine.begin() as c:
        c.execute(
            insert(json_cutovers).values(
                store="old_store", source_path=str(src), source_hash="", migrated_at="t", renamed=0
            )
        )
    with pytest.raises(MigrationError) as exc_info:
        _finish_cutovers(db_engine)
    assert "old_store" in str(exc_info.value)
    assert src.exists(), "file must NOT be renamed when the ledger row is malformed"
    assert len(list(tmp_path.glob("old_store.json.migrated-*"))) == 0
    with db_engine.connect() as c:
        assert c.execute(select(json_cutovers.c.renamed)).scalar() == 0


def test_finish_cutovers_treats_a_missing_source_as_already_renamed_by_a_sibling(
    tmp_path, monkeypatch, db_engine, caplog
):
    """Security-review finding LOW 8: a waiting sibling now wakes exactly when the
    migration owner finishes (MEDIUM 7), so two processes can race into
    `_finish_cutovers` for the SAME row. If the source file vanishes BETWEEN our
    `exists()` check and our own `rename()` call, `FileNotFoundError` means "another
    process already renamed it" — a benign race, not a failure. Must log and
    continue (still marking the row `renamed=1`), never raise `MigrationError`."""
    import hashlib
    import logging
    from pathlib import Path

    from backend.db.migrate import _finish_cutovers
    from backend.db.tables import json_cutovers

    src = tmp_path / "old_store.json"
    src.write_text("[]")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    with db_engine.begin() as c:
        c.execute(
            insert(json_cutovers).values(
                store="old_store",
                source_path=str(src),
                source_hash=digest,
                migrated_at="t",
                renamed=0,
            )
        )

    real_rename = Path.rename

    def racing_rename(self, target):
        if self == src:
            raise FileNotFoundError(f"[Errno 2] No such file or directory: '{self}'")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", racing_rename)

    with caplog.at_level(logging.INFO):
        _finish_cutovers(db_engine)  # must NOT raise

    with db_engine.connect() as c:
        assert c.execute(select(json_cutovers.c.renamed)).scalar() == 1, (
            "the row must still be marked renamed -- a sibling did the rename for us"
        )
    assert any("already renamed" in r.message for r in caplog.records)


def test_sqlite_backup_prunes_to_keep_5(tmp_path, monkeypatch):
    """Item 1: after a verified backup, only the newest _BACKUP_KEEP (5)
    pre-migrate-*.db files survive in BACKUP_DIR — the new backup counts as
    one of the 5. Seeds 6 fake backups with staggered mtimes so the survivor
    set is unambiguous, then asserts the EXACT set that remains."""
    import os
    import time

    db = _boot(tmp_path, monkeypatch)
    from backend.db import migrate as _migrate

    _migrate.ensure_migrated()  # real db at head — safe to back up

    _migrate.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    fakes = []
    for i in range(6):
        f = _migrate.BACKUP_DIR / f"pre-migrate-fake{i}.db"
        f.write_bytes(b"x")
        stamp = now - (6 - i) * 100  # i=0 oldest (600s ago) .. i=5 newest (100s ago)
        os.utime(f, (stamp, stamp))
        fakes.append(f)

    new_backup = _migrate._sqlite_backup(db)

    remaining = set(_migrate.BACKUP_DIR.glob("pre-migrate-*.db"))
    assert len(remaining) == 5
    assert new_backup in remaining
    assert remaining == {fakes[2], fakes[3], fakes[4], fakes[5], new_backup}, (
        "must keep exactly the 5 newest files (the 4 newest fakes + the new backup) "
        "and prune the 2 oldest fakes"
    )


def test_prune_never_deletes_the_just_verified_backup_on_mtime_ties(tmp_path, monkeypatch):
    """Fix round 1, Important finding (reviewer-reproduced): sorting candidates by
    st_mtime ALONE breaks on a tie (coarse-mtime filesystems, or two backups landing
    in the same second) — the tie-break silently falls to glob iteration order, which
    can rank the JUST-VERIFIED backup past the keep-5 boundary and delete it while
    keeping 5 stale ones. The fix takes `keep` as an explicit argument and excludes it
    from the deletion candidates UNCONDITIONALLY (before any sort), so no tie among
    the OTHER files can ever put it at risk. Forcing every surviving file — including
    the new backup — onto one identical mtime removes any chance of the assertion
    passing merely because the real timestamps happened not to collide."""
    import os
    import time

    db = _boot(tmp_path, monkeypatch)
    from backend.db import migrate as _migrate

    _migrate.ensure_migrated()  # real db at head — safe to back up

    _migrate.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        f = _migrate.BACKUP_DIR / f"pre-migrate-stale{i}.db"
        f.write_bytes(b"x")

    new_backup = _migrate._sqlite_backup(db)

    tied = time.time()
    for f in _migrate.BACKUP_DIR.glob("pre-migrate-*.db"):
        os.utime(f, (tied, tied))

    _migrate._prune_old_backups(new_backup)

    remaining = set(_migrate.BACKUP_DIR.glob("pre-migrate-*.db"))
    assert new_backup in remaining, "the just-verified backup must never be pruned"
    assert len(remaining) == 5


def test_sqlite_backup_dest_is_collision_proof_on_same_second_boots(tmp_path, monkeypatch):
    """Fix round 1, inclusion B: _stamp() is second-resolution, so two backups taken
    within the same wall-clock second previously collided on the IDENTICAL dest path
    and the second silently overwrote the first — a same-second boot must never
    overwrite an already-verified backup. _sqlite_backup now probes for an existing
    dest and suffixes -2, -3, ... until it finds a free path."""
    db = _boot(tmp_path, monkeypatch)
    from backend.db import migrate as _migrate

    _migrate.ensure_migrated()
    monkeypatch.setattr(_migrate, "_stamp", lambda: "20260101T000000Z")

    first = _migrate._sqlite_backup(db)
    second = _migrate._sqlite_backup(db)

    assert first != second, "two same-second backups must not collide on one path"
    assert first.exists()
    assert second.exists()


def test_prune_over_cap_tie_deletes_only_non_keep_files():
    """Fix round 2: the fix-round-1 tie test above lands EXACTLY at the keep-5
    boundary (_sqlite_backup's own internal prune call already shrinks 6 seeded
    files to 5 using real, unforced — and therefore unambiguous — mtimes before
    that test ever forces a tie), so it cannot force an actual deletion DECISION
    among tied candidates. The re-reviewer showed this lets a plausible incomplete
    fix slip through: a defensive `if stale == keep: continue` at unlink time,
    without excluding `keep` from the sorted candidate POOL itself. Under a full
    tie, a stable sort's ordering among equal keys falls back to glob() iteration
    order — if `keep` happens to land in the "to prune" slice, the defensive skip
    protects `keep` from deletion but does NOT compensate by deleting one more
    non-keep file in its place, so the pool shrinks to 6 (only 2 of the 3 "to
    prune" slots actually removed), violating the keep-5 contract. This test
    forces a genuine OVER-cap tie (8 files against a cap of 5) so that decision is
    actually exercised, and checks the deletion SET, not just a count.

    Calls `_prune_old_backups(keep)` directly — NOT via `_sqlite_backup` — because
    `_sqlite_backup`'s internal prune call would fire first against the real
    (unforced, distinct) mtimes at creation time and shrink the pool to 5 before
    this test ever gets to force the tie, masking the over-cap case entirely."""
    import os
    import time

    from backend.db import migrate as _migrate

    _migrate.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(7):
        f = _migrate.BACKUP_DIR / f"pre-migrate-stale{i}.db"
        f.write_bytes(b"x")
    keep = _migrate.BACKUP_DIR / "pre-migrate-keep.db"
    keep.write_bytes(b"x")

    tied = time.time()
    for f in _migrate.BACKUP_DIR.glob("pre-migrate-*.db"):
        os.utime(f, (tied, tied))

    before_set = set(_migrate.BACKUP_DIR.glob("pre-migrate-*.db"))
    assert len(before_set) == 8  # 7 stale + keep — a genuine over-cap tie

    _migrate._prune_old_backups(keep)

    remaining_set = set(_migrate.BACKUP_DIR.glob("pre-migrate-*.db"))
    assert keep in remaining_set, "keep must survive an over-cap tie"
    assert len(remaining_set) == 5
    assert keep not in (before_set - remaining_set), "keep must never be among the deleted files"


def test_require_backup_or_refuse_redacts_credentials(monkeypatch):
    """Item 2: the non-sqlite refusal must not leak the password, but must still
    identify the host/db so an operator can act on it."""
    monkeypatch.delenv("ONW_MIGRATE_ASSUME_BACKUP", raising=False)
    from backend.core.errors import MigrationError
    from backend.db.migrate import _require_backup_or_refuse

    with pytest.raises(MigrationError) as exc_info:
        _require_backup_or_refuse("postgresql+psycopg://user:sekret@h/db")
    combined = f"{exc_info.value}|{exc_info.value.context}"
    assert "sekret" not in combined
    assert "h/db" in combined, "host/db must still be identifiable"
    assert "user" in combined, "username is not a secret; only the password is masked"


def test_ensure_migrated_non_sqlite_upgrade_failure_redacts_credentials(monkeypatch):
    """Item 2: the OTHER named redaction site — ensure_migrated's own
    MigrationError on a failed upgrade against a non-sqlite URL. Reached with
    ONW_MIGRATE_ASSUME_BACKUP=1 (so _require_backup_or_refuse doesn't itself
    raise first) and _pending patched True (so no real network connection is
    ever attempted against the fake postgres URL)."""
    monkeypatch.setenv("ONW_DATABASE_URL", "postgresql+psycopg://user:sekret@h/db")
    monkeypatch.setenv("ONW_MIGRATE_ASSUME_BACKUP", "1")
    from backend.core.errors import MigrationError
    from backend.db import engine as eng
    from backend.db import migrate as _migrate

    eng.dispose_engine()
    monkeypatch.setattr(_migrate, "_pending", lambda url: True)

    def exploding_upgrade(cfg):
        raise RuntimeError("simulated migration failure")

    try:
        with pytest.raises(MigrationError) as exc_info:
            _migrate.ensure_migrated(upgrade=exploding_upgrade)
        combined = f"{exc_info.value}|{exc_info.value.context}"
        assert "sekret" not in combined
        assert "h/db" in combined
    finally:
        eng.dispose_engine()


def test_make_config_percent_encoded_url_round_trips():
    """Item 3: a URL containing a literal '%' (e.g. a URL-encoded space in a
    sqlite file path) must survive make_config -> get_main_option unchanged.
    ConfigParser's default interpolation treats a bare '%' as the start of a
    %(name)s reference and raises; pre-fix this raised INSIDE make_config()
    itself, before any migration ever ran."""
    from backend.db.alembic_config import make_config

    url = "sqlite:///t%20emp.db"
    cfg = make_config(url)
    assert cfg.get_main_option("sqlalchemy.url") == url


# ── Pre-migrate backup pruning also sweeps -wal/-shm sidecars ───────────────────────────


def _touch_backup(path, mtime: float, *, with_sidecars: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("db")
    os.utime(path, (mtime, mtime))
    if with_sidecars:
        for suffix in ("-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            sidecar.write_text("sidecar")
            os.utime(sidecar, (mtime, mtime))


def test_prune_old_backups_removes_sidecars_of_a_pruned_backup(tmp_path):
    """A pruned pre-migrate-*.db (beyond _BACKUP_KEEP) must take its own -wal/-shm
    sidecars with it — a WAL-mode source carries that journal mode into the backup's own
    header, so a later open (even this module's own PRAGMA quick_check) can leave one
    behind. A SURVIVING backup's sidecars must be left alone.

    ``keep`` (this run's own fresh backup) always survives PLUS the newest
    ``_BACKUP_KEEP - 1`` entries in ``BACKUP_DIR`` — six more distinctly-timed entries
    (s0 newest .. s5 oldest) means exactly s4/s5 are pruned, s0-s3 survive; sidecars are
    planted on s0 (must survive) and s5 (must be pruned with it)."""
    from backend.db import migrate as _migrate

    assert _migrate._BACKUP_KEEP == 5
    base = 1_700_000_000.0
    keep = _migrate.BACKUP_DIR / "pre-migrate-keep.db"
    _touch_backup(keep, base + 100)
    entries = []
    for i in range(6):
        p = _migrate.BACKUP_DIR / f"pre-migrate-s{i}.db"
        _touch_backup(p, base - (i * 10), with_sidecars=(i in (0, 5)))
        entries.append(p)
    s0, s1, s2, s3, s4, s5 = entries

    _migrate._prune_old_backups(keep)

    assert keep.exists()
    for survivor in (s0, s1, s2, s3):
        assert survivor.exists(), survivor
    assert s0.with_name(s0.name + "-wal").exists()
    assert s0.with_name(s0.name + "-shm").exists()
    for pruned in (s4, s5):
        assert not pruned.exists(), pruned
    assert not s5.with_name(s5.name + "-wal").exists()
    assert not s5.with_name(s5.name + "-shm").exists()


def test_prune_old_backups_sweeps_orphan_sidecars_whose_db_is_already_gone(tmp_path):
    """A -wal/-shm sidecar whose .db was removed by a PAST prune (before this fix existed)
    is an orphan — swept the next time pruning runs, regardless of which specific file was
    pruned this round."""
    from backend.db import migrate as _migrate

    keep = _migrate.BACKUP_DIR / "pre-migrate-keep.db"
    _touch_backup(keep, 1_700_000_100.0)
    orphan_wal = _migrate.BACKUP_DIR / "pre-migrate-longgone.db-wal"
    orphan_shm = _migrate.BACKUP_DIR / "pre-migrate-longgone.db-shm"
    _migrate.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    orphan_wal.write_text("orphan")
    orphan_shm.write_text("orphan")

    _migrate._prune_old_backups(keep)

    assert not orphan_wal.exists()
    assert not orphan_shm.exists()
    assert keep.exists()  # the fresh backup itself is never a candidate


def test_unlink_with_sidecars_is_best_effort_for_a_missing_file(tmp_path):
    from backend.db import migrate as _migrate

    # No file exists at all — every unlink raises FileNotFoundError, all swallowed.
    _migrate._unlink_with_sidecars(tmp_path / "does-not-exist.db")
