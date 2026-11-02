"""``db_isolated`` / ``db_engine`` point the engine singleton at a fresh test database while
daemon threads from earlier tests are still alive — the tool-call telemetry flusher wakes every
2s and builds the singleton itself. One that lands right after the old engine is disposed must
still open the migrated clone: on 2026-11-02 one opened the not-yet-copied path as an empty WAL
database, and its pooled connection kept that empty schema after the copy, so the whole test saw
``no such table: connectors``."""

from sqlalchemy import text

from tests.db.conftest import point_engine_at, sqlite_only


@sqlite_only("the golden-head clone is a file copy; Postgres upgrades in place")
def test_an_engine_built_the_moment_the_old_one_is_disposed_sees_the_migrated_schema(
    tmp_path, monkeypatch, _sqlite_golden_head
):
    from backend.db import engine as eng

    dispose = eng.dispose_engine

    def a_background_thread_wins_the_race():
        dispose()
        # The flusher's INSERT reads the schema (and swallows its own failure); so does this.
        with eng.get_engine().connect() as c:
            c.execute(text("SELECT count(*) FROM sqlite_master")).scalar()

    monkeypatch.setattr(eng, "dispose_engine", a_background_thread_wins_the_race)
    try:
        point_engine_at(f"sqlite:///{tmp_path}/race.db", monkeypatch, _sqlite_golden_head)
        with eng.get_engine().connect() as c:
            c.execute(text("SELECT id FROM connectors LIMIT 1")).all()
    finally:
        dispose()
