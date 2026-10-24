"""Revision 202610201400 — `run_code`'s hidden instance-wide "always allow" becomes an ordinary
permission value (ADR 2026-10-11-permissions-are-one-value-per-tool).

An owner who had switched `app_config.code_execution_auto_run` on keeps that choice as a stored
`core:run_code` Always allow (theirs, `source: "user"`); the flag is removed either way; a row the
owner already has for `run_code` is left alone; the kill switch (`code_execution_enabled`) and every
other app_config key are untouched.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, select, table, text

from .conftest import patch_all_cutover_sources_missing

PREV = "202610201300_tool_permissions_carry_a_source"
THIS = "202610201400_run_code_always_allow_is_a_permission"

PERMS = table(
    "tool_permissions",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)
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


def _run_code_row(engine) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(PERMS.c.payload).where(PERMS.c.id == "core:run_code")).first()
    return row[0] if row else None


def test_an_owner_who_had_always_allow_on_keeps_it_as_a_stored_value(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _set_app_config(
        engine,
        {"code_execution_enabled": True, "code_execution_auto_run": True, "app_name": "Mine"},
    )

    command.upgrade(cfg, THIS)

    assert _run_code_row(engine) == {
        "id": "core:run_code",
        "connector_id": "core",
        "tool": "run_code",
        "level": "always_allow",
        "source": "user",
        "updated_at": "2026-10-11T00:00:00+00:00",
    }
    # The flag is gone; the kill switch and everything else stay.
    assert _app_config(engine) == {"code_execution_enabled": True, "app_name": "Mine"}


def test_a_flag_that_was_off_is_just_removed_and_leaves_run_code_to_its_seed(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _set_app_config(engine, {"code_execution_enabled": True, "code_execution_auto_run": False})

    command.upgrade(cfg, THIS)

    assert _run_code_row(engine) is None
    assert _app_config(engine) == {"code_execution_enabled": True}


def test_a_value_the_owner_already_set_for_run_code_wins(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _set_app_config(engine, {"code_execution_auto_run": True})
    blocked = {
        "id": "core:run_code",
        "connector_id": "core",
        "tool": "run_code",
        "level": "blocked",
        "source": "user",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }
    with engine.begin() as c:
        c.execute(insert(PERMS).values(seq=1, id="core:run_code", payload=blocked))

    command.upgrade(cfg, THIS)

    assert _run_code_row(engine) == blocked
    assert _app_config(engine) == {}


def test_no_app_config_row_or_no_flag_is_a_no_op(at_prev):
    from alembic import command

    engine, cfg = at_prev
    command.upgrade(cfg, THIS)

    assert _app_config(engine) is None
    assert _run_code_row(engine) is None
