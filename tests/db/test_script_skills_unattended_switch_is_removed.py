"""Revision 202610201500 — the "Unattended script runs" switch is removed.

Its stored key is dropped from the `app_config` row; the two feature switches
(`code_execution_enabled`, `script_skills_enabled`) and every other key are untouched.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, select, table, text

from .conftest import patch_all_cutover_sources_missing

PREV = "202610201400_run_code_always_allow_is_a_permission"
THIS = "202610201500_script_skills_unattended_switch_is_removed"

STATE = table(
    "runtime_state",
    sa.column("namespace", sa.Text),
    sa.column("payload", sa.JSON),
)


@pytest.fixture()
def at_prev(db_url, monkeypatch, tmp_path):
    if db_url.startswith("postgresql"):
        from sqlalchemy import create_engine

        scrub = create_engine(db_url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    cfg = make_config(db_url)
    command.upgrade(cfg, PREV)
    yield eng.get_engine(), cfg
    eng.dispose_engine()


def _set_app_config(engine, payload: dict) -> None:
    with engine.begin() as c:
        c.execute(STATE.delete().where(STATE.c.namespace == "app_config"))
        c.execute(insert(STATE).values(namespace="app_config", payload=payload))


def _app_config(engine) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(STATE.c.payload).where(STATE.c.namespace == "app_config")).first()
    return row[0] if row else None


@pytest.mark.parametrize("stored", [True, False])
def test_the_stored_key_is_dropped_and_nothing_else_is(at_prev, stored):
    from alembic import command

    engine, cfg = at_prev
    _set_app_config(
        engine,
        {
            "script_skills_enabled": True,
            "code_execution_enabled": True,
            "script_skills_unattended_enabled": stored,
            "app_name": "Mine",
        },
    )

    command.upgrade(cfg, THIS)

    assert _app_config(engine) == {
        "script_skills_enabled": True,
        "code_execution_enabled": True,
        "app_name": "Mine",
    }


def test_no_app_config_row_or_no_key_is_a_no_op(at_prev):
    from alembic import command

    engine, cfg = at_prev
    command.upgrade(cfg, THIS)
    assert _app_config(engine) is None


def test_the_downgrade_is_a_safe_no_op(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _set_app_config(engine, {"script_skills_enabled": True})
    command.upgrade(cfg, THIS)
    command.downgrade(cfg, PREV)
    assert _app_config(engine) == {"script_skills_enabled": True}
