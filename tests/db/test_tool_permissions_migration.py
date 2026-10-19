"""Revision 0040 — one permission store: `connector_tool_permissions` -> `tool_permissions`,
absorbing `tool_grants`, with every level renamed to its UI label.

Three risks, one revision:

  1. **The level rename must be a REWRITE IN PLACE, not a reset.** Revision 0039 shipped, so
     a real instance can hold live `allow`/`ask`/`block` rows (its own MCP `deny` backfill,
     plus anything set through the ladder since). `stored_tool_level` fails CLOSED on an
     unrecognised value, so an unmigrated `block` would silently fall back to the default and
     an unmigrated `allow` would silently stop firing — a permission the owner set,
     discarded. This is the load-bearing assertion in this file.
  2. **`tool_grants` is absorbed, not merely copied.** Only a `scope: "always"` row crosses,
     landing under the tool's real OWNER (`sync_plaid_now` -> `plaid`, an untagged tool and
     the `*` YOLO row -> `core`) — never under the bare tool name, which would silently
     re-collide two providers sharing a name (the exact incident `ai/tools/connectors.py`
     records).
  3. **Absence is never backfilled**, on either side of the merge: an unset connector tool
     stays unset, and the retired `session` grant tier (already failing closed pre-migration)
     does not become a standing permission just because the merge touched its table.

Mirrors `tests/db/test_configs_cutover.py`'s / the retired
`test_connector_permissions_migration.py`'s own house pattern: drive alembic to the revision
immediately BEFORE this one, seed real rows through the OLD schema, upgrade to head, and read
the result back through the NEW store.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, select, table, text

from backend.db.tool_permission_store import ToolPermissionStore

from .conftest import patch_all_cutover_sources_missing


@pytest.fixture()
def upgraded_to_0039(db_url, monkeypatch, tmp_path):
    """The chain up to the revision immediately BEFORE this one, so real
    `connector_tool_permissions` and `tool_grants` rows can be seeded through their OWN
    (pre-merge) schema and the absorb/rewrite observed doing its work."""
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
    command.upgrade(cfg, "202608132057_connector_tool_permissions")
    yield eng.get_engine(), cfg
    eng.dispose_engine()


def _upgrade_to_head(cfg):
    from alembic import command

    command.upgrade(cfg, "head")


def _payload_table(name: str):
    """A typed handle matching the JSON payload column — plain ``sa.column("payload")``
    (no type) makes SQLite's driver refuse a dict parameter outright; ``sa.JSON`` is what
    the real migrations (0039/0040) declare their own throwaway handles with."""
    return table(
        name, sa.column("seq", sa.Integer), sa.column("id", sa.Text), sa.column("payload", sa.JSON)
    )


def _seed_permission_row(engine, table_name: str, connector_id: str, tool: str, level: str) -> None:
    """A raw row through the OLD (pre-0040) `connector_tool_permissions` shape — exactly
    what revision 0039's own store, or the pre-merge ladder, would have written."""
    perms = _payload_table(table_name)
    key = f"{connector_id}:{tool}"
    with engine.begin() as conn:
        seq = len(conn.execute(select(perms.c.seq)).fetchall())
        conn.execute(
            insert(perms).values(
                seq=seq,
                id=key,
                payload={
                    "id": key,
                    "connector_id": connector_id,
                    "tool": tool,
                    "level": level,
                    "updated_at": "2026-08-13T00:00:00+00:00",
                },
            )
        )


def _seed_grant_row(engine, tool: str, scope: str, **extra) -> None:
    """A raw row through the OLD (pre-0040) `tool_grants` shape."""
    grants = _payload_table("tool_grants")
    payload = {"tool": tool, "scope": scope, "granted_at": "2026-08-01T00:00:00+00:00", **extra}
    with engine.begin() as conn:
        seq = len(conn.execute(select(grants.c.seq)).fetchall())
        conn.execute(insert(grants).values(seq=seq, id=tool, payload=payload))


# ── 1. The level rename: rewritten in place, not reset ────────────────────────


def test_every_legacy_level_is_rewritten_to_its_new_name(upgraded_to_0039):
    engine, cfg = upgraded_to_0039
    _seed_permission_row(engine, "connector_tool_permissions", "plaid", "a_tool", "allow")
    _seed_permission_row(engine, "connector_tool_permissions", "plaid", "b_tool", "ask")
    _seed_permission_row(engine, "connector_tool_permissions", "plaid", "c_tool", "block")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("plaid", "a_tool") == "always_allow"
    assert store.get_level("plaid", "b_tool") == "needs_approval"
    assert store.get_level("plaid", "c_tool") == "blocked"


def test_a_real_pre_existing_block_row_is_not_silently_discarded(upgraded_to_0039):
    """THE load-bearing assertion (module docstring, point 1). Revision 0039 already shipped
    and its own MCP-`deny` backfill writes `block` rows on real instances — an unrewritten
    row would fail `stored_tool_level`'s unrecognised-value check and silently fall back to
    the cautious default, which HAPPENS to be safe for a `block` but is exactly the failure
    that would un-silence an `allow` a user actually set (the sibling assertion above)."""
    engine, cfg = upgraded_to_0039
    _seed_permission_row(engine, "connector_tool_permissions", "mcp:ibkr", "place_order", "block")

    _upgrade_to_head(cfg)

    from backend.services import tool_permissions as svc

    assert svc.stored_tool_level("mcp:ibkr", "place_order") == "blocked"


def test_the_row_id_and_every_other_field_survive_the_rename_untouched(upgraded_to_0039):
    engine, cfg = upgraded_to_0039
    _seed_permission_row(engine, "connector_tool_permissions", "plaid", "sync_plaid_now", "allow")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    row = store.find_by_id("plaid:sync_plaid_now")
    assert row["connector_id"] == "plaid"
    assert row["tool"] == "sync_plaid_now"
    assert row["updated_at"] == "2026-08-13T00:00:00+00:00"


# ── 2. tool_grants absorbed: only `scope: "always"`, under the tool's real owner ──


def test_a_tagged_tools_always_grant_lands_under_its_connector_owner(upgraded_to_0039):
    """`sync_plaid_now` carries `@tool(connector="plaid")` — the absorbed row must land
    there, not under the bare tool name, or two providers sharing behaviour could collide."""
    engine, cfg = upgraded_to_0039
    _seed_grant_row(engine, "sync_plaid_now", "always")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("plaid", "sync_plaid_now") == "always_allow"
    assert store.get_level("core", "sync_plaid_now") is None


def test_an_untagged_tools_always_grant_lands_under_core(upgraded_to_0039):
    engine, cfg = upgraded_to_0039
    _seed_grant_row(engine, "add_transactions", "always")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("core", "add_transactions") == "always_allow"


def test_the_yolo_star_row_lands_at_core_star(upgraded_to_0039):
    engine, cfg = upgraded_to_0039
    _seed_grant_row(engine, "*", "always")

    _upgrade_to_head(cfg)

    # The row lands as it always did; the boot sync (`sync_seeds`) later carries the retired
    # switch forward as per-tool Always allow rows (tests/test_tool_permission_seeding.py).
    store = ToolPermissionStore(engine=engine)
    assert store.get_level("core", "*") == "always_allow"


def test_a_session_scope_grant_the_retired_tier_is_not_migrated(upgraded_to_0039):
    """`session` already failed closed pre-migration (`ToolGrantStore._match`) and is not a
    standing grant — the merge must not mint one out of it."""
    engine, cfg = upgraded_to_0039
    _seed_grant_row(engine, "add_transactions", "session", session_id="sess-1")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("core", "add_transactions") is None
    assert store.all() == []


def test_a_once_scope_row_was_never_persisted_so_there_is_nothing_to_migrate(upgraded_to_0039):
    """`once` is never written by `ToolGrantStore.grant` in the first place — seeded here
    directly (bypassing that guard) purely to prove the migration does not treat it as a
    standing grant if it somehow existed."""
    engine, cfg = upgraded_to_0039
    _seed_grant_row(engine, "send_email", "once")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("core", "send_email") is None


def test_an_always_grant_does_not_overwrite_an_existing_permission_row(upgraded_to_0039):
    """A row already present under the tool's key (set through the NEW ladder before the
    merge landed) wins — the absorb only INSERTs pairs with no existing row."""
    engine, cfg = upgraded_to_0039
    _seed_permission_row(engine, "connector_tool_permissions", "plaid", "sync_plaid_now", "ask")
    _seed_grant_row(engine, "sync_plaid_now", "always")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("plaid", "sync_plaid_now") == "needs_approval"


# ── 3. Absent is never backfilled ──────────────────────────────────────────────


def test_a_tool_with_no_row_at_all_stays_unset(upgraded_to_0039):
    engine, cfg = upgraded_to_0039
    _seed_permission_row(engine, "connector_tool_permissions", "plaid", "sync_plaid_now", "allow")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("plaid", "get_plaid_status") is None


def test_no_legacy_rows_at_all_leaves_an_empty_table(upgraded_to_0039):
    engine, cfg = upgraded_to_0039
    _upgrade_to_head(cfg)
    assert ToolPermissionStore(engine=engine).all() == []


# ── 4. tool_grants is dropped afterward ────────────────────────────────────────


def test_tool_grants_table_is_dropped_after_the_absorb(upgraded_to_0039):
    engine, cfg = upgraded_to_0039
    _seed_grant_row(engine, "add_transactions", "always")

    _upgrade_to_head(cfg)

    from sqlalchemy import inspect

    assert "tool_grants" not in inspect(engine).get_table_names()


def test_the_table_itself_is_renamed_from_connector_tool_permissions(upgraded_to_0039):
    engine, cfg = upgraded_to_0039
    _upgrade_to_head(cfg)

    from sqlalchemy import inspect

    names = inspect(engine).get_table_names()
    assert "tool_permissions" in names
    assert "connector_tool_permissions" not in names
