"""Bounded inline tool offer for an explicit-offer (agent/scheduled) turn.

Motivated by a measured incident (2026-07-19, live agents.json): the "Transaction &
Trade Logger" agent's 143-tool ``allowed_tools`` resolved to 135 live schemas —
156K+ chars sent inline on EVERY model round trip of every scheduled fire, 4x the
entire interactive CORE offer. This module (``ai/tools/_agent_offer``) bounds a
LARGE explicit offer to a small inline slice + ``find_tools`` + the discovery gate,
while a SMALL offer (an agent's whole point — a short, curated tool list) stays
fully inline, byte-identical to before this module existed.

The load-bearing property under test: **reach never shrinks**. Every tool in a
large agent's ``allowed_tools`` must still be callable within the SAME turn (via
the same auto-load-then-validate lazy path interactive chat's discovery gate uses)
— this changes DELIVERY (inline now vs. discovered lazily), never the turn's
reachable universe (ADR ``2026-07-18-remove-layered-ai-permission-system``:
``allowed_tools`` is configuration, not an enforced ceiling).

Offline + isolated: no LLM, no network. ContextVars are reset between tests.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import assistant
from backend.ai.tools import TOOL_SCHEMAS, _skill_scope
from backend.ai.tools._agent_offer import split_agent_offer
from backend.ai.tools._extra_groups_scope import set_extra_groups, set_extra_names
from backend.ai.tools._groups import CORE_TOOL_NAMES
from backend.ai.tools.registry import registered_tool_names


@pytest.fixture(autouse=True)
def _reset_scopes():
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())
    yield
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())


# A large, REAL offer standing in for an oversized agent (the live Trade-Logger shape) —
# every registered tool except find_tools itself (no live agent lists it explicitly; the
# mechanism adds it implicitly).
def _full_registry_offer() -> frozenset:
    return frozenset(registered_tool_names()) - {"find_tools"}


# ---------------------------------------------------------------------------
# 1. split_agent_offer — pure unit tests
# ---------------------------------------------------------------------------


def test_small_offer_returns_none_inline_and_empty_catalog():
    """At or under the CORE budget: deliver everything, unchanged — no bounding."""
    small = frozenset({"get_net_worth", "telegram_send_message", "get_portfolio"})
    inline, catalog = split_agent_offer(small)
    assert inline is None
    assert catalog == frozenset()


def test_offer_exactly_at_the_limit_is_not_bounded():
    """The boundary case: exactly len(CORE_TOOL_NAMES) tools is still "small"."""
    limit = len(CORE_TOOL_NAMES)
    offer = frozenset(f"synthetic_tool_{i}" for i in range(limit))
    inline, catalog = split_agent_offer(offer)
    assert inline is None
    assert catalog == frozenset()


def test_large_offer_is_bounded():
    """One tool over the budget: bounded, not delivered whole."""
    limit = len(CORE_TOOL_NAMES)
    offer = frozenset(f"synthetic_tool_{i}" for i in range(limit + 1))
    inline, catalog = split_agent_offer(offer)
    assert inline is not None
    assert len(inline) <= limit
    assert catalog == offer - inline
    assert catalog  # something is genuinely lazy


def test_bounded_inline_always_includes_find_tools():
    """find_tools is the mechanism that makes the rest of the offer reachable — it must
    always be inline, even though no agent lists it in its own allowed_tools."""
    offer = _full_registry_offer()
    inline, _catalog = split_agent_offer(offer)
    assert inline is not None
    assert "find_tools" in inline


def test_bounded_inline_prioritizes_core_overlap():
    """The caller's own tools that overlap the interactive CORE set are inlined first —
    measured to be an agent's likely hot path (14-17 of 20 for both real oversized
    agents), so this is "free" prioritization, not a guess."""
    offer = _full_registry_offer()
    inline, _catalog = split_agent_offer(offer)
    core_overlap = offer & CORE_TOOL_NAMES
    assert core_overlap <= inline


def test_bounded_offer_never_exceeds_core_budget():
    offer = _full_registry_offer()
    inline, _catalog = split_agent_offer(offer)
    assert len(inline) <= len(CORE_TOOL_NAMES)


def test_catalog_is_exactly_offer_minus_inline():
    offer = _full_registry_offer()
    inline, catalog = split_agent_offer(offer)
    assert catalog == offer - inline
    assert inline | catalog >= offer  # nothing in offer is unaccounted for (mod find_tools)


def test_split_is_deterministic():
    """Same offer -> same split, every time — required for a stable (cacheable) tools=
    payload across an agent's repeated scheduled fires."""
    offer = _full_registry_offer()
    first = split_agent_offer(offer)
    second = split_agent_offer(offer)
    assert first == second


def test_never_adds_a_name_outside_offer_except_find_tools():
    offer = _full_registry_offer()
    inline, catalog = split_agent_offer(offer)
    extra = inline - offer
    assert extra <= {"find_tools"}
    assert catalog <= offer


# ---------------------------------------------------------------------------
# 2. assistant._active_tools() integration — the explicit-offer branch
# ---------------------------------------------------------------------------


def test_small_agent_offer_still_fully_inline_unchanged():
    """Regression pin: a small explicit offer (an agent's whole point) is completely
    unaffected by this module — byte-identical to before it existed."""
    small = {"get_net_worth", "telegram_send_message", "remove_positions"}
    _skill_scope.set_offer_scope(small)
    offered = {t["name"] for t in assistant._active_tools()}
    assert offered == small


def test_small_agent_offer_excludes_a_catalog_hidden_tool(monkeypatch):
    """_CATALOG_HIDDEN (ai/tools/catalog.py) must hold even for a SMALL explicit offer,
    where a caller (an agent's stored allowed_tools) named the hidden tool directly —
    without this, an agent configured with a hidden tool got its full schema inline, no
    discovery/hallucination needed, defeating the hide. Regression for a gap found by an
    independent review of docs/adr/2026-07-22-hide-tax-tab-and-tools.md: the
    schema-catalog/find_tools hide alone did not cover this path.

    The live set is empty since the tax feature was removed outright, so the filter is
    exercised by injecting a real tool name."""
    from backend.ai.tools import catalog as catalog_mod

    hidden = "get_returns"
    monkeypatch.setattr(catalog_mod, "_CATALOG_HIDDEN", frozenset({hidden}))
    small = {hidden, "get_net_worth", "telegram_send_message"}
    _skill_scope.set_offer_scope(small)
    offered_names = {t["name"] for t in assistant._active_tools()}
    assert hidden not in offered_names
    assert offered_names == small - {hidden}


def test_large_agent_offer_discovery_gate_never_loads_a_catalog_hidden_tool(monkeypatch):
    """The same gap, on the LARGE-offer discovery-gate path: even when a hidden tool
    isn't inlined this round, the gate must refuse to auto-load it just because the raw
    stored allowed_tools names it — offer_tool_names() is the single source both the
    inline offer and the gate read, so filtering there closes both at once."""
    from backend.ai.tools import catalog as catalog_mod

    hidden = "get_returns"
    monkeypatch.setattr(catalog_mod, "_CATALOG_HIDDEN", frozenset({hidden}))
    offer = _full_registry_offer() | {hidden}
    _skill_scope.set_offer_scope(offer)
    assert hidden not in _skill_scope.offer_tool_names()


def test_large_agent_offer_is_bounded_not_full_inline():
    """The whole point: a 270+-tool agent no longer gets 270+ schemas inline."""
    offer = _full_registry_offer()
    _skill_scope.set_offer_scope(offer)
    offered = assistant._active_tools()
    offered_names = {t["name"] for t in offered}
    assert len(offered) <= len(CORE_TOOL_NAMES) + 1  # CORE-overlap ∪ find_tools, no fill
    assert "find_tools" in offered_names
    assert offered_names <= offer | {"find_tools"}  # never wider than the caller's offer


def test_large_agent_offer_first_round_excludes_a_representative_rare_tool():
    offer = _full_registry_offer()
    _skill_scope.set_offer_scope(offer)
    offered_names = {t["name"] for t in assistant._active_tools()}
    assert "get_returns" in offer  # sanity: it's a real, offered-in-full-scope tool
    assert "get_returns" not in offered_names  # but not inlined this round


def test_large_agent_offer_honors_extra_names_loaded_this_turn():
    """A tool this SAME turn already auto-loaded (via the discovery gate / find_tools)
    stays in the offer on the next round — survives across the run's own rounds."""
    offer = _full_registry_offer()
    set_extra_names(frozenset({"get_returns"}))
    _skill_scope.set_offer_scope(offer)
    offered_names = {t["name"] for t in assistant._active_tools()}
    assert "get_returns" in offered_names


def test_large_agent_offer_extra_names_never_widen_past_the_offer():
    """A name loaded under some OTHER turn's offer (a stale ContextVar value) must never
    leak into THIS turn's offer if it isn't in THIS turn's own allowed_tools."""
    limit = len(CORE_TOOL_NAMES)
    offer = frozenset(f"synthetic_tool_{i}" for i in range(limit + 5))
    set_extra_names(frozenset({"get_returns"}))  # a REAL tool, but not in `offer`
    _skill_scope.set_offer_scope(offer)
    offered_names = {t["name"] for t in assistant._active_tools()}
    assert "get_returns" not in offered_names


def test_nested_skill_scope_under_a_large_offer_is_unaffected():
    """A skill running INSIDE a large agent offer intersects and is delivered whole —
    skills are out of scope for this bound (their own allowlists are already small)."""
    offer = _full_registry_offer()
    _skill_scope.set_offer_scope(offer)
    _skill_scope.set_offer_scope(["get_net_worth", "telegram_send_message"])
    offered_names = {t["name"] for t in assistant._active_tools()}
    assert offered_names <= {"get_net_worth", "telegram_send_message"} | {
        "list_skills",
        "activate_skill",
        "create_skill",
        "update_skill",
        "delete_skill",
        "apply_skill_change",
    }


def test_full_tool_offer_env_still_bypasses_everything(monkeypatch):
    """ONW_FULL_TOOL_OFFER=1 is a debug escape hatch and must still win outright — it is
    checked only on the no-offer/no-scope path, unaffected by this module."""
    monkeypatch.setenv("ONW_FULL_TOOL_OFFER", "1")
    offered = assistant._active_tools()
    assert offered == list(TOOL_SCHEMAS)


# ---------------------------------------------------------------------------
# 3. find_tools scoping under a large explicit offer — never wider than `offer`
# ---------------------------------------------------------------------------


async def _find(**kwargs):
    from backend.ai.tools._groups import find_tools

    text, _proposed = await find_tools(kwargs)
    return json.loads(text)


def test_find_tools_query_scoped_to_the_agents_own_offer():
    """A large agent's find_tools(query=...) must never surface a tool OUTSIDE its own
    configured allowed_tools — the search space is the offer, never the full registry."""
    limit = len(CORE_TOOL_NAMES)
    # An offer that deliberately EXCLUDES a real, easily-matched tool.
    offer = frozenset(registered_tool_names()) - {"find_tools", "get_rebalance_plan"}
    assert len(offer) > limit
    _skill_scope.set_offer_scope(offer)

    async def _go():
        return await _find(query="rebalance plan")

    result = asyncio.run(_go())
    loaded_names = {item["name"] for item in result.get("loaded", [])}
    assert "get_rebalance_plan" not in loaded_names


def test_find_tools_query_still_finds_a_tool_that_is_in_the_offer():
    offer = _full_registry_offer()
    _skill_scope.set_offer_scope(offer)

    async def _go():
        return await _find(query="rebalance plan")

    result = asyncio.run(_go())
    loaded_names = {item["name"] for item in result.get("loaded", [])}
    assert "get_rebalance_plan" in loaded_names


def test_find_tools_names_scoped_to_the_agents_own_offer():
    """get_returns is a REAL, registered tool — but it's excluded from THIS agent's own
    offer, so find_tools(names=[...]) must treat it exactly like a name that doesn't
    exist at all (the same "no such tool" shape), never load it."""
    offer = frozenset(registered_tool_names()) - {"find_tools", "get_returns"}
    _skill_scope.set_offer_scope(offer)

    async def _go():
        return await _find(names=["get_returns"])

    result = asyncio.run(_go())
    assert "loaded" not in result
    assert "no such tool" in result.get("error", "")


def test_bounded_inline_sends_the_offers_writes_before_reads():
    """A deferred write always bounces at the discovery gate, so a bounded offer inlines its
    writes first — a paper Autopilot review lost its memo when propose_paper_orders and
    record_autopilot_memo sorted past the budget."""
    from backend.services.paper_trading.autopilot_run import TOOLS

    offer = frozenset(TOOLS)
    assert len(offer) > len(CORE_TOOL_NAMES)  # sanity: the review offer is bounded
    inline, catalog = split_agent_offer(offer)
    assert inline is not None
    for name in ("propose_paper_orders", "record_autopilot_memo", "cancel_paper_orders"):
        assert name in inline
        assert name not in catalog
    assert len(inline) <= len(CORE_TOOL_NAMES)
