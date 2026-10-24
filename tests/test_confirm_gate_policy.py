"""``backend.ai._confirm_gate`` — surface capability, the approval default, and the
best-effort surfacing helpers.

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


# ── the default follows what a tool does — there is no exempt list ───────────────────────
#
# Owner incident 2026-07-21: "why is fetch_emails required permissions?" — reading your own
# inbox is a read. A read that only leaves a snapshot or a last-synced stamp behind declares
# `refreshes=` (not `mutates=`), so it is not a write and runs by default; nothing is
# hand-listed here any more. A tool that really changes something, or sends text out, asks.


def test_a_refreshing_read_does_not_ask_by_default():
    from backend.ai.tools import registry
    from backend.ai.tools._confirm_gate import is_gated_tool

    for tool in registry.refreshing_tool_domains():
        assert registry.is_write_tool(tool) is False, tool
        assert policy.needs_approval(tool, writes=registry.is_write_tool(tool)) is False, tool
        assert is_gated_tool(tool) is False, tool
        # ...yet its views still refresh: the freshness path is untouched.
        assert registry.mutated_domains(tool), f"{tool} lost its freshness tag"


def test_the_policy_module_has_no_name_list_of_exempt_tools():
    import backend.services.tool_permissions as perms

    assert not hasattr(policy, "GATE_EXEMPT_READ_SHAPED")
    assert not hasattr(perms, "GATE_EXEMPT_READ_SHAPED")


def test_a_genuine_write_still_needs_approval():
    # A real mutation and an external effect still confirm.
    assert policy.needs_approval("update_model_config", writes=True) is True
    assert policy.needs_approval("email_send_message", writes=False, external_effect=True) is True


def test_a_plain_read_does_not_need_approval():
    assert policy.needs_approval("get_net_worth", writes=False) is False


def test_end_to_end_defaults_are_the_same_for_core_and_connector_tools():
    """Through the live gate (is_gated_tool) reads — core or a
    connector's own — do not gate; a real write or a send, core or a connector's own, does."""
    from backend.ai.tools._confirm_gate import is_gated_tool

    for read in (
        "get_net_worth",
        "duckduckgo_search",
        "plaid_get_status",
        "coingecko_get_crypto_prices",
        "plaid_fetch_transactions",
        "email_search_messages",
    ):
        assert is_gated_tool(read) is False, read
    for write in (
        "update_model_config",
        "create_account",
        "telegram_send_message",
        "webhook_send_message",
        "email_send_message",
        "set_connector_enabled",
    ):
        assert is_gated_tool(write) is True, write


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
# next-door: tests/test_agents_retired.py asserts — deliberately, so the removal stays
# visible rather than being discovered later — that the unattended-write guard never
# refuses anything.

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
