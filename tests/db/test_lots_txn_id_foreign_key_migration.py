"""Revision 202611061200 — ``lots.txn_id`` becomes a real column with a foreign key.

Runs the revision for REAL on a tmp-path DB, synthetic rows only. Pins: a link to any existing
row is copied (a soft-deleted row still EXISTS, so it stays linked and the amend path treats it as
dead); a link to a row that is gone is cleared from the column AND the payload; a lot with no link
stays that way; the foreign key is enforced afterwards; downgrade drops the column, keeps the rows.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

from backend.db.alembic_config import make_config

_BEFORE = "202611051800_actual_budget_sync"
_AFTER = "202611061200_lots_txn_id_foreign_key"


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _BEFORE)
    engine = create_engine(url)
    event.listen(engine, "connect", lambda dbapi, _record: dbapi.execute("PRAGMA foreign_keys=ON"))
    yield url, engine
    engine.dispose()


def _txn(conn, seq: int, txn_id: str, *, deleted: bool = False) -> None:
    row = {"id": txn_id, "date": "2026-01-05", "kind": "investment", "source": "ai"}
    conn.execute(
        text(
            "INSERT INTO transactions (id, book_id, seq, date, kind, source, description, "
            "deleted, payload) VALUES (:i, 'default', :s, '2026-01-05', 'investment', 'ai', "
            "'Share purchase', :d, :p)"
        ),
        {"i": txn_id, "s": seq, "d": 1 if deleted else 0, "p": json.dumps(row)},
    )


def _lot(conn, seq: int, lot_id: str, txn_id) -> None:
    conn.execute(
        text("INSERT INTO lots (book_id, seq, id, payload) VALUES ('default', :s, :i, :p)"),
        {"s": seq, "i": lot_id, "p": json.dumps({"id": lot_id, "txn_id": txn_id, "fees": 1.5})},
    )


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _txn(conn, 0, "t_live")
        _txn(conn, 1, "t_dead", deleted=True)
        _lot(conn, 0, "l_live", "t_live")
        _lot(conn, 1, "l_dead", "t_dead")
        _lot(conn, 2, "l_gone", "t_never_existed")
        _lot(conn, 3, "l_none", None)


def _rows(engine) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, seq, txn_id, payload FROM lots ORDER BY seq"))
        return {r.id: r for r in rows}


def test_links_are_copied_and_a_link_to_a_missing_row_is_cleared(db):
    url, engine = db
    _seed(engine)

    command.upgrade(make_config(url), _AFTER)

    rows = _rows(engine)
    assert {k: r.txn_id for k, r in rows.items()} == {
        "l_live": "t_live",
        "l_dead": "t_dead",
        "l_gone": None,
        "l_none": None,
    }
    assert json.loads(rows["l_gone"].payload)["txn_id"] is None
    assert json.loads(rows["l_live"].payload) == {"id": "l_live", "txn_id": "t_live", "fees": 1.5}
    assert [r.seq for r in rows.values()] == [0, 1, 2, 3], "row order survives the rebuild"


def test_the_foreign_key_is_enforced_after_the_upgrade(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE lots SET txn_id = 't_nope' WHERE id = 'l_none'"))

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM transactions WHERE id = 't_live'"))
    assert _rows(engine)["l_live"].txn_id is None, "ON DELETE SET NULL"


def test_downgrade_drops_the_column_and_keeps_the_rows(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)

    command.downgrade(make_config(url), _BEFORE)

    columns = {c["name"] for c in inspect(engine).get_columns("lots")}
    assert "txn_id" not in columns
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM lots")).scalar() == 4
