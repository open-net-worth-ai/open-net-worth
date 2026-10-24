"""Revision 202610201300 — every permission row says whether it is still the seed or the owner's
(ADR 2026-10-11-permissions-are-one-value-per-tool).

Every row that exists before the revision was written by the owner (nothing seeded a row until
now), so each is stamped `source: "user"`; a row that already has a source is left alone. The
rest of the change — inserting the missing seed rows and carrying the retired YOLO switch
forward — needs the live tool registry and is the boot sync's job
(`tests/test_tool_permission_seeding.py`).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, select, table, text

from .conftest import patch_all_cutover_sources_missing

PREV = "202610201200_search_tools_belong_to_the_search_connector"
THIS = "202610201300_tool_permissions_carry_a_source"

PERMS = table(
    "tool_permissions",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
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


def _payload(owner: str, tool: str, level: str, **extra) -> dict:
    return {
        "id": f"{owner}:{tool}",
        "connector_id": owner,
        "tool": tool,
        "level": level,
        "updated_at": "2026-09-01T00:00:00+00:00",
        **extra,
    }


def _rows(engine) -> dict[str, dict]:
    with engine.connect() as c:
        return {r.id: r.payload for r in c.execute(select(PERMS.c.id, PERMS.c.payload))}


def test_every_existing_row_becomes_the_owners_and_nothing_else_changes(at_prev):
    from alembic import command

    engine, cfg = at_prev
    before = [
        _payload("core", "add_transactions", "always_allow"),
        _payload("core", "create_payees", "needs_approval"),  # equal to the seed, still theirs
        _payload("email", "email_send_message", "blocked"),
        _payload("mcp:drive", "delete_file", "always_allow"),
        _payload("core", "*", "always_allow"),  # the retired YOLO row is left for the boot sync
    ]
    with engine.begin() as c:
        for seq, payload in enumerate(before, start=100):
            c.execute(insert(PERMS).values(seq=seq, id=payload["id"], payload=payload))

    command.upgrade(cfg, THIS)

    rows = _rows(engine)
    assert set(rows) == {p["id"] for p in before}
    for payload in before:
        assert rows[payload["id"]] == {**payload, "source": "user"}


def test_a_row_that_already_has_a_source_is_left_alone_and_a_rerun_is_a_no_op(at_prev):
    from alembic import command

    engine, cfg = at_prev
    seeded = _payload("core", "get_net_worth", "always_allow", source="seed")
    with engine.begin() as c:
        c.execute(insert(PERMS).values(seq=100, id=seeded["id"], payload=seeded))

    command.upgrade(cfg, THIS)

    assert _rows(engine)[seeded["id"]] == seeded


def test_an_empty_table_stays_empty(at_prev):
    from alembic import command

    engine, cfg = at_prev
    command.upgrade(cfg, THIS)

    assert _rows(engine) == {}
