"""Lane R3 tool-scoping tests — updated for the group-free discovery model.

The R3 "group definitions" this file was written against are gone (TOOL_GROUPS and
the ``groups=`` load path were removed to match Claude Code's ToolSearch shape).
The invariants that outlived them — and are pinned here:

- Default offer is exactly CORE_TOOL_NAMES, in the 10–17 target range (the
  2026-07-19 evidence-based CORE diet — see docs/adr/2026-07-19-core-tool-offer-diet.md).
- Representative admin tools stay out of it until find_tools loads them by name.
- find_tools(query=…) surfaces a domain's tools without any group concept.
- The must-stay-core tools are core.
- Headless offer is unchanged by anything find_tools loaded.
- statement-import skill bundle includes rollback/restore/rollforward.
"""

from __future__ import annotations

import json

import pytest

from backend.ai import assistant
from backend.ai.tools import TOOL_SCHEMAS, _skill_scope
from backend.ai.tools._extra_groups_scope import set_extra_names
from backend.ai.tools._groups import CORE_TOOL_NAMES, rare_tool_names

# A small explicit tool offer, standing in for what a headless caller now passes.
_SAMPLE_OFFER = {"get_net_worth", "get_transactions", "get_portfolio", "telegram_send_message"}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_scopes():
    _skill_scope.clear_scope()
    set_extra_names(frozenset())
    yield
    _skill_scope.clear_scope()
    set_extra_names(frozenset())


# ---------------------------------------------------------------------------
# 1. Default offer excludes the tools that used to live in rare groups
# ---------------------------------------------------------------------------

# One representative from each of the twelve retired groups. These are ordinary
# rare tools now — reachable by keyword search, never by a group name.
_FORMERLY_GROUPED = [
    "create_automation",  # was agents_admin — Agents retired 2026-08-02, Automations are
    #                       the surviving unattended-work surface
    "email_send_message",  # connectors_admin
    "get_model_config",  # system_admin
    "create_skill",  # skills_authoring
    "assign_budget",  # budgeting
    "create_grant",  # equity_writes
    "open_option",  # option_writes
    "rename_payees",  # payees_admin
    "update_automations",  # was rules_engine — a categorization rule IS an Automation now
    "scan_intel",  # market_intel
    "create_commitment",  # commitments
    "simulate_retirement",  # analysis_deep
]


@pytest.mark.parametrize("tool_name", _FORMERLY_GROUPED)
def test_formerly_grouped_tool_is_rare_and_not_offered(tool_name):
    """Each is in the derived rare set and absent from the default slim offer."""
    assert tool_name in rare_tool_names(), f"'{tool_name}' should be rare (registry - CORE)"
    offered = {t["name"] for t in assistant._active_tools()}
    assert tool_name not in offered, f"'{tool_name}' leaked into the default offer"


# ---------------------------------------------------------------------------
# 2. find_tools(query=…) reaches a domain with no group concept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("retirement simulation", "simulate_retirement"),
        # 'tax loss harvest' was dropped (Task 87 — the tax tools are catalog-hidden and
        # deliberately unreachable via find_tools, tests/test_find_tools.py pins that).
        ("realized gains losses", "get_realized_pnl"),
        ("rebalance portfolio", "get_rebalance_plan"),
        ("spend categorization rule", "create_automation"),
    ],
)
async def test_find_tools_query_reaches_domain(query, expected):
    """Keyword search is the ONLY load path — it must reach each former group's tools."""
    from backend.ai.tools._groups import find_tools

    before = {t["name"] for t in assistant._active_tools()}
    assert expected not in before, f"'{expected}' already in default offer (should be rare)"

    result_json, proposed = await find_tools({"query": query})
    assert proposed is None
    result = json.loads(result_json)
    assert "error" not in result
    loaded_names = {item["name"] for item in result["loaded"]}
    assert expected in loaded_names, f"query {query!r} did not surface {expected!r}"

    after = {t["name"] for t in assistant._active_tools()}
    assert expected in after, f"'{expected}' not offered after find_tools loaded it"


# ---------------------------------------------------------------------------
# 3. Default offer is exactly CORE_TOOL_NAMES
# ---------------------------------------------------------------------------


def test_default_offer_is_exactly_core_tool_names():
    offered = {t["name"] for t in assistant._active_tools()}
    assert offered == CORE_TOOL_NAMES, (
        f"Default offer deviates from CORE_TOOL_NAMES.\n"
        f"Extra: {sorted(offered - CORE_TOOL_NAMES)}\n"
        f"Missing: {sorted(CORE_TOOL_NAMES - offered)}"
    )


@pytest.mark.parametrize("tool_name", sorted(CORE_TOOL_NAMES))
def test_core_tool_names_present_in_default_offer(tool_name):
    """Every CORE_TOOL_NAMES tool is offered in the default slim offer."""
    offered = {t["name"] for t in assistant._active_tools()}
    assert tool_name in offered, f"CORE tool '{tool_name}' missing from default offer"


# ---------------------------------------------------------------------------
# 4. Must-stay-core tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    [
        "find_tools",  # the discovery escape hatch
        "list_accounts",  # #1 tool by measured session-spread (36.2%)
        "get_transactions",  # #2 tool by measured session-spread (34.1%)
        "import_transactions",  # low measured spread, kept on structural grounds — see ADR
    ],
)
def test_must_stay_core(tool_name):
    assert tool_name in CORE_TOOL_NAMES
    assert tool_name not in rare_tool_names()


@pytest.mark.parametrize(
    "tool_name",
    [
        "email_search_messages",
        "list_checkpoints",
        "restore_checkpoint",
        "telegram_send_message",
        "create_payees",
    ],
)
def test_demoted_by_core_diet_now_rare(tool_name):
    """These were the prior lane's "must-stay-core" set; the 2026-07-19 CORE diet
    (docs/adr/2026-07-19-core-tool-offer-diet.md) demoted them on measured session-spread
    evidence (all <4%). Still fully reachable via the discovery gate — just not inline
    by default. (``fetch_emails`` was one of the five originally, but has since been
    retired outright — ADR 2026-08-12-email-search-hydrate-split — so it is replaced here
    with its successor, ``email_search_messages``, which was never added to CORE either.
    Likewise ``notify`` was retired 2026-08-20 in favor of per-channel tools — replaced here
    with ``telegram_send_message``, which was never added to CORE either.)"""
    assert tool_name not in CORE_TOOL_NAMES
    assert tool_name in rare_tool_names()


# ---------------------------------------------------------------------------
# 5. Headless offer is unchanged (security boundary)
# ---------------------------------------------------------------------------


def test_headless_offer_unaffected_by_loaded_names():
    """Loading every rare tool by name must NOT widen the headless offer."""
    set_extra_names(rare_tool_names())
    _skill_scope.set_offer_scope(_SAMPLE_OFFER)
    offered = {t["name"] for t in assistant._active_tools()}
    assert offered == _SAMPLE_OFFER


# ---------------------------------------------------------------------------
# 6. Default offer size / cost
# ---------------------------------------------------------------------------


def test_default_offer_tool_count_in_target_range():
    """Default core offer must have 10–17 tools (the 2026-07-19 evidence-based CORE diet)."""
    count = len(assistant._active_tools())
    assert 10 <= count <= 17, (
        f"Default offer has {count} tools — outside the 10-17 target range. "
        "Re-check CORE_TOOL_NAMES assignments."
    )


def test_default_offer_json_size_in_target_range():
    """Default slim offer JSON must be under 55K chars (per-turn token cost). Owner call
    2026-10-27: raised from 50K, which the offer had passed since the initial commit (51,170)."""
    size = len(json.dumps(assistant._active_tools()))
    assert size < 55_000, (
        f"Default offer JSON is {size:,} chars — over the 55K target. "
        "Demote a verbose tool out of CORE_TOOL_NAMES."
    )


# ---------------------------------------------------------------------------
# 7. find_tools description no longer advertises groups
# ---------------------------------------------------------------------------


def test_find_tools_description_has_no_group_concept():
    """The description must describe keyword search, not a group taxonomy."""
    ft = next(t for t in TOOL_SCHEMAS if t["name"] == "find_tools")
    desc = ft["description"].lower()
    assert "search" in desc
    assert "group" not in desc, "find_tools description still advertises groups"


# ---------------------------------------------------------------------------
# 8. Statement-import skill bundle
# ---------------------------------------------------------------------------
# _ALLOWED_TOOLS and _BUNDLE_VERSION were removed from statement_import_bundle.py:
# a skill no longer narrows a turn's tool scope at all (skills are instructions, not
# tool policy — see skill_creator_bundle.py), so rollback/restore tools are always
# reachable regardless of which skill is active. Nothing left here to pin.


def test_the_retired_rollforward_name_is_gone():
    """Nothing resolves a retired name at runtime (ADR 2026-08-25-tool-names-are-final).

    This used to also assert the rename ledger could rewrite the old name onto the new one.
    That half went with the ledger's 90 historical entries (emptied 2026-08-28) — they
    recorded renames made in a predecessor repository, and no stored allowlist reachable from
    this one can carry a name from there. What survives is the live half: the old name is not
    a tool, the new one is."""
    from backend.ai.tools.registry import registered_tool_names

    assert "rollforward_import_batch" not in registered_tool_names()
    assert "restore_import_batch" in registered_tool_names()
