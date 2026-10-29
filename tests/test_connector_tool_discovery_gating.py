"""Connector discovery gating — the ``connector=`` tag on the tool registry, and the
catalog/``find_tools`` enforcement of it (RFC ``.claude/rfcs/2026-08-10-connectors-
architecture.md`` §B2).

Covers:
  - registry mechanics: ``tool_connector``/``connector_tagged_tools`` (alias-resolving,
    untagged tools return None/absent)
  - ``ai.tools._connector_gate.hidden_connector_tools`` — the shared computation both
    surfaces below consult
  - the flat system-prompt catalog (``ai.tools.catalog.build_tool_catalog``) excludes a
    disabled connector's tool, includes it once enabled
  - ``find_tools`` (query search AND exact ``names=``) excludes/includes the same way
  - INTERNAL (untagged) tools are completely unaffected by connector state either way —
    the explicit regression guard the task calls for
  - fail-closed behavior when the enablement lookup itself breaks
  - dispatch/execution is NEVER blocked by this gate — discovery only

Offline, isolated by the suite's autouse Tier-2 table clear (tests/conftest.py); no LLM /
network.
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools import execute_tool
from backend.ai.tools._confirm_gate import effective_level
from backend.ai.tools._connector_gate import hidden_connector_tools, permission_owner
from backend.ai.tools._groups import _candidate_schemas, _search_tools, find_tools
from backend.ai.tools.catalog import build_tool_catalog
from backend.ai.tools.registry import connector_tagged_tools, tool_connector
from backend.services import connectors as connectors_service


def call(name: str, **inp):
    text, proposed = asyncio.run(execute_tool(name, inp))
    return json.loads(text), proposed


# ── registry mechanics ──────────────────────────────────────────────────────────────


def test_tool_connector_reports_the_tag():
    assert tool_connector("plaid_fetch_transactions") == "plaid"
    assert tool_connector("tavily_search") == "web_search:tavily"


def test_tool_connector_none_for_an_untagged_internal_tool():
    assert tool_connector("list_accounts") is None
    assert tool_connector("get_transactions") is None


def test_tool_connector_none_for_an_unknown_name():
    assert tool_connector("not_a_real_tool_xyz") is None


def test_tool_connector_none_for_the_bulk_convenience_tools():
    """Documented decision: connector_sync/get_connector_status/web_search/fetch_page span
    (or don't fit) a single connector, so they carry no tag and are never gated."""
    for name in ("connector_sync", "get_connector_status", "web_search", "fetch_page", "x_search"):
        assert tool_connector(name) is None, name


def test_connector_tagged_tools_covers_every_promoted_tool():
    tagged = connector_tagged_tools()
    assert tagged["plaid_fetch_transactions"] == "plaid"
    assert tagged["ibkr_get_status"] == "ibkr"
    assert tagged["serper_search"] == "web_search:serper"
    assert tagged["google_pse_fetch_page"] == "web_search:google_pse"
    # All three email comms blocks — receive (email_search_messages/email_get_messages) and send
    # (email_send_message) — are scoped to the Email connector, not the shared `core` bucket.
    assert tagged["email_search_messages"] == "email"
    assert tagged["email_get_messages"] == "email"
    assert tagged["email_send_message"] == "email"


# ── permission_owner() — the JOIN onto tool_permissions ─────────────────────────────
#
# Regression: email_send_message/email_search_messages/email_get_messages were untagged, so a decision made
# through the Email connector's OWN "Tool permissions" ladder (which writes under
# connector_id="email", taken verbatim from the URL) was stored under a DIFFERENT owner
# than dispatch ever consulted (`core`) — a grant a user believed they set was silently
# never read. Tagging closes it: both surfaces now agree on the owner.


def test_permission_owner_resolves_all_three_email_tools_to_email():
    assert permission_owner("email_send_message") == "email"
    assert permission_owner("email_search_messages") == "email"
    assert permission_owner("email_get_messages") == "email"


def test_email_tools_default_levels_are_unchanged_by_the_tag():
    """The tag changes WHO a decision is filed under, never whether an undecided call
    needs a human click. ``email_send_message`` is a real write with an irreversible external
    effect — it asked before tagging (via the `core` bucket's own approval-model verdict)
    and must still ask now (via the connector default, which is the SAME answer here).
    ``email_search_messages``/``email_get_messages`` are the read-shaped-exempt pair (owner incident
    2026-07-21, ``GATE_EXEMPT_READ_SHAPED``) — they ran with NO approval before tagging and
    must still run with none now; a regression here would silently reintroduce "why does
    reading my own inbox need permission?" for every fresh install."""
    assert effective_level("email_send_message") == "needs_approval"
    assert effective_level("email_search_messages") == "always_allow"
    assert effective_level("email_get_messages") == "always_allow"


def test_an_explicit_decision_on_a_read_shaped_email_tool_still_wins():
    """The exemption is only the UNDECIDED default — an explicit stored decision (set
    through the Email connector's own ladder, owner="email") still overrides it in either
    direction, exactly like any other tool."""
    from backend.services import tool_permissions

    tool_permissions.set_tool_permission("email", "email_search_messages", "blocked")
    assert effective_level("email_search_messages") == "blocked"
    tool_permissions.set_tool_permission("email", "email_get_messages", "always_allow")
    assert effective_level("email_get_messages") == "always_allow"


# ── hidden_connector_tools() ────────────────────────────────────────────────────────


def test_disabled_connectors_hide_their_tools_by_default():
    """Fresh test DB: no aggregation provider is configured/enabled, so every
    data-source tool is hidden by default."""
    hidden = hidden_connector_tools()
    assert "plaid_fetch_transactions" in hidden
    assert "plaid_get_status" in hidden
    assert "ibkr_fetch_activity" in hidden


def test_duckduckgo_is_never_hidden_by_default():
    """DuckDuckGo is the zero-config default — enabled with no setup, so its own
    per-provider tools must be reachable from a fresh install too."""
    hidden = hidden_connector_tools()
    assert "duckduckgo_search" not in hidden
    assert "duckduckgo_fetch_page" not in hidden


def test_enabling_a_connector_unhides_its_tools():
    connectors_service.set_connector_enabled("plaid", True)
    hidden = hidden_connector_tools()
    assert "plaid_fetch_transactions" not in hidden
    assert "plaid_get_status" not in hidden
    # A DIFFERENT provider stays hidden — enabling one must not widen every tag.
    assert "ibkr_fetch_activity" in hidden


def test_disabling_a_connector_rehides_its_tools():
    connectors_service.set_connector_enabled("plaid", True)
    assert "plaid_fetch_transactions" not in hidden_connector_tools()
    connectors_service.set_connector_enabled("plaid", False)
    assert "plaid_fetch_transactions" in hidden_connector_tools()


def test_enabling_a_web_search_provider_unhides_only_that_providers_tools():
    connectors_service.set_connector_enabled("web_search:tavily", True)
    hidden = hidden_connector_tools()
    assert "tavily_search" not in hidden
    assert "tavily_fetch_page" not in hidden
    assert "serper_search" in hidden  # untouched


def test_enablement_lookup_failure_fails_closed(monkeypatch):
    """An enablement-lookup error must HIDE every connector-tagged tool, never show one
    uncertainty couldn't confirm — matching this codebase's other discovery gates."""

    def boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(connectors_service, "enabled_connector_ids", boom)
    hidden = hidden_connector_tools()
    assert "plaid_fetch_transactions" in hidden
    assert "duckduckgo_search" in hidden  # even the normally-always-on default


def test_email_tools_are_hidden_until_the_connector_is_enabled():
    hidden = hidden_connector_tools()
    assert "email_send_message" in hidden
    assert "email_search_messages" in hidden
    assert "email_get_messages" in hidden

    connectors_service.set_connector_enabled("email", True)
    hidden = hidden_connector_tools()
    assert "email_send_message" not in hidden
    assert "email_search_messages" not in hidden
    assert "email_get_messages" not in hidden
    # A different provider's tools are untouched.
    assert "plaid_fetch_transactions" in hidden


def test_no_tagged_tools_is_a_fast_empty_path(monkeypatch):
    """When nothing is tagged, the function must not even attempt the enablement
    lookup — a cheap short-circuit, and incidentally proves it can't spuriously hide an
    untagged tool."""
    monkeypatch.setattr("backend.ai.tools.registry.connector_tagged_tools", lambda: {})
    assert hidden_connector_tools() == frozenset()


# ── DISABLED_CONNECTORS: withdrawn by policy, not merely unconfigured ────────────────
#
# Yodlee (2026-08-14, ADR 2026-08-14-hide-yodlee-enterprise-only-pricing): enterprise-only
# pricing makes it impractical for this app's actual self-hosted users. Unlike every other
# connector-tagged tool above, it stays hidden even if its store reports enabled=True —
# see test_yodlee_tool_is_refused_at_dispatch_unlike_every_other_gated_connector below for
# the dispatch-level half of this same exception.


def test_yodlee_tools_are_hidden_by_default():
    hidden = hidden_connector_tools()
    assert "yodlee_get_status" in hidden
    assert "yodlee_fetch_transactions" in hidden


def test_yodlee_tools_stay_hidden_even_if_its_store_reports_enabled():
    """The one connector where 'enabled' in the store does NOT unhide its tools — covers
    an instance that had Yodlee configured before this withdrawal, or an env-seeded
    credential."""
    connectors_service.set_connector_enabled("yodlee", True)
    assert "yodlee" in connectors_service.enabled_connector_ids()  # sanity: really enabled
    hidden = hidden_connector_tools()
    assert "yodlee_get_status" in hidden
    assert "yodlee_fetch_transactions" in hidden


# ── the flat catalog ─────────────────────────────────────────────────────────────────


def test_catalog_excludes_a_disabled_connectors_tool():
    catalog = build_tool_catalog()
    assert "plaid_fetch_transactions" not in catalog
    assert "plaid_get_status" not in catalog


def test_catalog_includes_an_enabled_connectors_tool():
    connectors_service.set_connector_enabled("plaid", True)
    catalog = build_tool_catalog()
    assert "plaid_fetch_transactions" in catalog
    assert "plaid_get_status" in catalog


def test_catalog_never_includes_yodlee_tools_even_when_enabled():
    """Contrast with the Plaid test just above: Yodlee is DISABLED_CONNECTORS, so
    enabling its store flag does not unhide it the way it does for an ordinary
    not-yet-configured connector."""
    connectors_service.set_connector_enabled("yodlee", True)
    catalog = build_tool_catalog()
    assert "yodlee_get_status" not in catalog
    assert "yodlee_fetch_transactions" not in catalog


def test_catalog_always_includes_duckduckgo_tools():
    catalog = build_tool_catalog()
    assert "duckduckgo_search" in catalog


def test_catalog_untouched_internal_tool_names_always_present():
    """The explicit regression guard: an internal (untagged) tool's presence in the
    catalog must be completely independent of any connector's enabled state."""
    catalog_before = build_tool_catalog()
    assert "list_accounts" in catalog_before
    assert "get_transactions" in catalog_before

    connectors_service.set_connector_enabled("plaid", True)
    connectors_service.set_connector_enabled("web_search:tavily", True)
    catalog_after = build_tool_catalog()
    assert "list_accounts" in catalog_after
    assert "get_transactions" in catalog_after
    # And every OTHER internal name is byte-identical in/out either way.
    internal_names = {
        n
        for n in catalog_before.split(", ")
        if not n.startswith("(") and tool_connector(n.strip()) is None
    }
    for n in internal_names:
        assert n in catalog_after, n


# ── find_tools: query search ────────────────────────────────────────────────────────


def test_search_does_not_surface_a_disabled_connectors_tool():
    results = _search_tools("sync plaid connector")
    assert "plaid_fetch_transactions" not in results


def test_search_surfaces_the_tool_once_its_connector_is_enabled():
    connectors_service.set_connector_enabled("simplefin", True)
    results = _search_tools("simplefin bank transactions fetch")
    assert "simplefin_fetch_transactions" in results


def test_search_never_surfaces_retired_ibkr_flex_even_when_enabled():
    """IBKR Flex is retired (DISABLED_CONNECTORS) — Lucius never finds its tools."""
    connectors_service.set_connector_enabled("ibkr", True)
    results = _search_tools("interactive brokers flex sync")
    assert "ibkr_fetch_activity" not in results
    assert "ibkr_get_status" not in results


def test_search_still_surfaces_internal_tools_regardless_of_connector_state():
    results = _search_tools("retirement simulation")
    assert "simulate_retirement" in results


def test_search_never_surfaces_yodlee_even_when_enabled():
    """Contrast with test_search_surfaces_the_tool_once_its_connector_is_enabled just
    above: enabling SimpleFIN's store flag unhides it from search; enabling Yodlee's does not
    — DISABLED_CONNECTORS overrides the store."""
    connectors_service.set_connector_enabled("yodlee", True)
    results = _search_tools("yodlee bank sync status")
    assert "yodlee_fetch_transactions" not in results
    assert "yodlee_get_status" not in results


def test_candidate_schemas_omits_hidden_connector_tools():
    names = {s["name"] for s in _candidate_schemas()}
    assert "plaid_fetch_transactions" not in names
    assert "serper_search" not in names
    connectors_service.set_connector_enabled("plaid", True)
    names = {s["name"] for s in _candidate_schemas()}
    assert "plaid_fetch_transactions" in names


# ── find_tools: exact names= load ───────────────────────────────────────────────────


def test_find_tools_names_reports_a_disabled_connector_tool_as_unknown():
    """A gated tool's name is indistinguishable from a genuinely unknown one on this
    path — the whole point of the gate: it isn't offered, so a load-by-name treats it as
    absent, same as a hallucinated name. (Whether it's requested alone — the 'error' shape
    below — or alongside a real name — the 'unknown' list — is find_tools' pre-existing,
    unrelated contract; this pins the ALONE case.)"""

    async def _inner():
        text, change = await find_tools({"names": ["plaid_fetch_transactions"]})
        assert change is None
        data = json.loads(text)
        assert "no such tool" in data.get("error", "")
        assert "plaid_fetch_transactions" in data["error"]

    asyncio.run(_inner())


def test_find_tools_names_reports_a_disabled_connector_tool_among_unknown():
    """Requested ALONGSIDE a real, reachable name: the gated one lands in `unknown`,
    exactly like a genuinely nonexistent tool would."""

    async def _inner():
        text, change = await find_tools(
            {"names": ["plaid_fetch_transactions", "get_rebalance_plan"]}
        )
        assert change is None
        data = json.loads(text)
        assert "plaid_fetch_transactions" in data.get("unknown", [])
        assert "get_rebalance_plan" in [item["name"] for item in data["loaded"]]

    asyncio.run(_inner())


def test_find_tools_names_never_loads_yodlee_even_when_enabled():
    """Contrast with test_find_tools_names_loads_it_once_enabled below: enabling Plaid's
    store flag makes find_tools load it by name; enabling Yodlee's does not — it lands in
    `unknown`, same shape as test_find_tools_names_reports_a_disabled_connector_tool_
    among_unknown above."""
    connectors_service.set_connector_enabled("yodlee", True)

    async def _inner():
        text, change = await find_tools(
            {"names": ["yodlee_fetch_transactions", "get_rebalance_plan"]}
        )
        assert change is None
        data = json.loads(text)
        assert "yodlee_fetch_transactions" in data.get("unknown", [])
        assert "get_rebalance_plan" in [item["name"] for item in data["loaded"]]

    asyncio.run(_inner())


def test_find_tools_names_loads_it_once_enabled():
    connectors_service.set_connector_enabled("plaid", True)

    async def _inner():
        text, change = await find_tools({"names": ["plaid_fetch_transactions"]})
        assert change is None
        data = json.loads(text)
        loaded_names = [item["name"] for item in data["loaded"]]
        assert "plaid_fetch_transactions" in loaded_names
        assert not data.get("unknown")

    asyncio.run(_inner())


def test_find_tools_names_is_unaffected_for_an_internal_tool():
    async def _inner():
        text, change = await find_tools({"names": ["get_rebalance_plan"]})
        assert change is None
        data = json.loads(text)
        loaded_names = [item["name"] for item in data["loaded"]]
        assert "get_rebalance_plan" in loaded_names

    asyncio.run(_inner())


# ── discovery gating never blocks EXECUTION ─────────────────────────────────────────


def test_a_gated_tool_still_dispatches_when_called_directly():
    """Discovery hides the tool from the OFFER/search surface — it never refuses the
    call itself. The unconfigured provider's own handler reports its own status, exactly
    like the bulk get_connector_status tool already does."""
    assert (
        "plaid_fetch_transactions" in hidden_connector_tools()
    )  # sanity: genuinely gated right now

    body, proposed = call("plaid_fetch_transactions")
    assert proposed is None
    assert body["status"] == "not_configured"


def test_a_gated_status_tool_still_reads_when_called_directly():
    assert "plaid_get_status" in hidden_connector_tools()
    body, proposed = call("plaid_get_status")
    assert proposed is None
    assert "plaid" in body["providers"]


# ── the one exception: DISABLED_CONNECTORS never dispatches ────────────────────────
#
# Yodlee (2026-08-14, ADR 2026-08-14-hide-yodlee-enterprise-only-pricing) is the one
# connector where the rule just above — "discovery hides it, dispatch still runs it" —
# does NOT hold. It is withdrawn by policy, not merely unconfigured, so an exact-name
# call is refused outright, even with the connector store's enabled flag forced True.


def test_yodlee_tool_is_refused_at_dispatch_unlike_every_other_gated_connector():
    """The sharpest contrast in this file: Plaid (test_a_gated_tool_still_dispatches_
    when_called_directly above) still executes on an exact-name call while hidden.
    Yodlee does not — registry.dispatch refuses it before its handler runs."""
    connectors_service.set_connector_enabled("yodlee", True)
    assert "yodlee" in connectors_service.enabled_connector_ids()  # sanity: really enabled
    assert "yodlee_get_status" in hidden_connector_tools()  # sanity: still hidden too

    body, proposed = call("yodlee_get_status")
    assert proposed is None
    assert "error" in body
    assert "yodlee" in body["error"].lower()


def test_yodlee_sync_tool_is_refused_at_dispatch_unlike_every_other_gated_connector():
    body, proposed = call("yodlee_fetch_transactions")
    assert proposed is None
    assert "error" in body
    assert "yodlee" in body["error"].lower()


# ── the gap: set_connector_enabled carries no connector= tag, so it never hit the ─────
# DISABLED_CONNECTORS dispatch check above — it has its OWN check in the tool handler
# (backend/ai/tools/connectors.py). Security-review finding, 2026-08-14: a call like
# set_connector_enabled(connector_id="yodlee", enabled=true) would otherwise flip
# YodleeConfigStore.enabled and resume the scheduler's unattended poll job, bypassing
# every guard above entirely (the scheduler calls run_yodlee_sync() directly, never
# through registry.dispatch()).


def test_set_connector_enabled_tool_refuses_to_enable_yodlee_even_when_preconfigured():
    """The exact exploitable scenario the gap was found in: an instance with valid,
    previously-saved Yodlee credentials. Lucius calling set_connector_enabled must still
    be refused — the store's enabled flag must not move at all."""
    from backend.db.yodlee_config_store import YodleeConfigStore

    store = YodleeConfigStore()
    store.update(
        {
            "client_id": "cid_123",
            "secret": "shh",
            "username": "user@example.com",
            "password": "hunter2",
            "enabled": False,
        }
    )
    assert store.is_configured()  # sanity: this is the "already configured" scenario

    body, proposed = call("set_connector_enabled", connector_id="yodlee", enabled=True)
    assert proposed is None
    assert body["ok"] is False
    assert "yodlee" in body["error"].lower()
    # Refused BEFORE the write — not merely refused-with-a-message after the state
    # already flipped. Confirms the check runs ahead of any service/store call.
    assert store.get()["enabled"] is False


def test_set_connector_enabled_tool_refuses_yodlees_read_capability_too():
    """The capability= branch reaches the identical store flip + reschedule for Yodlee
    (its only capability is 'read', which maps onto the same enabled flag) — must be
    refused the same way as the whole-connector toggle above."""
    body, proposed = call(
        "set_connector_enabled", connector_id="yodlee", enabled=True, capability="read"
    )
    assert proposed is None
    assert body["ok"] is False
    assert "yodlee" in body["error"].lower()


def test_set_connector_enabled_tool_still_allows_disabling_yodlee():
    """Disabling can never resume anything — only enabled=True is refused."""
    from backend.db.yodlee_config_store import YodleeConfigStore

    connectors_service.set_connector_enabled("yodlee", True)  # direct service call, not the AI tool
    body, proposed = call("set_connector_enabled", connector_id="yodlee", enabled=False)
    assert proposed is None
    assert body["ok"] is True
    assert YodleeConfigStore().get()["enabled"] is False


def test_set_connector_enabled_tool_is_unaffected_for_an_ordinary_connector():
    """Regression guard: the new check must not widen to any other connector id."""
    body, proposed = call("set_connector_enabled", connector_id="plaid", enabled=True)
    assert proposed is None
    assert body["ok"] is True
    assert body["connector"]["enabled"] is True


def test_set_connector_enabled_tool_refuses_before_the_service_is_ever_touched(monkeypatch):
    """Ordering, verified directly rather than inferred from the result shape: the
    refusal must happen BEFORE services.connectors.set_connector_enabled ever runs — not
    merely produce an error message after the store already moved. (There is no scheduler
    entry point left to check separately — RFC connectors-as-data-sources deleted the
    per-provider ingest poll and every reschedule_* shim; a connector's own fetch tool is
    the only thing that would have been "resumed" here.)"""
    from backend.db.yodlee_config_store import YodleeConfigStore

    def _boom_service(*a, **kw):
        raise AssertionError("services.connectors.set_connector_enabled must not be called")

    monkeypatch.setattr(connectors_service, "set_connector_enabled", _boom_service)

    body, proposed = call("set_connector_enabled", connector_id="yodlee", enabled=True)
    assert proposed is None
    assert body["ok"] is False
    assert "yodlee" in body["error"].lower()
    assert YodleeConfigStore().get()["enabled"] is False


def test_set_connector_enabled_tool_toggles_email_as_a_whole():
    """Email has one on/off, so Pause/Resume is how a real operator (or Lucius, with
    confirmation) switches the whole provider; there is no per-capability toggle to
    re-arm a leg the operator paused."""
    body, proposed = call("set_connector_enabled", connector_id="email", enabled=True)
    assert proposed is None
    assert body["ok"] is True
    assert body["connector"]["enabled"] is True
