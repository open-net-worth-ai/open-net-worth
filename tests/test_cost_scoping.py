"""Lane D tool-scoping tests.

Covers:
- rare_tool_names() is exactly registry - CORE_TOOL_NAMES
- Default interactive offer excludes rare tools, includes find_tools + core tools
- find_tools(groups=...) enables a group within the same ContextVar context
- Meta persistence round-trip (upsert + read back)
- Old metas without extra_tool_groups field parse as empty list
- Unknown group returns an error
- ONW_FULL_TOOL_OFFER=1 restores the full offer
- Headless offer is byte-for-byte unchanged (security boundary)
- _external_tool_schemas output is sorted by name
- Idempotent: re-requesting an already-enabled group yields loaded=[]
- Additive: groups only grow per session
"""

from __future__ import annotations

import json

import pytest

from backend.ai import assistant
from backend.ai.tools import TOOL_SCHEMAS, _skill_scope

# A small explicit tool offer, standing in for what a headless caller now passes.
_SAMPLE_OFFER = {"get_net_worth", "get_transactions", "get_portfolio", "telegram_send_message"}


@pytest.fixture(autouse=True)
def _reset_scope_and_groups():
    """Reset both the skill scope and extra-groups ContextVar between tests."""
    from backend.ai.tools._extra_groups_scope import set_extra_groups

    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    yield
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())


# ---------------------------------------------------------------------------
# 1. Registry integrity
# ---------------------------------------------------------------------------


def test_rare_tool_names_is_registry_minus_core():
    """'Rare' is DERIVED from CORE, never curated in parallel."""
    from backend.ai.tools._groups import CORE_TOOL_NAMES, rare_tool_names

    live = frozenset(t["name"] for t in TOOL_SCHEMAS)
    assert rare_tool_names() == live - CORE_TOOL_NAMES


def test_find_tools_itself_is_not_rare():
    """find_tools must not appear in the rare set — it's always in CORE."""
    from backend.ai.tools._groups import CORE_TOOL_NAMES, rare_tool_names

    assert "find_tools" not in rare_tool_names()
    assert "find_tools" in CORE_TOOL_NAMES


# ---------------------------------------------------------------------------
# 2. Default slim offer
# ---------------------------------------------------------------------------


def test_default_offer_excludes_rare_tools():
    """Rare tools are not in the default interactive offer."""
    from backend.ai.tools._groups import rare_tool_names

    offered = {t["name"] for t in assistant._active_tools()}
    leaked = offered & rare_tool_names()
    assert leaked == set(), f"rare tools leaked into default offer: {sorted(leaked)}"


def test_default_offer_includes_find_tools():
    """find_tools must always be offered so the model can discover groups."""
    offered = {t["name"] for t in assistant._active_tools()}
    assert "find_tools" in offered


def test_default_offer_includes_core_money_tools():
    """Core money-chat tools must be present in the default slim offer."""
    offered = {t["name"] for t in assistant._active_tools()}
    for tool_name in ("add_transactions", "get_portfolio", "list_accounts"):
        assert tool_name in offered, f"core tool '{tool_name}' missing from default offer"


def test_default_offer_is_smaller_than_full():
    """Slim offer must have fewer tools than the full registry."""
    slim_count = len(assistant._active_tools())
    full_count = len(TOOL_SCHEMAS)
    assert slim_count < full_count


# ---------------------------------------------------------------------------
# 3. find_tools(query=...) loads matching tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_tools_query_loads_tools_in_context():
    """After find_tools(query=...), the matched tools appear in _active_tools()."""
    from backend.ai.tools._groups import find_tools

    result_json, _ = await find_tools({"query": "retirement simulation"})
    result = json.loads(result_json)
    assert "error" not in result
    loaded_names = {item["name"] for item in result["loaded"]}
    assert "simulate_retirement" in loaded_names

    offered = {t["name"] for t in assistant._active_tools()}
    for name in loaded_names:
        assert name in offered, f"'{name}' not offered after find_tools loaded it"


@pytest.mark.asyncio
async def test_find_tools_query_additive_does_not_shrink():
    """A second search does not remove tools loaded by the first."""
    from backend.ai.tools._groups import find_tools

    await find_tools({"query": "retirement simulation"})
    # 'tax loss harvest' was dropped (Task 87 — the tax tools are catalog-hidden and
    # deliberately unreachable via find_tools); 'realized gains losses' is an
    # equally-rare, non-hidden tool the same additive-load property can pin against.
    await find_tools({"query": "realized gains losses"})

    offered = {t["name"] for t in assistant._active_tools()}
    assert "simulate_retirement" in offered, "first search's tool disappeared"
    assert "get_realized_pnl" in offered


# ---------------------------------------------------------------------------
# 4. A query that matches nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_tools_unmatched_query_loads_nothing():
    """A miss returns an empty loaded list and leaves the name scope untouched."""
    from backend.ai.tools._extra_groups_scope import get_extra_names
    from backend.ai.tools._groups import find_tools

    result_json, _ = await find_tools({"query": "xyzzy_nonexistent_gibberish_zzz123"})
    result = json.loads(result_json)
    assert "error" not in result
    assert result["loaded"] == []
    assert get_extra_names() == frozenset()


# ---------------------------------------------------------------------------
# 5. Session meta persistence
# ---------------------------------------------------------------------------


def _isolate_sessions(tmp_path, monkeypatch):
    # sessions is DB-backed now (Wave 5E, JSON->DB) and isolated per test by conftest's
    # autouse Tier-2 table clear — no per-store monkeypatch needed any more. Kept as a
    # named no-op so every existing call site (a documentation marker of intent) is
    # unchanged.
    del tmp_path, monkeypatch


def test_meta_persistence_round_trip(tmp_path, monkeypatch):
    """upsert_session_meta stores extra_tool_groups; read back matches."""
    _isolate_sessions(tmp_path, monkeypatch)
    session_id = "test-lane-d-1"

    assistant.upsert_session_meta(
        session_id, title="Test", extra_tool_groups=["agents_admin", "system_admin"]
    )
    index = assistant._read_index()
    entry = next(e for e in index if e["id"] == session_id)
    assert entry["extra_tool_groups"] == ["agents_admin", "system_admin"]


def test_meta_old_entry_parses_without_field(tmp_path, monkeypatch):
    """Old session metas that lack extra_tool_groups parse as empty list (backward compat)."""
    _isolate_sessions(tmp_path, monkeypatch)
    # Write a meta the old way (no extra_tool_groups key).
    assistant.upsert_session_meta("old-session", title="Old")
    index = assistant._read_index()
    entry = next(e for e in index if e["id"] == "old-session")
    # Field absent → treated as [] by callers using `.get("extra_tool_groups") or []`.
    assert entry.get("extra_tool_groups") is None or entry.get("extra_tool_groups") == []
    # run.py reads it via `_entry.get("extra_tool_groups") or []` — simulate that.
    groups = entry.get("extra_tool_groups") or []
    assert groups == []


def test_meta_extra_tool_groups_update(tmp_path, monkeypatch):
    """Updating extra_tool_groups on an existing entry replaces the field."""
    _isolate_sessions(tmp_path, monkeypatch)
    sid = "upd-session"
    assistant.upsert_session_meta(sid, extra_tool_groups=["agents_admin"])
    assistant.upsert_session_meta(sid, extra_tool_groups=["agents_admin", "connectors_admin"])
    index = assistant._read_index()
    entry = next(e for e in index if e["id"] == sid)
    assert set(entry["extra_tool_groups"]) == {"agents_admin", "connectors_admin"}


# ---------------------------------------------------------------------------
# 6. ONW_FULL_TOOL_OFFER=1 escape hatch
# ---------------------------------------------------------------------------


def test_full_tool_offer_env_restores_all(monkeypatch):
    """ONW_FULL_TOOL_OFFER=1 returns the full TOOL_SCHEMAS (no slimming)."""
    monkeypatch.setenv("ONW_FULL_TOOL_OFFER", "1")
    assert assistant._active_tools() == list(TOOL_SCHEMAS)


def test_full_tool_offer_env_off_by_default():
    """Without the env var, the slim offer is returned."""
    from backend.ai.tools._groups import rare_tool_names

    offered = {t["name"] for t in assistant._active_tools()}
    assert offered & rare_tool_names() == set()


# ---------------------------------------------------------------------------
# 7. Headless offer is byte-for-byte unchanged (security boundary)
# ---------------------------------------------------------------------------


def test_headless_offer_unaffected_by_loaded_names():
    """Loaded tool names must NOT widen a headless turn's offer (security boundary)."""
    from backend.ai.tools._extra_groups_scope import set_extra_names
    from backend.ai.tools._groups import rare_tool_names

    set_extra_names(rare_tool_names())
    _skill_scope.set_offer_scope(_SAMPLE_OFFER)

    offered = {t["name"] for t in assistant._active_tools()}
    # Must equal the exact headless allowlist — no extras from loaded names.
    assert offered == _SAMPLE_OFFER


def test_headless_offer_same_regardless_of_loaded_names(monkeypatch):
    """The headless offer is the same with or without extra names loaded."""
    from backend.ai.tools._extra_groups_scope import set_extra_names
    from backend.ai.tools._groups import rare_tool_names

    _skill_scope.set_offer_scope(_SAMPLE_OFFER)
    no_names_offer = {t["name"] for t in assistant._active_tools()}

    set_extra_names(rare_tool_names())
    with_names_offer = {t["name"] for t in assistant._active_tools()}

    assert no_names_offer == with_names_offer


# ---------------------------------------------------------------------------
# 8. _external_tool_schemas output is sorted
# ---------------------------------------------------------------------------


def test_external_tool_schemas_sorted(monkeypatch):
    """_external_tool_schemas() returns schemas sorted by name for deterministic bytes."""
    import backend.core.mcp_external as mcp_ext

    class _FakeDispatcher:
        def schemas(self):
            # Return in reverse order to test that sorting is applied.
            return [
                {"name": "zebra_tool", "description": "z"},
                {"name": "alpha_tool", "description": "a"},
                {"name": "mango_tool", "description": "m"},
            ]

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())
    result = assistant._external_tool_schemas()
    names = [s["name"] for s in result]
    assert names == sorted(names), f"external schemas not sorted: {names}"


def test_external_tool_schemas_empty_when_no_dispatcher(monkeypatch):
    """_external_tool_schemas() returns [] when no dispatcher is configured."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: None)
    assert assistant._external_tool_schemas() == []
