"""The run card's Context section, built from what the run actually touched.

It has always been designed to show connectors, accounts read, skills and remembered
facts, and has always rendered "No context recorded" — the only thing stored under
``context`` was the trigger and its payload, which for a manual trigger is nothing at all.
Nobody needs to declare any of it: the run's own tool calls already say it.
"""

from __future__ import annotations

import pytest

from backend.ai import run_context


@pytest.fixture(autouse=True)
def _scope():
    token = run_context.begin()
    yield
    run_context.reset(token)


@pytest.fixture
def accounts(monkeypatch):
    class _Store:
        def query_accounts(self):
            return [
                {"id": "acc_1", "name": "Meridian Bank Multiplier (SGD)"},
                {"id": "acc_2", "name": "Meridian Bank Savings"},
            ]

    monkeypatch.setattr("backend.db.account_store.AccountStore", lambda: _Store())


def test_a_telegram_send_message_records_the_channel_it_used():
    run_context.observe("telegram_send_message", {"message": "hi"})

    assert run_context.collected()["connectors"] == ["telegram"]


def test_a_webhook_send_message_records_the_channel_it_used():
    run_context.observe("webhook_send_message", {"message": "hi"})

    assert run_context.collected()["connectors"] == ["webhook"]


def test_reading_mail_records_the_email_connector():
    run_context.observe("email_search_messages", {})

    assert run_context.collected()["connectors"] == ["email"]


def test_hydrating_mail_records_the_email_connector():
    run_context.observe("email_get_messages", {})

    assert run_context.collected()["connectors"] == ["email"]


def test_a_provider_fetch_tool_records_its_connector_by_name_not_by_arg(monkeypatch):
    """S-F10: retired connector_sync took a `connector`/`connector_id` arg; its nine
    fetch-tool replacements (RFC connectors-as-data-sources) take no such field at all --
    the provider is implicit in the tool NAME, read off the registry's own connector= tag."""
    monkeypatch.setattr(
        "backend.ai.tools.registry.tool_connector",
        lambda name: "plaid" if name == "plaid_fetch_transactions" else None,
    )
    run_context.observe("plaid_fetch_transactions", {"since": "2026-01-01"})

    assert run_context.collected()["connectors"] == ["plaid"]


def test_a_broker_fetch_tool_records_its_connector_too(monkeypatch):
    monkeypatch.setattr(
        "backend.ai.tools.registry.tool_connector",
        lambda name: "ibkr" if name == "ibkr_fetch_activity" else None,
    )
    run_context.observe("ibkr_fetch_activity", {})

    assert run_context.collected()["connectors"] == ["ibkr"]


def test_retired_connector_sync_no_longer_recorded():
    """connector_sync is retired outright (no alias) -- a stray call under the old name
    (an already-in-flight/stale transcript, never a fresh one) must not crash or silently
    resolve to something; it simply records nothing."""
    run_context.observe("connector_sync", {"connector": "plaid"})

    assert run_context.collected() == {}


def test_an_account_argument_is_recorded_by_NAME_not_id(accounts):
    run_context.observe("get_transactions", {"account_id": "acc_1"})

    assert run_context.collected()["accounts"] == ["Meridian Bank Multiplier (SGD)"]


def test_an_unresolvable_account_is_dropped_rather_than_shown_raw(accounts):
    """ "acc_9f3b…" under a heading reading "Accounts it read" is worse than a shorter list."""
    run_context.observe("get_transactions", {"account_id": "acc_gone"})

    assert "accounts" not in run_context.collected()


def test_both_legs_of_a_transfer_are_recorded(accounts):
    run_context.observe("add_transactions", {"from_account": "acc_1", "to_account": "acc_2"})

    assert run_context.collected()["accounts"] == [
        "Meridian Bank Multiplier (SGD)",
        "Meridian Bank Savings",
    ]


def test_a_list_of_accounts_is_recorded(accounts):
    run_context.observe("get_transactions", {"account_ids": ["acc_2", "acc_1"]})

    assert run_context.collected()["accounts"] == [
        "Meridian Bank Savings",
        "Meridian Bank Multiplier (SGD)",
    ]


def test_a_skill_run_is_recorded():
    run_context.observe("activate_skill", {"skill": "log-transactions"})

    assert run_context.collected()["skills"] == ["log-transactions"]


def test_a_remembered_fact_is_recorded():
    run_context.observe("remember_fact", {"fact": "Kopitiam Central → Dining"})

    assert run_context.collected()["remembered"] == ["Kopitiam Central → Dining"]


def test_a_long_fact_is_trimmed():
    run_context.observe("remember_fact", {"fact": "x" * 400})

    assert len(run_context.collected()["remembered"][0]) == 120


def test_the_same_thing_twice_is_recorded_once(accounts):
    run_context.observe("get_transactions", {"account_id": "acc_1"})
    run_context.observe("get_transactions", {"account_id": "acc_1"})

    assert run_context.collected()["accounts"] == ["Meridian Bank Multiplier (SGD)"]


def test_each_group_is_capped(accounts, monkeypatch):
    for i in range(run_context.MAX_PER_GROUP + 5):
        run_context.observe("remember_fact", {"fact": f"fact {i}"})

    assert len(run_context.collected()["remembered"]) == run_context.MAX_PER_GROUP


def test_an_empty_group_is_omitted_entirely():
    """So the card can honestly say "No context recorded" when there is none."""
    run_context.observe("telegram_send_message", {"message": "hi"})
    collected = run_context.collected()

    assert set(collected) == {"connectors"}


def test_a_run_that_touched_nothing_records_nothing():
    assert run_context.collected() == {}


def test_a_broken_lookup_never_breaks_the_run(monkeypatch):
    """This is a cosmetic summary; it must not be why a run fails."""

    def _boom():
        raise RuntimeError("store is down")

    monkeypatch.setattr("backend.db.account_store.AccountStore", _boom)
    run_context.observe("get_transactions", {"account_id": "acc_1"})

    assert run_context.collected() == {}


def test_observing_with_no_run_scope_open_is_a_no_op():
    token = run_context._ctx.set(None)
    try:
        run_context.observe("telegram_send_message", {"message": "hi"})
        assert run_context.collected() == {}
    finally:
        run_context._ctx.reset(token)


def test_a_non_dict_input_is_ignored():
    """``telegram_send_message`` records its channel from the TOOL NAME, not the input, so
    it isn't a useful example here anymore — use a tool whose recording genuinely depends
    on parsing ``args``."""
    run_context.observe("remember_fact", "not a dict")

    assert run_context.collected() == {}
