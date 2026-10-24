"""ToolPermissionStore on Core (Connectors redesign §4, one-store merge ADR
`2026-08-14-one-permission-store-for-every-tool`) — DB-CRUD round trip, the deterministic
composite key, the per-connector read, the deliberately lossy group stamp, and the two
classification guarantees the ladder depends on (checkpoint-EXCLUDED so a /rewind cannot
un-block a tool; NON_RESTORABLE so a DR restore cannot either).

Absorbed here from the deleted `ToolGrantStore` (revision 0040): this table now answers for
EVERY tool in the app, not just connectors, so `set_level`/`clear_level` cover what
`ToolGrantStore.grant`/`.revoke` used to.
"""

from __future__ import annotations

import pytest

from backend.db.tool_permission_store import LEVELS, ToolPermissionStore, permission_key


@pytest.fixture()
def store(db_engine):
    return ToolPermissionStore(engine=db_engine)


def test_set_then_get_round_trips_the_level(store):
    assert store.get_level("plaid", "plaid_sync_now") is None
    row = store.set_level("plaid", "plaid_sync_now", "always_allow")
    assert row["connector_id"] == "plaid"
    assert row["tool"] == "plaid_sync_now"
    assert row["level"] == "always_allow"
    assert row["updated_at"]
    assert store.get_level("plaid", "plaid_sync_now") == "always_allow"


def test_row_id_is_the_deterministic_connector_tool_key(store):
    store.set_level("web_search:tavily", "tavily_search", "blocked")
    key = permission_key("web_search:tavily", "tavily_search")
    assert key == "web_search:tavily:tavily_search"
    assert store.find_by_id(key)["level"] == "blocked"
    # A tool name is a Python identifier and a connector id never contains "/", so the
    # pair is still recoverable from the key alone.
    assert key.rsplit(":", 1) == ["web_search:tavily", "tavily_search"]


def test_resetting_a_pair_replaces_its_row_rather_than_duplicating(store):
    store.set_level("plaid", "plaid_sync_now", "always_allow")
    store.set_level("plaid", "plaid_sync_now", "blocked")
    rows = [r for r in store.all() if r["tool"] == "plaid_sync_now"]
    assert len(rows) == 1
    assert rows[0]["level"] == "blocked"


def test_for_connector_returns_only_that_connectors_rows(store):
    store.set_level("plaid", "plaid_sync_now", "always_allow")
    store.set_level("plaid", "plaid_get_status", "blocked")
    store.set_level("mcp:ibkr", "place_order", "blocked")
    assert {r["tool"] for r in store.for_connector("plaid")} == {
        "plaid_sync_now",
        "plaid_get_status",
    }
    assert [r["tool"] for r in store.for_connector("mcp:ibkr")] == ["place_order"]
    assert store.for_connector("nope") == []


def test_group_stamp_overwrites_every_per_tool_override(store):
    """The group control is deliberately LOSSY (§4) — it is how a Custom group is
    flattened back to uniform in one action."""
    store.set_level("mcp:ibkr", "get_positions", "always_allow")
    store.set_level("mcp:ibkr", "place_order", "blocked")
    store.set_levels("mcp:ibkr", ["get_positions", "place_order", "cancel_order"], "needs_approval")
    assert {r["tool"]: r["level"] for r in store.for_connector("mcp:ibkr")} == {
        "get_positions": "needs_approval",
        "place_order": "needs_approval",
        "cancel_order": "needs_approval",
    }


def test_group_stamp_of_duplicate_names_collapses_onto_one_row(store):
    store.set_levels("telegram", ["send_message", "send_message"], "always_allow")
    assert len(store.for_connector("telegram")) == 1


def test_group_stamp_of_nothing_is_a_no_op(store):
    assert store.set_levels("telegram", [], "always_allow") == []
    assert store.all() == []


def test_clear_connector_removes_only_that_connector(store):
    store.set_level("plaid", "plaid_sync_now", "always_allow")
    store.set_level("mcp:ibkr", "place_order", "blocked")
    assert store.clear_connector("plaid") == 1
    assert store.for_connector("plaid") == []
    assert store.get_level("mcp:ibkr", "place_order") == "blocked"
    assert store.clear_connector("plaid") == 0  # idempotent


def test_reseed_connector_level_rewrites_only_the_matching_level_as_a_seed(store):
    """`reseed_connector_level` — `revoke_allowed_tools`'s narrow sibling: it must withdraw
    exactly the named level, never a refusal sitting beside it, and what it writes is a seed."""
    store.set_level("mcp:ibkr", "place_order", "always_allow")
    store.set_level("mcp:ibkr", "get_positions", "always_allow")
    store.set_level("mcp:ibkr", "delete_all", "blocked")
    assert store.reseed_connector_level("mcp:ibkr", "always_allow", "needs_approval") == 2
    rows = {r["tool"]: (r["level"], r["source"]) for r in store.for_connector("mcp:ibkr")}
    assert rows == {
        "place_order": ("needs_approval", "seed"),
        "get_positions": ("needs_approval", "seed"),
        "delete_all": ("blocked", "user"),
    }


def test_clear_level_forgets_one_pair_and_reports_whether_a_row_existed(store):
    """`clear_level` — the absorbed `ToolGrantStore.revoke`'s new spelling: a single-pair
    delete that returns to UNSET rather than to a stored `needs_approval`."""
    assert store.clear_level("core", "add_transactions") is False
    store.set_level("core", "add_transactions", "always_allow")
    assert store.clear_level("core", "add_transactions") is True
    assert store.get_level("core", "add_transactions") is None


def test_unknown_level_is_refused_at_the_store(store):
    """Belt over the service's own check — this is the only write path into the table, and
    a level nothing recognises would read back as unenforceable."""
    with pytest.raises(ValueError):
        store.set_level("plaid", "plaid_sync_now", "sometimes")
    with pytest.raises(ValueError):
        store.set_levels("plaid", ["plaid_sync_now"], "custom")
    assert store.all() == []


def test_the_stored_vocabulary_is_exactly_three_levels():
    """There is NO fourth stored level: "Custom" is a derived group label with nowhere in
    this table to live."""
    assert set(LEVELS) == {"always_allow", "needs_approval", "blocked"}
    assert "custom" not in LEVELS


# ── The reserved YOLO row rides the same shape as any other tool ──────────────


def test_the_yolo_row_is_an_ordinary_row_under_the_core_bucket(store):
    """Absorbed from `ToolGrantStore`'s own `*` row: yolo is listed and revoked through the
    same surface as every other decision, not a side channel."""
    store.set_level("core", "*", "always_allow")
    assert store.get_level("core", "*") == "always_allow"
    assert any(r["tool"] == "*" and r["connector_id"] == "core" for r in store.all())
    assert store.clear_level("core", "*") is True
    assert store.get_level("core", "*") is None


# ── Classification: checkpoint-exclusion, non-restorability, non-portability ──


def test_table_is_checkpoint_excluded_so_a_rewind_cannot_unblock_a_tool():
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "tool_permissions" in _EXCLUDED_TABLES
    # The absorbed store's own name must not linger as if it were still a live table.
    assert "tool_grants" not in _EXCLUDED_TABLES
    assert "connector_tool_permissions" not in _EXCLUDED_TABLES


def test_table_is_non_restorable_but_not_secret():
    from backend.core.sensitive import NON_RESTORABLE_TABLES, SECRET_TABLES

    assert "tool_permissions" in NON_RESTORABLE_TABLES
    assert "tool_permissions" not in SECRET_TABLES


def test_table_is_not_portable_and_is_wiped_by_reset():
    """Instance-local trust: never travels in an export bundle, but "reset means reset" —
    and the direction a reset moves this table is back to the cautious default."""
    from backend.db.portable_bridge import _WIPE_FNS, BRIDGED

    assert "tool_permissions.json" not in BRIDGED
    assert "tool_permissions.json" in _WIPE_FNS


def test_reset_wipe_actually_reaches_the_table(db_engine):
    from backend.db.portable_bridge import wipe_all

    store = ToolPermissionStore(engine=db_engine)
    store.set_level("plaid", "plaid_sync_now", "always_allow")
    with db_engine.begin() as conn:
        wipe_all(conn)
    assert store.all() == []


def test_a_turn_that_only_sets_a_permission_produces_no_checkpoint(db_isolated):
    """Proven end to end, not just via set membership (absorbed from
    `test_tool_grant_store.py::test_tool_grants_writes_produce_no_checkpoint`): a turn that
    ONLY writes this table produces NO checkpoint at all, so there is nothing for a later
    /rewind to replay — a revoked permission can never come back this way
    (`restore_checkpoint` is model-callable with `mutates=("*",)`; an included permission
    table would be privilege escalation through an unrelated tool)."""
    from backend.ai import checkpoint as cp

    store = ToolPermissionStore()
    ctx, token = cp.begin_turn("s1", 0, "set a permission")
    try:
        store.set_level("core", "add_transactions", "always_allow")
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is None  # no checkpoint minted — tool_permissions is the only table touched
    assert store.get_level("core", "add_transactions") == "always_allow"


# ── source: seed vs user, and what a sync may touch ────────────────────────────


def test_a_write_is_a_user_decision_unless_it_says_otherwise(store):
    assert store.set_level("core", "add_transactions", "always_allow")["source"] == "user"
    assert store.set_levels("core", ["a", "b"], "blocked")[0]["source"] == "user"


def test_an_unknown_source_is_refused_at_the_store(store):
    with pytest.raises(ValueError):
        store.set_level("core", "add_transactions", "blocked", source="app")


def test_sync_inserts_missing_rows_and_is_idempotent(store):
    seeds = {("core", "get_net_worth"): ("always_allow", "seed")}
    assert store.sync(seeds) == {"added": 1, "updated": 0, "pruned": 0}
    first = store.get_row("core", "get_net_worth")

    assert store.sync(seeds) == {"added": 0, "updated": 0, "pruned": 0}
    assert store.get_row("core", "get_net_worth") == first  # byte-identical: updated_at unmoved


def test_sync_moves_a_seed_row_to_a_changed_rule_but_never_a_user_row(store):
    store.sync({("core", "a"): ("always_allow", "seed"), ("core", "b"): ("always_allow", "seed")})
    store.set_level("core", "b", "always_allow")  # the owner's own choice, same value

    counts = store.sync(
        {("core", "a"): ("needs_approval", "seed"), ("core", "b"): ("needs_approval", "seed")}
    )

    assert counts["updated"] == 1
    assert store.get_level("core", "a") == "needs_approval"
    assert store.get_level("core", "b") == "always_allow"  # a user row never follows the rule


def test_a_row_without_a_source_is_treated_as_the_owners(store):
    store.upsert(
        {"id": "core:x", "connector_id": "core", "tool": "x", "level": "blocked", "updated_at": ""}
    )

    store.sync({("core", "x"): ("always_allow", "seed")})

    assert store.get_level("core", "x") == "blocked"


def test_sync_prunes_only_seed_rows(store):
    store.sync({("core", "gone"): ("always_allow", "seed")})
    store.set_level("core", "kept", "blocked")

    counts = store.sync({}, prune=[("core", "gone"), ("core", "kept")])

    assert counts["pruned"] == 1
    assert store.get_level("core", "gone") is None
    assert store.get_level("core", "kept") == "blocked"  # a decision about a retired tool stays


def test_set_many_overwrites_a_user_row_with_a_seed(store):
    store.set_level("core", "x", "blocked")

    store.set_many({("core", "x"): ("always_allow", "seed")})

    row = store.get_row("core", "x")
    assert (row["level"], row["source"]) == ("always_allow", "seed")
