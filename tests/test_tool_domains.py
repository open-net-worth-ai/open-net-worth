"""Domain tags on write tools — registry mechanics + the classification sweep.

The sweep is the load-bearing part. Every registered production tool must be EXPLICITLY
classified as one of four things; there is no "unclassified" state a new tool can ship in:

  1. ``mutates=(...)`` on its ``@tool`` decorator — it writes those data domains. This is
     the tag that makes an undecided tool ASK (``registry.is_write_tool``).
  2. ``refreshes=(...)`` — a READ that makes a screen refresh (a sync status, a cache, a
     memoized result) and changes nothing the owner would call a change. Reaches
     ``data_changed`` exactly like ``mutates=`` but does not ask. Because tagging a tool this
     way removes its approval default, the members are a REVIEWED set pinned below.
  3. ``READ_ONLY_TOOLS`` — reviewed, writes nothing persistent.
  4. ``NO_DOMAIN_WRITE_TOOLS`` — it has a real side effect, but no LOCAL data domain a
     frontend view could refetch (outbound-only, sandboxed, or turn-ephemeral).

This replaced a name-prefix sweep that only policed tools whose NAME started with one of
19 write-ish verbs. That let a genuinely-writing tool ship untagged as long as it was
called ``get_``/``list_``/``propose_`` — which is exactly how ``get_watchlist``,
``get_live_prices``, ``diff_statement``, ``propose_lot_dispose`` and friends went years
without a tag. Classification is now by REVIEW, not by naming luck.

Tagging a ``propose_*`` tool was always a smell rather than a resolution: a preview that
writes leaves debris behind when the user says no. **2026-07-19 (harness reliability
program): the 13 ``propose_*``/``apply_*`` pairs that used to split "pure preview" from
"the write" are MERGED** into one confirm-token'd tool each (mirroring
``add_transactions``/``update_transactions`` — ``docs/adr/
2026-07-19-ledger-confirm-gate-one-tool-stored-args.md``): the surviving ``apply_*`` name
carries the real ``mutates=(...)`` tag (a card-capable/attended call previews and stages
without writing; an unattended/direct call writes immediately — see
``registry.should_preview_write``), and the former ``propose_*`` name is now an ALIAS of
it, so it resolves to the SAME tag — there is no longer a separately-classified
non-writing preview tool for these 13 operations, and ``READ_ONLY_TOOLS`` no longer lists
them (an alias is not a live registered name — see ``_production_tool_names``).

Out of scope for ``mutates=`` (deliberately, so the tag stays a data-refresh signal):
telemetry and conversation bookkeeping — ``cost_log.json`` (written by EVERY tool that
touches the LLM), the tool-discovery log, chat session meta. None of them back a view.
"""

import re

from backend.ai.tools._write_classification import NO_DOMAIN_WRITE_TOOLS, READ_ONLY_TOOLS
from backend.ai.tools.registry import (
    is_write_tool,
    iter_registered_tools,
    mutated_domains,
    mutating_tool_domains,
    refreshing_tool_domains,
    written_domains,
)


def test_mutated_domains_for_tagged_tool():
    assert mutated_domains("add_transactions") == ("ledger",)


def test_mutated_domains_unknown_or_read_tool_is_empty():
    assert mutated_domains("get_portfolio") == ()
    assert mutated_domains("no_such_tool") == ()


def test_mutating_tool_domains_map_shape():
    m = mutating_tool_domains()
    # Canonical names only — aliases are resolved by mutated_domains(), never listed here.
    assert m["add_transactions"] == ("ledger",)
    assert "get_portfolio" not in m
    for name, doms in m.items():
        assert isinstance(doms, tuple) and doms, name
        for d in doms:
            assert re.match(r"^([a-z_]{1,24}|\*)$", d), (name, d)


def _production_tool_names() -> list[str]:
    # Scope to PRODUCTION tool modules: the registry is a process-global that other suites
    # may (legitimately) register runtime/test tools into, which made an unscoped sweep
    # order-dependent. Production coverage is what this polices.
    return [
        name
        for name, _desc, module in iter_registered_tools()
        if module.startswith("backend.ai.tools")
    ]


def test_every_tool_is_explicitly_classified():
    """No tool ships unclassified — regardless of what it is NAMED."""
    tagged = set(mutating_tool_domains()) | set(refreshing_tool_domains())
    unclassified = sorted(
        name
        for name in _production_tool_names()
        if name not in tagged and name not in READ_ONLY_TOOLS and name not in NO_DOMAIN_WRITE_TOOLS
    )
    assert unclassified == [], (
        "these tools declare neither mutates=(...) / refreshes=(...) nor a reviewed read-only "
        f"marker: {unclassified}. Trace the handler through its services/db calls, then either "
        "add mutates=(domains) (it changes something), refreshes=(domains) (a read that only "
        "makes a screen refresh) or add it to READ_ONLY_TOOLS / NO_DOMAIN_WRITE_TOOLS."
    )


def test_classification_is_unambiguous():
    """A tool is in exactly one bucket — a tag plus a read-only marker is a contradiction."""
    writes = set(mutating_tool_domains())
    refreshes = set(refreshing_tool_domains())
    assert not (writes & refreshes), sorted(writes & refreshes)
    for tagged in (writes, refreshes):
        assert not (tagged & READ_ONLY_TOOLS), sorted(tagged & READ_ONLY_TOOLS)
        assert not (tagged & set(NO_DOMAIN_WRITE_TOOLS)), sorted(
            tagged & set(NO_DOMAIN_WRITE_TOOLS)
        )
    assert not (READ_ONLY_TOOLS & set(NO_DOMAIN_WRITE_TOOLS))


def test_a_tool_cannot_declare_both_mutates_and_refreshes():
    """A real change always wins — a tool that is both would be a write that claims to be a
    read, so registration refuses it outright."""
    import pytest

    from backend.ai.tools import registry as reg

    name = "zz_declares_both_tags"
    try:
        with pytest.raises(ValueError, match="declares both mutates= and refreshes="):

            @reg.tool(
                name=name,
                description="x",
                input_schema={"type": "object"},
                mutates=("ledger",),
                refreshes=("config",),
            )
            async def _both(_input_data):  # pragma: no cover - never dispatched
                return "{}", None

        assert name not in reg._REGISTRY
    finally:
        reg._REGISTRY.pop(name, None)
        reg._validator_for.cache_clear()


def test_exempt_sets_have_no_stale_entries():
    """A removed/renamed tool must not linger as a silent exemption."""
    live = set(_production_tool_names())
    assert not (READ_ONLY_TOOLS - live), sorted(READ_ONLY_TOOLS - live)
    assert not (set(NO_DOMAIN_WRITE_TOOLS) - live), sorted(set(NO_DOMAIN_WRITE_TOOLS) - live)


def test_no_domain_write_exemptions_carry_a_reason():
    for name, reason in NO_DOMAIN_WRITE_TOOLS.items():
        assert reason.strip(), name


# Regression pins for the read-SHAPED tools that genuinely write. These are the exact
# cases the old prefix sweep could never catch — if someone "cleans up" one of these tags
# because the name looks like a read, this fails.
def test_get_live_prices_is_a_read_and_records_no_history():
    """It used to append to price_history on every call — see READ_ONLY_TOOLS above."""
    import inspect

    from backend.services.prices import fetch_live

    assert mutated_domains("get_live_prices") == ()
    assert inspect.signature(fetch_live).parameters["record_history"].default is True
    src = inspect.getsource(__import__("backend.ai.tools.prices", fromlist=["x"]).get_live_prices)
    assert "record_history=False" in src, "the tool must opt OUT of recording"


def test_get_watchlist_is_a_read_and_a_job_owns_the_reconciliation():
    """It used to call `sync_from_holdings` on every read — see READ_ONLY_TOOLS above.

    Reconciling is now a scheduled job. Pinned here rather than left to a comment because
    the two facts have to move together: if the job is ever removed and nothing replaces it,
    the auto-managed My-holdings watchlist silently stops being reconciled for every reader
    that is not an open screen — including the model.
    """
    import inspect

    import backend.scheduler as scheduler

    assert mutated_domains("get_watchlist") == ()
    src = inspect.getsource(__import__("backend.ai.tools.watchlist", fromlist=["x"]).get_watchlist)
    # The CALL, not the word: `getsource` includes the @tool decorator, whose comment
    # explains where the reconciliation went.
    assert "store.sync_from_holdings(" not in src, "a read must not reconcile"
    assert "sync_from_holdings" in inspect.getsource(scheduler._run_watchlist_sync)
    assert "watchlist_sync" in inspect.getsource(scheduler.start), "the job must be registered"


def test_list_match_reviews_filters_zombies_without_writing():
    """The sweep's two halves are separable and only one is a mutation.

    Filtering still has to happen — a review pointing at a deleted transaction is not merely
    stale, it is something a reader could act on (merging against a row that is already
    gone). Stamping the store is what a `list_` tool has no business doing.
    """
    import inspect

    from backend.services import match_review

    assert mutated_domains("list_match_reviews") == ()
    src = inspect.getsource(
        __import__("backend.ai.tools.matching", fromlist=["x"]).list_match_reviews
    )
    assert "persist_sweep=False" in src, "the tool must opt OUT of stamping"

    # The filter half still runs with persist off: a zombie is dropped from the result and
    # counted, and `set_status` is never reached.
    calls: list = []
    live, count = match_review._sweep_zombies(
        [
            {"id": "mr_live", "incoming_id": "a", "canonical_id": "b"},
            {"id": "mr_zombie", "incoming_id": "gone", "canonical_id": "b"},
        ],
        {"a": {"id": "a"}, "b": {"id": "b"}},
        persist=False,
    )
    assert [i["id"] for i in live] == ["mr_live"], "the zombie must still be filtered out"
    assert count == 1
    assert calls == []


# The reads that make a screen refresh but change nothing the owner would call a change —
# each one traced through its handler (2026-10-11). Tagging a tool ``refreshes=`` instead of
# ``mutates=`` is what stops it asking by default, so this set is a REVIEW GATE: a new member
# must be added here deliberately, with the handler traced to bookkeeping only.
REVIEWED_REFRESHING_READS: dict[str, tuple[str, ...]] = {
    # a body-less / completed provenance snapshot per message + the mailbox's last-synced stamp
    "email_search_messages": ("config",),
    "email_get_messages": ("config",),
    # the computed result remembered on the statement record / import batch
    "diff_statement": ("uploads",),
    "verify_statement_balance": ("uploads",),
    # the machine-made Intel feed and the auto-managed holdings watchlist (== "Scan now")
    "scan_intel": ("intel", "watchlist"),
    # a raw-evidence snapshot per row + the connector's own last-synced stamp
    "simplefin_fetch_transactions": ("config",),
    "plaid_fetch_transactions": ("config",),
    "lunch_money_fetch_transactions": ("config",),
    "lunch_flow_fetch_transactions": ("config",),
    "ynab_fetch_transactions": ("config",),
    "enable_banking_fetch_transactions": ("config",),
    "yodlee_fetch_transactions": ("config",),
    "alpaca_fetch_positions": ("config",),
    "ibkr_fetch_activity": ("config",),
}
# Bookkeeping lives in these domains only: connector status/snapshots, the statement/batch
# record's memo, the derived Intel feed and the auto-managed watchlist.
BOOKKEEPING_DOMAINS = frozenset({"config", "uploads", "intel", "watchlist"})


def test_refreshing_reads_are_exactly_the_reviewed_set():
    assert refreshing_tool_domains() == REVIEWED_REFRESHING_READS


def test_a_refreshing_read_writes_only_bookkeeping_domains():
    for name, domains in refreshing_tool_domains().items():
        assert set(domains) <= BOOKKEEPING_DOMAINS, (name, domains)


def test_a_refreshing_read_still_refreshes_screens_but_is_not_a_write():
    """The freshness path is unchanged — ``mutated_domains`` (what the ``data_changed``
    broadcast reads) still reports the refresh — while the approval default, which keys on
    ``is_write_tool``, does not see a write."""
    for name, domains in REVIEWED_REFRESHING_READS.items():
        assert mutated_domains(name) == domains, name
        assert written_domains(name) == (), name
        assert is_write_tool(name) is False, name


def test_real_writers_that_look_like_reads_stay_tagged_mutates():
    assert mutated_domains("mark_notifications_read") == ("notification_log",)
    assert mutated_domains("telegram_send_message") == ("notification_log",)
    assert mutated_domains("webhook_send_message") == ("notification_log",)
    assert mutated_domains("resolve_instruments") == ("portfolio",)
    for name in (
        "mark_notifications_read",
        "telegram_send_message",
        "webhook_send_message",
        "resolve_instruments",
        "add_transactions",
        "set_connector_enabled",
    ):
        assert is_write_tool(name) is True, name
        assert written_domains(name) == mutated_domains(name), name


def test_plain_reads_are_neither_a_write_nor_a_refresh():
    for name in ("get_portfolio", "duckduckgo_search", "plaid_get_status", "no_such_tool"):
        assert is_write_tool(name) is False, name
        assert mutated_domains(name) == (), name


def test_the_merged_write_tools_carry_their_domain_tag():
    """The 2026-07-19 propose/apply collapse left ONE tool per write, and it must be tagged
    for what it CAN do. The former ``propose_*`` names are retired outright (ADR
    2026-08-25-tool-names-are-final), so there is nothing to compare them against any more —
    the live tool's tag is the whole answer.

    Static tags describe what a tool CAN do, not what a given call did — whether THIS
    particular call actually writes is decided dynamically by
    `registry.should_preview_write()` (card-capable/attended → preview only;
    unattended/direct → writes), which is exercised behaviorally in
    tests/test_propose_does_not_write.py, not by this static-tag sweep."""
    assert mutated_domains("update_positions") == ("portfolio",)
    assert mutated_domains("dispose_lots") == ("portfolio",)
    for retired in ("propose_position_update", "propose_lot_dispose"):
        assert mutated_domains(retired) == (), f"{retired} is retired — it must tag nothing"


# The frontend's deleted MUTATING_TOOLS set (useChat.ts) — pin that every member still
# triggers the RouterShell core refresh via domains (superset allowed, never a loss).
OLD_FRONTEND_MUTATING_TOOLS = {
    "update_positions",
    "add_positions",
    "remove_positions",
    "add_transactions",
    "add_transactions",
    "update_transactions",
    "update_transactions",
    "create_payees",
    "rename_payees",
    "merge_payees",
    "delete_payees",
    "delete_transactions",
    "delete_transactions_where",
    "create_account",
    "reconcile_accounts",
    "restore_checkpoint",
    "update_action_status",
}
ROUTERSHELL_CORE_DOMAINS = {"portfolio", "ledger", "accounts", "payees", "categories", "budget"}


def test_old_frontend_mutating_set_still_covered():
    for name in OLD_FRONTEND_MUTATING_TOOLS:
        doms = set(mutated_domains(name))
        assert doms, f"{name} lost its refresh trigger"
        assert doms & ROUTERSHELL_CORE_DOMAINS or "*" in doms, (name, doms)


# ── The seed rule reads DECLARATIONS: workspace domains and external_effect ─────────────────
#
# ADR 2026-10-11-permissions-are-one-value-per-tool: a write whose domains are ALL Lucius's own
# workspace is seeded Always allow; any other write, and anything declaring ``external_effect=``,
# is seeded Ask; a read runs. The workspace is declared once per DOMAIN
# (``ai._confirm_gate.WORKSPACE_DOMAINS``), never as a list of tools.

WORKSPACE_DOMAINS_EXPECTED = {"memory", "skills", "artifacts", "notification_log"}


def test_the_workspace_domains_are_declared_once_and_are_real_domains():
    from backend.ai._confirm_gate import WORKSPACE_DOMAINS

    assert WORKSPACE_DOMAINS == WORKSPACE_DOMAINS_EXPECTED
    used = {d for doms in mutating_tool_domains().values() for d in doms}
    assert WORKSPACE_DOMAINS <= used, sorted(WORKSPACE_DOMAINS - used)
    # No book domain sneaks in: the owner's ledger, accounts, plan and settings still ask.
    assert not (WORKSPACE_DOMAINS & {"ledger", "accounts", "portfolio", "budget", "config"})


def test_every_write_is_seeded_by_its_declarations():
    """The whole registry, no names: Ask unless every written domain is workspace."""
    from backend.ai._confirm_gate import WORKSPACE_DOMAINS
    from backend.ai.tools import registry as reg
    from backend.services import tool_permissions as perms

    seen_allow = set()
    for name, domains in mutating_tool_domains().items():
        stays_home = set(domains) <= WORKSPACE_DOMAINS and not reg.has_external_effect(name)
        expected = "always_allow" if stays_home else "needs_approval"
        assert perms.seed_level(name) == expected, (name, domains)
        if stays_home:
            seen_allow.add(name)
    assert seen_allow == {
        "remember_fact",
        "forget_fact",
        "create_skill",
        "update_skill",
        "delete_skill",
        "save_code_as_skill",
        "create_artifact",
        "mark_notifications_read",
    }


def test_a_write_that_mixes_a_workspace_domain_with_a_book_domain_still_asks():
    from backend.ai import _confirm_gate as policy

    assert policy.needs_approval("t", writes=True, domains=("memory",)) is False
    assert policy.needs_approval("t", writes=True, domains=("memory", "skills")) is False
    assert policy.needs_approval("t", writes=True, domains=("memory", "ledger")) is True
    assert policy.needs_approval("t", writes=True, domains=("skills", "*")) is True
    # No domain information is never read as "workspace".
    assert policy.needs_approval("t", writes=True) is True


def test_external_effect_asks_whatever_domains_a_tool_writes():
    """A Telegram send writes ``notification_log`` (a workspace domain) yet reaches outside."""
    from backend.ai import _confirm_gate as policy
    from backend.services import tool_permissions as perms

    assert policy.needs_approval(
        "t", writes=True, domains=("notification_log",), external_effect=True
    )
    for name in ("telegram_send_message", "webhook_send_message"):
        assert perms.seed_level(name) == "needs_approval", name
    assert perms.seed_level("mark_notifications_read") == "always_allow"


def test_external_effect_is_declared_on_the_tools_that_act_outside_the_owners_data():
    from backend.ai.tools.registry import has_external_effect

    declared = {name for name in _production_tool_names() if has_external_effect(name)}
    assert declared == {
        "email_send_message",
        "telegram_send_message",
        "webhook_send_message",
        "run_script",
        "run_code",
    }


def test_save_code_as_skill_is_tagged_as_the_write_it_is():
    """It creates a skill record and its bundle — it was filed as READ_ONLY by mistake."""
    assert mutating_tool_domains()["save_code_as_skill"] == ("skills",)
    assert "save_code_as_skill" not in READ_ONLY_TOOLS
