"""Revision 202610251700_statement_previews: the persisted review of a statement."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from backend.db.alembic_config import make_config

_PARENT = "202610171500_trade_cash_legs_say_it_in_the_description"
_REVISION = "202610251700_statement_previews"


@pytest.fixture
def parent_db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _PARENT)
    engine = create_engine(url)
    yield engine, url
    engine.dispose()


def test_upgrade_creates_the_table_and_downgrade_removes_it(parent_db):
    engine, url = parent_db
    assert "statement_previews" not in inspect(engine).get_table_names()

    command.upgrade(make_config(url), _REVISION)
    columns = {c["name"] for c in inspect(engine).get_columns("statement_previews")}
    assert columns == {
        "statement_id",
        "fmt",
        "instr_hash",
        "account_arg",
        "resolved_account_id",
        "preview",
        "updated_at",
    }

    command.downgrade(make_config(url), _PARENT)
    assert "statement_previews" not in inspect(engine).get_table_names()
