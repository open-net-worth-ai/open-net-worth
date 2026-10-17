"""Tests for the ``find_tools`` meta-tool (tool discovery).

Discovery is keyword search ONLY — tool groups and the ``groups=`` parameter are
gone (matching Claude Code's ToolSearch shape: ``query`` + ``max_results``).

Covers:
- CORE_TOOL_NAMES invariants (find_tools present, not in the derived rare set).
- query returns expected tools for known phrases.
- query loads names into the extra-name ContextVar.
- max_results bounds the result set and is clamped defensively.
- a legacy ``groups=`` argument is rejected cleanly, never crashing.
- missing query → friendly error JSON.
- garbage query → empty loaded list, no crash.
- ContextVars are reset between tests.

Offline + isolated: no LLM, no network.

Run:
    .venv/bin/pytest tests/test_find_tools.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools._extra_groups_scope import get_extra_names, set_extra_names
from backend.ai.tools._groups import (
    _DEFAULT_MAX_RESULTS,
    _MAX_MAX_RESULTS,
    CORE_TOOL_NAMES,
    _clamp_max_results,
    _search_tools,
    find_tools,
    rare_tool_names,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_scopes():
    """Reset the extra-name ContextVar before and after each test."""
    set_extra_names(frozenset())
    yield
    set_extra_names(frozenset())


# ---------------------------------------------------------------------------
# CORE_TOOL_NAMES invariants
# ---------------------------------------------------------------------------


def test_find_tools_in_core():
    assert "find_tools" in CORE_TOOL_NAMES


def test_find_tools_not_in_rare():
    assert "find_tools" not in rare_tool_names()


def test_core_names_not_in_rare():
    """CORE ∩ rare must be empty — guaranteed by construction (rare = registry - CORE)."""
    overlap = CORE_TOOL_NAMES & rare_tool_names()
    assert overlap == frozenset(), f"Core ∩ rare overlap: {sorted(overlap)}"


def test_core_contains_expected_tools():
    """The measured-evidence CORE set (docs/adr/2026-07-19-core-tool-offer-diet.md):
    ``get_ledger_summary``, ``get_spending_summary``, ``create_payees`` (was
    ``create_payee``), ``fetch_emails``, ``notify``, ``list_checkpoints`` and
    ``restore_checkpoint`` were demoted — each
    measured under 4% real-session spread and none carrying import_transactions' fix-a-
    measured-bug justification. They remain fully reachable via the discovery gate."""
    expected = {
        "calculate",
        "get_transactions",
        "add_transactions",
        "update_transactions",
        "import_transactions",
        "list_accounts",
        "get_portfolio",
        "get_live_prices",
        "get_profile",
        "list_categories",
        "get_payees",
        "recall_memory",
    }
    missing = expected - CORE_TOOL_NAMES
    assert missing == set(), f"Expected core tools missing: {sorted(missing)}"


def test_demoted_tools_are_reachable_not_deleted():
    """Five of the seven originally-demoted tools must still exist in the live registry
    (reachable via the discovery gate / find_tools) — demotion changes DEFAULT OFFER,
    never REACH. ``fetch_emails`` and ``notify`` are the two exceptions: both were later
    retired outright (``fetch_emails``: ADR 2026-08-12-email-search-hydrate-split,
    replaced by search_messages + get_messages; ``notify``: replaced by the per-channel
    telegram_send_message / webhook_send_message tools, no compatibility alias), a
    materially different outcome from demotion — genuinely gone, not merely un-offered."""
    from backend.ai.tools.registry import registered_tool_names

    live = set(registered_tool_names())
    demoted = {
        "get_ledger_summary",
        "get_spending_summary",
        "create_payees",
        "list_checkpoints",
        "restore_checkpoint",
    }
    retired = {"fetch_emails", "notify"}
    assert retired.isdisjoint(live), f"Retired tool(s) still in registry: {retired & live}"
    assert demoted <= live, f"Demoted tool(s) missing from the registry entirely: {demoted - live}"
    assert demoted & CORE_TOOL_NAMES == set(), (
        "These tools were deliberately demoted from CORE — "
        f"still present: {sorted(demoted & CORE_TOOL_NAMES)}"
    )


# ---------------------------------------------------------------------------
# _search_tools unit tests
# ---------------------------------------------------------------------------


def test_search_retirement_simulation():
    """'retirement simulation' should surface simulate_retirement."""
    results = _search_tools("retirement simulation")
    assert "simulate_retirement" in results


def test_search_rebalance():
    """'rebalance' should surface get_rebalance_plan."""
    results = _search_tools("rebalance")
    assert "get_rebalance_plan" in results


def test_search_returns_bounded_results():
    """Default limit of 10 is respected."""
    results = _search_tools("get")
    assert len(results) <= 10


def test_search_garbage_query_no_crash():
    """A completely nonsensical query should return empty list without crashing."""
    results = _search_tools("xyzzy_nonexistent_gibberish_zzz123")
    assert results == []


def test_search_empty_query_tokens_no_crash():
    """A query that tokenizes to nothing (only short punctuation) returns empty."""
    results = _search_tools("!!!---???")
    assert results == []


def test_search_net_worth_surfaces_tool():
    """'net worth' query should surface get_net_worth."""
    results = _search_tools("net worth")
    assert "get_net_worth" in results


# ---------------------------------------------------------------------------
# find_tools handler — missing query → error
# ---------------------------------------------------------------------------


def test_find_tools_no_query_returns_error():
    async def _inner():
        text, change = await find_tools({})
        assert change is None
        data = json.loads(text)
        assert "error" in data
        assert "query" in data["error"]

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# find_tools handler — the removed `groups` parameter
# ---------------------------------------------------------------------------


def test_find_tools_schema_has_no_groups_parameter():
    """No GROUP concept in the schema — the thing this test actually guards.

    Asserted as an exact param set until 2026-07-19, which made it a tripwire for any new
    parameter rather than for the group concept: `names` (load exact tools by name, the
    cheaper alternative to a keyword search) was added deliberately and is pinned by
    tests/test_discovery_gate.py, and `required` was dropped because either `query` OR
    `names` now satisfies the call. Narrowed to the invariant in the docstring, with an
    allowlist so a genuinely unexpected parameter is still caught.
    """
    from backend.ai.tools import TOOL_SCHEMAS

    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "find_tools")
    props = schema["input_schema"]["properties"]
    assert "groups" not in props, "the group concept must stay out of the find_tools schema"
    assert set(props) <= {
        "query",
        "names",
        "max_results",
    }, f"unexpected find_tools params: {sorted(props)}"


def test_find_tools_legacy_groups_only_call_errors_cleanly():
    """A legacy `groups=[...]` call (no query) gets the query error, never a crash."""

    async def _inner():
        text, change = await find_tools({"groups": ["analysis_deep"]})
        assert change is None
        data = json.loads(text)
        assert "error" in data
        assert "query" in data["error"]

    asyncio.run(_inner())


def test_find_tools_ignores_stray_groups_argument():
    """`groups` alongside a real query is ignored — the search still runs normally."""

    async def _inner():
        text, change = await find_tools(
            {"query": "retirement simulation", "groups": ["analysis_deep"]}
        )
        assert change is None
        data = json.loads(text)
        assert "error" not in data
        assert "enabled_groups" not in data
        loaded_names = [item["name"] for item in data["loaded"]]
        assert "simulate_retirement" in loaded_names
        # Loading by group would have pulled in the whole former analysis_deep bucket.
        assert "get_portfolio_xray" not in loaded_names

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# find_tools handler — max_results
# ---------------------------------------------------------------------------


def test_find_tools_max_results_bounds_the_result_set():
    async def _inner():
        text, _ = await find_tools({"query": "get", "max_results": 3})
        data = json.loads(text)
        assert len(data["loaded"]) <= 3

    asyncio.run(_inner())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, _DEFAULT_MAX_RESULTS),
        ("not a number", _DEFAULT_MAX_RESULTS),
        (0, 1),
        (-5, 1),
        (7, 7),
        (10_000, _MAX_MAX_RESULTS),
    ],
)
def test_clamp_max_results(raw, expected):
    """A model-supplied max_results is coerced into [1, _MAX_MAX_RESULTS], never raises."""
    assert _clamp_max_results(raw) == expected


# ---------------------------------------------------------------------------
# find_tools handler — query search
# ---------------------------------------------------------------------------


def test_find_tools_query_loads_names():
    """find_tools(query=…) loads found names into the extra-names ContextVar."""

    async def _inner():
        text, change = await find_tools({"query": "retirement simulation"})
        assert change is None
        data = json.loads(text)
        assert "loaded" in data
        loaded_names = [item["name"] for item in data["loaded"]]
        assert "simulate_retirement" in loaded_names
        # ContextVar reflects the loaded names within the same async context
        assert "simulate_retirement" in get_extra_names()

    asyncio.run(_inner())


def test_find_tools_query_garbage_no_crash():
    """A garbage query that matches nothing returns empty loaded list without an error key."""

    async def _inner():
        text, change = await find_tools({"query": "xyzzy_nonexistent_gibberish_zzz123"})
        assert change is None
        data = json.loads(text)
        assert "error" not in data
        assert data["loaded"] == []

    asyncio.run(_inner())


def test_find_tools_query_result_has_summary():
    """Each loaded item includes a 'summary' field."""

    async def _inner():
        text, _ = await find_tools({"query": "rebalance plan"})
        data = json.loads(text)
        assert data["loaded"]  # non-empty, else the field assertions below are vacuous
        for item in data["loaded"]:
            assert "name" in item
            assert "summary" in item

    asyncio.run(_inner())


@pytest.mark.parametrize(
    "query",
    [
        "reminder",
        "bill payment reminder",
        "remind me to pay my card bill",
        "nudge me before a due date",
    ],
)
def test_a_reminder_query_finds_the_automation_tools(query):
    """Regression (2026-08-09): a request to set a reminder for an upcoming credit-card bill
    made Lucius propose a COMMITMENT — a tracked obligation in the plan — when the user
    wanted a notification.

    It was not the model improvising. Search scores query tokens against tool name +
    description only, and "remind" appeared nowhere in any Automation tool's text while it
    appeared eleven times in commitments.py (``reminder_automation_id``). No automation tool
    is in CORE_TOOL_NAMES either, so discovery was the ONLY path to create_automation — and
    every reminder-phrased query returned commitment tools exclusively. The model proposed
    the only shape it was shown.

    A reminder is an Automation: a schedule trigger whose run notifies. This pins the
    vocabulary that makes it reachable.
    """

    async def _inner():
        text, _ = await find_tools({"query": query})
        loaded = [item["name"] for item in json.loads(text)["loaded"]]
        assert "create_automation" in loaded, loaded

    asyncio.run(_inner())


def test_an_external_mcp_tool_loaded_by_name_gets_a_real_summary():
    """Owner-reported: ``find_tools(names=[...])`` on an external MCP tool
    (``mcp__<server>__<tool>``) always returned ``"summary": ""``, even for a server the
    assistant had genuinely discovered and offered. Root cause: ``_tool_summary`` only ever
    searched the static internal ``TOOL_SCHEMAS`` registry, which an external tool — loaded
    at runtime from the server's own handshake, never registered as a first-party tool — can
    never appear in. Regression: register a fake dispatcher (mirrors
    ``test_skill_host_runtime.py``'s own pattern) and confirm the summary is populated from
    ITS schema instead."""
    from backend.core import mcp_external

    external_name = "mcp__vantage-brokerage-mcp__get_account_info"

    class _Dispatcher:
        def schemas(self):
            return [
                {
                    "name": external_name,
                    "description": "Fetch the connected Vantage Brokerage account's balances and positions.",
                    "input_schema": {},
                }
            ]

        async def call(self, name, args):
            raise AssertionError("summary lookup must never dispatch a call")

    mcp_external.register_external_dispatcher(_Dispatcher())
    try:

        async def _inner():
            text, _ = await find_tools({"names": [external_name]})
            loaded = {item["name"]: item["summary"] for item in json.loads(text)["loaded"]}
            assert loaded.get(external_name) == (
                "Fetch the connected Vantage Brokerage account's balances and positions."
            ), loaded

        asyncio.run(_inner())
    finally:
        mcp_external.register_external_dispatcher(None)


def test_find_tools_result_is_bounded_at_the_tool():
    """A broad query used to return up to 50 full descriptions (35k chars, re-sent on every
    later round of an unattended run). The result now stays inside a fixed budget: ranked
    hits keep full text while it fits, the rest carry a one-line summary and `truncated`."""
    from backend.ai.tools._groups import _SUMMARY_BUDGET_CHARS, _TAIL_SUMMARY_CHARS

    async def _inner():
        text, _ = await find_tools({"query": "get list create update add", "max_results": 10_000})
        loaded = json.loads(text)["loaded"]
        assert {item["name"] for item in loaded} <= get_extra_names()  # truncated ones load too
        return text, loaded

    text, loaded = asyncio.run(_inner())

    assert 0 < len(loaded) <= _MAX_MAX_RESULTS
    full = [item for item in loaded if not item.get("truncated")]
    tail = [item for item in loaded if item.get("truncated")]
    assert tail, "this query must overflow the budget, or the test pins nothing"
    assert sum(len(item["summary"]) for item in full) <= _SUMMARY_BUDGET_CHARS
    assert all(len(item["summary"]) <= _TAIL_SUMMARY_CHARS for item in tail)
    assert len(text) < _SUMMARY_BUDGET_CHARS + _MAX_MAX_RESULTS * 250


def test_a_small_result_keeps_every_full_description():
    from backend.ai.tools._groups import _bounded_summaries, _tool_summary

    out = _bounded_summaries(["get_returns"])

    assert out == [{"name": "get_returns", "summary": _tool_summary("get_returns")}]
