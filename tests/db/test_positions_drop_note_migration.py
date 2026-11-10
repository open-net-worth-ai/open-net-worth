"""Revision 202610292000_positions_drop_note — every position payload loses its ``note`` key.

A position's other fields, and a lot's own note, are untouched.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from .conftest import patch_all_cutover_sources_missing

_REVISION = "202610292000_positions_drop_note"


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


def _payload(engine, table: str, row_id: str) -> dict:
    with engine.connect() as c:
        raw = c.execute(
            sa.text(f"SELECT payload FROM {table} WHERE id = :id"),  # noqa: S608
            {"id": row_id},
        ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def test_upgrade_drops_the_position_note_and_keeps_the_lot_note(before_revision):
    from alembic import command

    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore

    engine, cfg = before_revision
    pos = PositionStore().create(
        {"instrument_id": "inst_x", "account_id": "acc_x", "vesting_state": "vested"}
    )
    with engine.begin() as c:
        c.execute(
            sa.text("UPDATE positions SET payload = :p WHERE id = :id"),
            {"p": json.dumps({**pos, "note": "Speculative"}), "id": pos["id"]},
        )
    lot = LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": "inst_x",
            "account_id": "acc_x",
            "acquired_at": "2026-10-16",
            "original_qty": 8.0,
            "remaining_qty": 8.0,
            "unit_cost": 150.0,
            "cost_currency": "USD",
            "kind": "buy",
            "note": "bought 8 @ 150",
        }
    )

    command.upgrade(cfg, _REVISION)

    after = _payload(engine, "positions", pos["id"])
    assert "note" not in after
    assert {k: after[k] for k in ("instrument_id", "account_id", "vesting_state")} == {
        "instrument_id": "inst_x",
        "account_id": "acc_x",
        "vesting_state": "vested",
    }
    assert _payload(engine, "lots", lot["id"])["note"] == "bought 8 @ 150"
