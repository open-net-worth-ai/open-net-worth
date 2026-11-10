"""Revision ``202610291900_ibkr_flex_display_name`` — the built-in IBKR connector's stored name.

Runs the migration for REAL on an isolated tmp-path DB against hand-seeded rows. The built-in
``ibkr`` row (Flex report import) was stored as "Interactive Brokers" — the name IBKR's own MCP
server now takes from its website — so Lucius's @-mention list showed two identical rows.
"""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_BEFORE = "202610290100_investment_theses"
_AFTER = "202610291900_ibkr_flex_display_name"


@pytest.fixture
def _db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _BEFORE)
    engine = create_engine(url)
    yield url, engine
    engine.dispose()


def _seed(engine, cid: str, kind: str, name: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO connectors (id, kind, name, enabled, config, state, created_at,"
                " updated_at) VALUES (:id, :kind, :name, 0, '{}', '{}', 'now', 'now')"
            ),
            {"id": cid, "kind": kind, "name": name},
        )


def _name(engine, cid: str) -> str:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT name FROM connectors WHERE id = :id"), {"id": cid}
        ).scalar_one()


def test_upgrade_renames_the_old_default_and_downgrade_restores_it(_db):
    url, engine = _db
    _seed(engine, "ibkr", "ibkr", "Interactive Brokers")
    _seed(engine, "mcp:api-ibkr-com", "mcp", "Interactive Brokers")

    command.upgrade(make_config(url), _AFTER)
    assert _name(engine, "ibkr") == "IBKR Flex"
    assert _name(engine, "mcp:api-ibkr-com") == "Interactive Brokers"  # only the built-in

    command.downgrade(make_config(url), _BEFORE)
    assert _name(engine, "ibkr") == "Interactive Brokers"


def test_a_name_the_user_chose_is_kept(_db):
    url, engine = _db
    _seed(engine, "ibkr", "ibkr", "My IBKR reports")
    command.upgrade(make_config(url), _AFTER)
    assert _name(engine, "ibkr") == "My IBKR reports"
