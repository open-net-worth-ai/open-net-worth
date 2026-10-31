"""``backend.ai._confirm_gate`` — surface capability, the read-shaped exemption, the
unattended hardline, and the best-effort surfacing helpers.

These pin the PURE policy math in isolation; ``ai/tools/_confirm_gate.py`` is the dispatch
call site that consumes it.

The per-agent write envelope this file also covered is GONE (Agents retired 2026-08-02) —
see the note where its tests were, and tests/test_agents_retired.py for what replaced them.
The surfacing helpers below outlived it: they still take an ``agent_id`` argument, now
always None on the paths that call them, and their messages still say "envelope" in
fixtures written when one existed.
"""

from __future__ import annotations

import pytest

from backend.ai import _confirm_gate as policy

# ── can_render_card ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("web", True),
        ("connector", True),
        ("connector:telegram", True),
        ("connector:slack", True),
        ("connector:discord", True),
        ("connector:whatsapp", True),
        ("scheduled", False),
        (None, False),
        ("", False),
    ],
)
def test_can_render_card(surface, expected):
    assert policy.can_render_card(surface) is expected


# ── read-shaped exemption — a bookkeeping write is not a user-meaningful mutation ────────
#
# Owner incident 2026-07-21: "why is fetch_emails required permissions?" — reading your own
# inbox is a read. It carries mutates=("config",) for FRESHNESS (the cursor moved), and the
# gate reused that tag as the permission criterion, over-triggering on a whole CLASS of
# read-shaped tools. These assert the exemption catches the class and nothing else.


def test_read_shaped_tools_do_not_need_approval_even_though_they_write():
    # writes=True (they carry mutates=), yet they must NOT ask — their write is bookkeeping.
    for tool in policy.GATE_EXEMPT_READ_SHAPED:
        assert policy.needs_approval(tool, writes=True) is False, tool


# Exempt tools that write NOTHING (no `mutates=` tag, listed in READ_ONLY_TOOLS): the bank
# account lists. They are exempt for permission only — nothing to refresh afterwards.
_NEVER_WRITE = frozenset(
    f"{provider}_list_accounts"
    for provider in (
        "simplefin",
        "plaid",
        "lunch_money",
        "lunch_flow",
        "ynab",
        "enable_banking",
        "yodlee",
    )
)


def test_the_exempt_class_is_the_expected_curated_set_not_a_heuristic():
    # A name heuristic would sweep in propose_position_update (find-or-CREATEs an account).
    # This pins the reviewed membership so an accidental add/removal is a red diff, not silent.
    # get_live_prices/get_watchlist/list_match_reviews were here too until 2026-08-23, when
    # each was changed to pass record_history=False/persist_sweep=False and dropped its own
    # `mutates=` tag entirely (see their tool docstrings) — a tool that never writes needs no
    # write exemption, so membership here shrank to match (see
    # test_tool_domains.py::test_read_shaped_writers_stay_tagged for the current mutates= set).
    # 2026-09-06: the nine bank/broker fetch tools (RFC connectors-as-data-sources) joined —
    # same "reading your own inbox is a read" argument as email above; design intent
    # confirmed with the owner (otherwise every template Automation run parks on an
    # approval card for a fetch that books nothing itself).
    # 2026-10-29: the seven `<provider>_list_accounts` tools joined (b4f30b3) — a narrower read
    # than the fetch that writes nothing at all, so unlike every member above they carry no
    # `mutates=` tag; the exemption exists only so an "ask" connector default does not park
    # an unattended sync run (see _NEVER_WRITE above).
    assert policy.GATE_EXEMPT_READ_SHAPED == frozenset(
        {
            "email_search_messages",
            "email_get_messages",
            "diff_statement",
            "verify_statement_balance",
            "scan_intel",
            "simplefin_fetch_transactions",
            "plaid_fetch_transactions",
            "lunch_money_fetch_transactions",
            "lunch_flow_fetch_transactions",
            "ynab_fetch_transactions",
            "enable_banking_fetch_transactions",
            "yodlee_fetch_transactions",
            "alpaca_fetch_positions",
            "ibkr_fetch_activity",
        }
        | _NEVER_WRITE
    )


def test_a_genuine_write_still_needs_approval():
    # The exemption must not leak: a real mutation and an external effect still confirm.
    assert policy.needs_approval("update_model_config", writes=True) is True
    assert policy.needs_approval("email_send_message", writes=False) is True


def test_end_to_end_read_shaped_tools_are_not_gated_but_still_carry_mutates():
    """Through the live gate (is_gated_tool) with GATE_ALL_WRITES armed: the exempt tools do
    NOT gate, yet each still carries mutates= so its views keep refreshing — the exemption is
    permission-only, never a freshness regression. A genuine write still gates."""
    from backend.ai.tools import registry
    from backend.ai.tools._confirm_gate import is_gated_tool

    for tool in policy.GATE_EXEMPT_READ_SHAPED:
        assert is_gated_tool(tool) is False, tool
        if tool in _NEVER_WRITE:
            assert not registry.mutated_domains(tool), (
                f"{tool} is read-only; it must not claim a write"
            )
        else:
            assert registry.mutated_domains(tool), f"{tool} lost its freshness tag"
    assert is_gated_tool("update_model_config") is True
    assert is_gated_tool("create_account") is True


# ── hardline blocklist — no envelope, no config, ever ────────────────────────────────────


def test_hardline_blocks_the_model_chose_the_set_tools():
    assert policy.is_hardline_blocked("delete_transactions_where") is True
    assert policy.is_hardline_blocked("apply_payee_rules") is True


def test_hardline_does_not_block_routine_ledger_writes():
    assert policy.is_hardline_blocked("add_transactions") is False
    assert policy.is_hardline_blocked("update_transaction") is False
    assert policy.is_hardline_blocked("apply_payee_rules_to_payee") is False  # scoped, not global


# ── envelope evaluation ─────────────────────────────────────────────────────────────────
#
# REMOVED with AgentEnvelope itself (Agents retired 2026-08-02, owner decision). Seven tests
# pinned its per-agent unattended-write bound: allowed with no envelope configured (an
# UNTAKEN opt-in, not a declined one), allowed inside every dimension, refused outside the
# account set, refused over the amount ceiling, absolute-value on that ceiling, `None` vs
# empty-set as "unconstrained" vs "nothing permitted", and the refusal shape.
#
# The bound is GONE AND HAS NO SUCCESSOR — that is the decision, not an oversight, and it
# is the one thing here a reader should not have to infer. What survives is pinned
# next-door: tests/test_agents_retired.py asserts the hardline tier still refuses an
# unattended `delete_transactions_where`, and asserts — deliberately, so the removal stays
# visible rather than being discovered later — that an ordinary unattended write of
# 1,000,000 now proceeds with no amount, kind or account bound at all.

# ── surfacing helpers — best-effort, never raise, never block ───────────────────────────


@pytest.fixture
def signals(db_isolated):
    """A freshly-migrated, isolated NotificationLogStore so these tests never touch the live table."""


@pytest.mark.asyncio
async def test_write_inside_envelope_surfaces_a_signal_and_notifies(signals, monkeypatch):
    from backend.db import NotificationLogStore

    calls = {}

    async def _fake_notify(message, channels=None, title=None):
        calls["message"] = message
        return {"in_app": {"sent": 1}}

    monkeypatch.setattr("backend.services.notify.notify", _fake_notify)

    await policy.surface_unattended_write(
        agent_id="email-transaction-logger",
        tool_name="add_transactions",
        summary="Booked SGD 12.40 · Groceries · from Checking",
        undo_hint="Undo: delete transaction txn_abc123",
    )

    rows = NotificationLogStore().all()
    assert len(rows) == 1
    assert rows[0]["type"] == "review"
    assert "Groceries" in rows[0]["summary"]
    assert "Undo:" in rows[0]["summary"]
    assert "email-transaction-logger" in calls["message"]


@pytest.mark.asyncio
async def test_refused_write_surfaces_what_and_why(signals, monkeypatch):
    from backend.db import NotificationLogStore

    calls = {}

    async def _fake_notify(message, channels=None, title=None):
        calls["message"] = message
        return {"in_app": {"sent": 1}}

    monkeypatch.setattr("backend.services.notify.notify", _fake_notify)

    await policy.surface_unattended_refusal(
        agent_id="email-transaction-logger",
        tool_name="add_transactions",
        reason="account 'acc_savings' is outside the agent's envelope",
        attempted="book SGD 900.00 to Savings",
    )

    rows = NotificationLogStore().all()
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"
    assert "outside the agent's envelope" in rows[0]["summary"]
    assert "900.00" in rows[0]["summary"]
    assert "outside the agent's envelope" in calls["message"]


@pytest.mark.asyncio
async def test_surfacing_never_raises_even_when_notify_is_broken(signals, monkeypatch):
    """A headless write must never fail or block on a surfacing failure — it already
    happened (or was refused); reporting it is best-effort."""

    async def _boom(message, channels=None):
        raise RuntimeError("channel down")

    monkeypatch.setattr("backend.services.notify.notify", _boom)

    # Neither call raises despite notify() blowing up.
    await policy.surface_unattended_write(
        agent_id="agent-1", tool_name="add_transactions", summary="Booked something"
    )
    await policy.surface_unattended_refusal(
        agent_id="agent-1", tool_name="add_transactions", reason="nope", attempted="something"
    )


# ── notify_unattended_write_enabled toggle (Settings > Permissions) ─────────────────────


@pytest.mark.asyncio
async def test_write_surfacing_suppressed_when_notify_flag_is_off(signals, monkeypatch, tmp_path):
    """With the instance flag OFF, a routine unattended write produces NEITHER a Signals row
    NOR an outbound notify() call — only the agent's own end-of-run summary remains, which is
    a separate call this function never makes."""
    import backend.db.app_config_store as store_mod
    from backend.db import NotificationLogStore
    from backend.db.app_config_store import AppConfigStore

    # app_config is DB-backed now (Wave 5D2a, JSON->DB) — conftest's autouse Tier-2 table
    # clear already isolates it per test, no monkeypatch needed.
    AppConfigStore().set_flag(store_mod.NOTIFY_UNATTENDED_WRITE_FLAG, False)

    notify_calls = []

    async def _fake_notify(message, channels=None, title=None):
        notify_calls.append(message)
        return {"in_app": {"sent": 1}}

    monkeypatch.setattr("backend.services.notify.notify", _fake_notify)

    await policy.surface_unattended_write(
        agent_id="transaction-trade-logger",
        tool_name="add_transactions",
        summary="Booked SGD 3.95 · Groceries · Corner Deli",
    )

    assert NotificationLogStore().all() == []
    assert notify_calls == []


@pytest.mark.asyncio
async def test_write_surfacing_still_fires_when_notify_flag_is_on_by_default(signals, monkeypatch):
    """Default (no flag set at all, fresh instance) is ON — behavior is unchanged from
    before this flag existed."""
    from backend.db import NotificationLogStore

    notify_calls = []

    async def _fake_notify(message, channels=None, title=None):
        notify_calls.append(message)
        return {"in_app": {"sent": 1}}

    monkeypatch.setattr("backend.services.notify.notify", _fake_notify)

    await policy.surface_unattended_write(
        agent_id="transaction-trade-logger",
        tool_name="add_transactions",
        summary="Booked SGD 3.95 · Groceries · Corner Deli",
    )

    assert len(NotificationLogStore().all()) == 1
    assert len(notify_calls) == 1


@pytest.mark.asyncio
async def test_refusal_surfacing_unaffected_by_notify_flag(signals, monkeypatch, tmp_path):
    """surface_unattended_refusal always notifies regardless of the toggle's state — a
    misbehaving/misconfigured agent's refused write is a different signal than a routine
    successful write, and must never be silenced by this flag."""
    import backend.db.app_config_store as store_mod
    from backend.db import NotificationLogStore
    from backend.db.app_config_store import AppConfigStore

    # app_config is DB-backed now (Wave 5D2a, JSON->DB) — conftest's autouse Tier-2 table
    # clear already isolates it per test, no monkeypatch needed.
    AppConfigStore().set_flag(store_mod.NOTIFY_UNATTENDED_WRITE_FLAG, False)

    notify_calls = []

    async def _fake_notify(message, channels=None, title=None):
        notify_calls.append(message)
        return {"in_app": {"sent": 1}}

    monkeypatch.setattr("backend.services.notify.notify", _fake_notify)

    await policy.surface_unattended_refusal(
        agent_id="transaction-trade-logger",
        tool_name="add_transactions",
        reason="account 'acc_savings' is outside the agent's envelope",
        attempted="book SGD 900.00 to Savings",
    )

    assert len(NotificationLogStore().all()) == 1
    assert len(notify_calls) == 1


# ── the tool-result shapes an integrating caller returns verbatim ───────────────────────


def test_hardline_refusal_result_shape():
    import json

    text, proposed = policy.hardline_refusal_result("delete_transactions_where")
    assert proposed is None
    payload = json.loads(text)
    assert payload["ok"] is False
    assert "never run unattended" in payload["error"]
