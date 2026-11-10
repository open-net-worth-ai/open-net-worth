"""Revision 202610272100_memory_topic_summaries — one generated Summary line per memory topic
(ADR 2026-10-25 amendment 8). Runs the migration for REAL on an isolated tmp-path DB.
"""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from backend.db.alembic_config import make_config

_PREV = "202610271800_memory_forgotten"
_REV = "202610272100_memory_topic_summaries"
_TABLE = "memory_topic_summaries"


@pytest.fixture
def _db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _PREV)
    engine = create_engine(url)
    yield engine, url
    engine.dispose()


def test_upgrade_creates_the_table_and_downgrade_removes_it(_db):
    engine, url = _db
    assert _TABLE not in inspect(engine).get_table_names()

    command.upgrade(make_config(url), _REV)
    columns = {c["name"] for c in inspect(engine).get_columns(_TABLE)}
    assert columns == {"section", "topic_key", "topic", "summary", "facts_digest", "updated_at"}
    assert inspect(engine).get_pk_constraint(_TABLE)["constrained_columns"] == [
        "section",
        "topic_key",
    ]
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {_TABLE} VALUES ('you', 'profile', 'Profile', 'Who they are', "
                "'abc', '2026-10-27T00:00:00+00:00')"
            )
        )

    command.downgrade(make_config(url), _PREV)
    assert _TABLE not in inspect(engine).get_table_names()
    command.upgrade(make_config(url), _REV)
    assert _TABLE in inspect(engine).get_table_names()
