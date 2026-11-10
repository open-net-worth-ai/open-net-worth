"""Revision 202610271200 — every legacy memory fact is stamped ``state: "unsorted"``.

Runs the migration for REAL (``command.upgrade`` on an isolated tmp-path DB) against hand-seeded
PRE-migration payloads. A legacy fact carries ``tier`` and no ``state``/``section``/``topic``;
the revision adds ``state: "unsorted"``, ``section: null``, ``topic: null`` and touches nothing
else. A fact that already has a ``state`` is left exactly as it is.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_PREV = "202610251700_statement_previews"
_REV = "202610271200_memory_facts_get_a_section"

_LEGACY = {
    "id": "mem_legacy",
    "text": "Prefers concise answers",
    "tier": "core",
    "created_at": "2026-01-02T03:04:05+00:00",
    "pinned": True,
}
_LIVE = {
    "id": "mem_live",
    "text": "Saves 500 a month",
    "state": "live",
    "section": "money",
    "topic": "saving",
    "created_at": "2026-02-03T00:00:00+00:00",
}


@pytest.fixture
def _engine(tmp_path):
    db_path = tmp_path / "onw.db"
    engine = create_engine(f"sqlite:///{db_path}")
    command.upgrade(make_config(f"sqlite:///{db_path}"), _PREV)
    yield engine
    engine.dispose()


def _seed(engine, row_id: str, payload, *, seq: int) -> None:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO memory (seq, id, payload) VALUES (:seq, :id, :payload)"),
            {"seq": seq, "id": row_id, "payload": raw},
        )


def _payload(engine, row_id: str) -> dict:
    with engine.begin() as conn:
        raw = conn.execute(
            text("SELECT payload FROM memory WHERE id = :id"), {"id": row_id}
        ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def _upgrade(tmp_path) -> None:
    command.upgrade(make_config(f"sqlite:///{tmp_path / 'onw.db'}"), _REV)


def test_a_legacy_fact_is_stamped_unsorted_and_keeps_everything_else(_engine, tmp_path):
    _seed(_engine, "mem_legacy", _LEGACY, seq=1)

    _upgrade(tmp_path)

    assert _payload(_engine, "mem_legacy") == {
        **_LEGACY,
        "state": "unsorted",
        "section": None,
        "topic": None,
    }


def test_a_fact_that_already_has_a_state_is_untouched(_engine, tmp_path):
    _seed(_engine, "mem_live", _LIVE, seq=1)
    with _engine.begin() as conn:
        before = conn.execute(text("SELECT payload FROM memory WHERE id = 'mem_live'")).scalar_one()

    _upgrade(tmp_path)

    with _engine.begin() as conn:
        after = conn.execute(text("SELECT payload FROM memory WHERE id = 'mem_live'")).scalar_one()
    assert after == before
    assert _payload(_engine, "mem_live") == _LIVE


def test_one_unreadable_payload_does_not_stop_the_others(_engine, tmp_path):
    _seed(_engine, "broken", "not json", seq=1)
    _seed(_engine, "mem_legacy", _LEGACY, seq=2)

    _upgrade(tmp_path)

    assert _payload(_engine, "mem_legacy")["state"] == "unsorted"


def test_downgrade_is_a_no_op_and_a_second_upgrade_changes_nothing(_engine, tmp_path):
    _seed(_engine, "mem_legacy", _LEGACY, seq=1)
    _upgrade(tmp_path)
    once = _payload(_engine, "mem_legacy")

    cfg = make_config(f"sqlite:///{tmp_path / 'onw.db'}")
    command.downgrade(cfg, _PREV)
    assert _payload(_engine, "mem_legacy") == once
    command.upgrade(cfg, _REV)

    assert _payload(_engine, "mem_legacy") == once
