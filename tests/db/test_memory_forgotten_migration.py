"""Revision 202610271800_memory_forgotten — the fingerprint table for forgotten facts, and the
one-time rename of the You topic "About you" to "Profile" (ADR 2026-10-25 amendment 7).

Runs the migration for REAL on an isolated tmp-path DB against hand-seeded pre-migration rows.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from backend.db.alembic_config import make_config

_PREV = "202610271200_memory_facts_get_a_section"
_REV = "202610271800_memory_forgotten"

_ABOUT = {
    "id": "mem_about",
    "content": "Speaks Portuguese",
    "state": "live",
    "section": "you",
    "topic": "About you",
}
_LOWER = {
    "id": "mem_lower",
    "content": "Has two cats",
    "state": "live",
    "section": "you",
    "topic": "about you",
}
_OTHER = {
    "id": "mem_other",
    "content": "Odd filing",
    "state": "live",
    "section": "other",
    "topic": "About you",
}
_PREFS = {
    "id": "mem_prefs",
    "content": "Likes tables",
    "state": "live",
    "section": "you",
    "topic": "Preferences",
}


@pytest.fixture
def _db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _PREV)
    engine = create_engine(url)
    yield engine, url
    engine.dispose()


def _seed(engine, *facts) -> None:
    with engine.begin() as conn:
        for seq, fact in enumerate(facts):
            conn.execute(
                text("INSERT INTO memory (seq, id, payload) VALUES (:s, :i, :p)"),
                {"s": seq, "i": fact["id"], "p": json.dumps(fact)},
            )


def _payload(engine, row_id: str) -> dict:
    with engine.begin() as conn:
        raw = conn.execute(
            text("SELECT payload FROM memory WHERE id = :i"), {"i": row_id}
        ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def test_upgrade_creates_the_table_and_downgrade_removes_it(_db):
    engine, url = _db
    assert "memory_forgotten" not in inspect(engine).get_table_names()

    command.upgrade(make_config(url), _REV)
    columns = {c["name"]: c for c in inspect(engine).get_columns("memory_forgotten")}
    assert set(columns) == {"hash", "forgotten_at"}
    assert inspect(engine).get_pk_constraint("memory_forgotten")["constrained_columns"] == ["hash"]

    command.downgrade(make_config(url), _PREV)
    assert "memory_forgotten" not in inspect(engine).get_table_names()


def test_upgrade_renames_the_you_topic_about_you_to_profile_and_nothing_else(_db):
    engine, url = _db
    _seed(engine, _ABOUT, _LOWER, _OTHER, _PREFS)

    command.upgrade(make_config(url), _REV)

    assert _payload(engine, "mem_about") == {**_ABOUT, "topic": "Profile"}
    assert _payload(engine, "mem_lower") == {**_LOWER, "topic": "Profile"}
    assert _payload(engine, "mem_other") == _OTHER
    assert _payload(engine, "mem_prefs") == _PREFS


def test_the_rename_skips_an_unreadable_row_and_is_idempotent(_db):
    engine, url = _db
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO memory (seq, id, payload) VALUES (99, 'broken', 'not json')")
        )
    _seed(engine, {**_ABOUT, "id": "mem_after"})

    command.upgrade(make_config(url), _REV)
    once = _payload(engine, "mem_after")
    command.downgrade(make_config(url), _PREV)
    command.upgrade(make_config(url), _REV)

    assert once["topic"] == "Profile" and _payload(engine, "mem_after") == once
