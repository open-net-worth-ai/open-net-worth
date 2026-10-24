"""Revision 202610201200 — `web_search` / `fetch_page` belong to the search connector and
`x_search` is retired (ADR 2026-10-11-tool-defaults-follow-what-a-tool-does).

Pinned: a restrictive stored decision on the generic tools is carried onto every provider's own
tool (never over a row the owner already has there), an `always_allow` is not carried, the three
`core:` rows are deleted, and Automation `always_allowed` lists naming `x_search` name
`web_search` instead.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, select, table, text

from .conftest import patch_all_cutover_sources_missing

PREV = "202610111300_grant_holdings_hold_no_lots"
THIS = "202610201200_search_tools_belong_to_the_search_connector"
PROVIDERS = ("duckduckgo", "tavily", "perplexity", "serper", "google_pse", "firecrawl")

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


def _row(owner: str, tool: str, level: str, stamp: str = "2026-09-01T00:00:00+00:00") -> dict:
    key = f"{owner}:{tool}"
    return {
        "id": key,
        "connector_id": owner,
        "tool": tool,
        "level": level,
        "updated_at": stamp,
    }


def _seed(engine, *rows: dict) -> None:
    with engine.begin() as c:
        for seq, payload in enumerate(rows, start=100):
            c.execute(insert(PERMS).values(seq=seq, id=payload["id"], payload=payload))


def _rows(engine) -> dict[str, dict]:
    with engine.connect() as c:
        return {r.id: r.payload for r in c.execute(select(PERMS.c.id, PERMS.c.payload))}


def test_a_blocked_web_search_is_carried_onto_every_providers_search_tool(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _seed(engine, _row("core", "web_search", "blocked"))
    command.upgrade(cfg, THIS)

    rows = _rows(engine)
    assert "core:web_search" not in rows
    for provider in PROVIDERS:
        carried = rows[f"web_search:{provider}:{provider}_search"]
        assert carried == _row(f"web_search:{provider}", f"{provider}_search", "blocked"), provider
        # fetch was never restricted, so its tools stay undecided
        assert f"web_search:{provider}:{provider}_fetch_page" not in rows


def test_a_needs_approval_fetch_page_is_carried_onto_every_providers_fetch_tool(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _seed(engine, _row("core", "fetch_page", "needs_approval"))
    command.upgrade(cfg, THIS)

    rows = _rows(engine)
    assert "core:fetch_page" not in rows
    for provider in PROVIDERS:
        assert rows[f"web_search:{provider}:{provider}_fetch_page"]["level"] == "needs_approval", (
            provider
        )


def test_a_provider_row_the_owner_already_has_is_left_alone(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _seed(
        engine,
        _row("core", "web_search", "blocked"),
        _row("web_search:tavily", "tavily_search", "always_allow", "2026-09-15T00:00:00+00:00"),
    )
    command.upgrade(cfg, THIS)

    rows = _rows(engine)
    assert rows["web_search:tavily:tavily_search"]["level"] == "always_allow"
    assert rows["web_search:tavily:tavily_search"]["updated_at"] == "2026-09-15T00:00:00+00:00"
    assert rows["web_search:serper:serper_search"]["level"] == "blocked"


def test_an_always_allow_is_not_carried_and_x_search_is_deleted(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _seed(
        engine,
        _row("core", "web_search", "always_allow"),
        _row("core", "x_search", "blocked"),
        _row("core", "create_payees", "always_allow"),
    )
    command.upgrade(cfg, THIS)

    rows = _rows(engine)
    assert not any(k.startswith("web_search:") for k in rows)
    assert "core:web_search" not in rows
    assert "core:x_search" not in rows
    assert rows["core:create_payees"]["level"] == "always_allow"  # unrelated rows untouched


def test_a_chain_with_nothing_stored_is_a_no_op(at_prev):
    from alembic import command

    engine, cfg = at_prev
    before = _rows(engine)
    command.upgrade(cfg, THIS)
    assert _rows(engine) == before


def _seed_automation(engine, row_id: str, payload: dict, seq: int = 1) -> None:
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT OR IGNORE INTO books (id, name, base_currency, created_at) "
                "VALUES ('default', '', 'SGD', '')"
            )
        )
        c.execute(
            text(
                "INSERT INTO automations (book_id, seq, id, payload) "
                "VALUES ('default', :seq, :id, :payload)"
            ),
            {"seq": seq, "id": row_id, "payload": json.dumps(payload)},
        )


def _automation(engine, row_id: str) -> dict:
    with engine.connect() as c:
        raw = c.execute(text("SELECT payload FROM automations WHERE id = :id"), {"id": row_id})
        value = raw.scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def test_an_automation_naming_x_search_names_web_search_instead(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _seed_automation(
        engine,
        "auto_1",
        {
            "id": "auto_1",
            "always_allowed": [
                {"src": "Web", "desc": "search", "tools": ["x_search", "get_net_worth"]},
                {"src": "Web", "desc": "both", "tools": ["web_search", "x_search"]},
                {"src": "Mail", "desc": "send", "tools": ["email_send_message"]},
            ],
        },
    )
    _seed_automation(engine, "auto_2", {"id": "auto_2", "always_allowed": []}, seq=2)
    command.upgrade(cfg, THIS)

    groups = _automation(engine, "auto_1")["always_allowed"]
    assert groups[0]["tools"] == ["web_search", "get_net_worth"]
    assert groups[1]["tools"] == ["web_search"]  # de-duplicated
    assert groups[2]["tools"] == ["email_send_message"]
    assert _automation(engine, "auto_2")["always_allowed"] == []


def test_downgrade_is_a_safe_no_op(at_prev):
    from alembic import command

    engine, cfg = at_prev
    _seed(engine, _row("core", "web_search", "blocked"))
    command.upgrade(cfg, THIS)
    after = _rows(engine)
    command.downgrade(cfg, PREV)
    assert _rows(engine) == after
