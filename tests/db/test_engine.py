import threading

from sqlalchemy import text


def test_database_url_defaults_to_sqlite_in_data_dir(monkeypatch):
    monkeypatch.delenv("ONW_DATABASE_URL", raising=False)
    from backend.db import engine as eng

    url = eng.database_url()
    assert url.startswith("sqlite:///")
    assert url.replace("\\", "/").endswith("/onw.db")


def test_database_url_env_override(monkeypatch):
    monkeypatch.setenv("ONW_DATABASE_URL", "postgresql+psycopg://u:p@localhost/x")
    from backend.db import engine as eng

    assert eng.database_url() == "postgresql+psycopg://u:p@localhost/x"


def test_sqlite_pragmas_applied(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    e = eng.get_engine()
    with e.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    eng.dispose_engine()


def test_get_engine_is_a_singleton_across_threads(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    seen = []

    def grab():
        seen.append(eng.get_engine())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len({id(e) for e in seen}) == 1
    eng.dispose_engine()


def test_unit_of_work_commits_and_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    with eng.unit_of_work() as conn:
        conn.execute(text("CREATE TABLE t (a INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))
    try:
        with eng.unit_of_work() as conn:
            conn.execute(text("INSERT INTO t VALUES (2)"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with eng.get_engine().connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM t")).scalar() == 1
    eng.dispose_engine()


def test_on_commit_fires_after_the_transaction_actually_commits(tmp_path, monkeypatch):
    """The self-deadlock this seam exists to prevent (storage plan 4d task 2): a callback
    registered via on_commit() must not run while the transaction is still open — proven
    here by having the callback read the row back through a FRESH, independent connection.
    An uncommitted write is invisible across connections, so if on_commit fired too early
    the callback's own read would see zero rows instead of one."""
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    seen: list[int] = []

    def _read_committed_count() -> None:
        with eng.get_engine().connect() as fresh:
            seen.append(fresh.execute(text("SELECT COUNT(*) FROM t")).scalar())

    with eng.unit_of_work() as conn:
        conn.execute(text("CREATE TABLE t (a INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))
        eng.on_commit(_read_committed_count)
        assert seen == [], "on_commit must not fire before the block even finishes"

    assert seen == [1], "on_commit must fire exactly once, after the commit lands"
    eng.dispose_engine()


def test_on_commit_callback_discarded_on_rollback(tmp_path, monkeypatch):
    """The sibling phantom-event-on-rollback half: a transaction that raises must fire NO
    registered callback at all — never late, never for a row that ends up not existing."""
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    fired: list[bool] = []
    with eng.unit_of_work() as conn:
        conn.execute(text("CREATE TABLE t (a INTEGER)"))
    try:
        with eng.unit_of_work() as conn:
            conn.execute(text("INSERT INTO t VALUES (1)"))
            eng.on_commit(lambda: fired.append(True))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert fired == []
    with eng.get_engine().connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM t")).scalar() == 0
    eng.dispose_engine()


def test_on_commit_callback_failure_is_isolated(tmp_path, monkeypatch):
    """A raising callback must never propagate out of the committing caller (a write that
    just succeeded must never look like it failed) and must never stop OTHER callbacks
    queued behind it from running."""
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    order: list[str] = []
    with eng.unit_of_work() as conn:
        conn.execute(text("CREATE TABLE t (a INTEGER)"))
        eng.on_commit(lambda: order.append("first"))
        eng.on_commit(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        eng.on_commit(lambda: order.append("third"))
    assert order == ["first", "third"]
    eng.dispose_engine()


def test_on_commit_with_no_active_transaction_fires_immediately(tmp_path, monkeypatch):
    """Defensive fallback only — every real caller registers from inside a conn= composed
    under locked_unit_of_work(), but a stray call with nothing open must still run the
    callback (never silently drop it) rather than raise."""
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    fired: list[bool] = []
    eng.on_commit(lambda: fired.append(True))
    assert fired == [True]
    eng.dispose_engine()


def test_env_override_into_missing_directory_works(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/fresh/subdir/t.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    with eng.unit_of_work() as conn:
        conn.execute(text("CREATE TABLE t (a INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))
    with eng.get_engine().connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM t")).scalar() == 1
    eng.dispose_engine()


def test_sqlite_pool_serves_every_worker_thread_without_starving_the_loop(tmp_path, monkeypatch):
    """~50 worker threads (FastAPI's 40 + asyncio's executor) share the SQLite engine. With the
    default 15 connections they exhausted the pool and the event loop's own small reads queued
    behind them for seconds (loop_watch BLOCKED in the pool's checkout, 2026-11-19). WAL lets
    readers run concurrently, so 40 connections can be open at once without waiting."""
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/pool.db")
    from backend.db import engine as eng

    eng.dispose_engine()
    engine = eng.get_engine()
    held = [engine.connect() for _ in range(40)]
    try:
        for conn in held:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        for conn in held:
            conn.close()
        eng.dispose_engine()
