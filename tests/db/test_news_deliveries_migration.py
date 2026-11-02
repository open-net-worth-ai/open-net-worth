"""Revision 202611021200_news_deliveries — the Intel delivery decider's ledger table.

Additive: upgrade creates the table (empty) and touches no other row; downgrade drops it.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from .conftest import patch_all_cutover_sources_missing

_REVISION = "202611021200_news_deliveries"


@pytest.fixture()
def before_revision(db_url, monkeypatch, tmp_path):
    if db_url.startswith("postgresql"):
        scrub = sa.create_engine(db_url)
        with scrub.begin() as c:
            c.execute(sa.text("DROP SCHEMA public CASCADE"))
            c.execute(sa.text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)

    from alembic import command
    from alembic.script import ScriptDirectory

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    cfg = make_config(db_url)
    command.upgrade(cfg, ScriptDirectory.from_config(cfg).get_revision(_REVISION).down_revision)
    yield eng.get_engine(), cfg
    eng.dispose_engine()


def test_upgrade_creates_the_table_with_its_indexes_and_downgrade_drops_it(before_revision):
    from alembic import command

    engine, cfg = before_revision
    assert "news_deliveries" not in sa.inspect(engine).get_table_names()

    command.upgrade(cfg, _REVISION)
    inspector = sa.inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("news_deliveries")}
    assert columns == {
        "id",
        "seq",
        "status",
        "decision",
        "source",
        "story_id",
        "subject",
        "user_set",
        "created_at",
        "decided_at",
        "told_at",
        "expires_at",
        "payload",
    }
    indexes = {i["name"] for i in inspector.get_indexes("news_deliveries")}
    assert {
        "ix_news_deliveries_seq",
        "ix_news_deliveries_status",
        "ix_news_deliveries_story_id",
    } <= indexes
    with engine.connect() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM news_deliveries")).scalar_one() == 0

    command.downgrade(cfg, "-1")
    assert "news_deliveries" not in sa.inspect(engine).get_table_names()


def test_the_status_check_rejects_a_value_outside_the_machine(before_revision):
    from alembic import command

    engine, cfg = before_revision
    command.upgrade(cfg, _REVISION)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO news_deliveries (id, seq, status, source, created_at, expires_at, "
                "payload) VALUES ('d', 0, 'sent', 'analysis', 'x', 'y', '{}')"
            )
        )
