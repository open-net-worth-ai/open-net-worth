"""Automation-local tool grants: Always allow, and nothing per-conversation.

The intended flow, and the one this file pins: an Automation starts with no tool cap; it
calls a tool; a gated tool raises a confirm card in the run's conversation; tapping
**Always allow** records the tool on that Automation so it never asks again.

The per-conversation tier that used to sit beside it (``session_allowed_tools``) was removed
on 2026-08-02 — a card offers Allow once and Always allow, nothing else.
"""

from __future__ import annotations

import pytest

from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_runtime_store import AutomationRuntimeStore
from backend.db.automation_store import AutomationStore
from backend.services.automations.runs import grant_automation_tool, request_for_run
from backend.services.automations.service import apply_automation_change


@pytest.fixture
def isolated_automation_paths(db_isolated):
    from backend.core.automation_runner import register_automation_session_materializer
    from backend.services.automations.service import register_automation_cleanup

    register_automation_cleanup(None)
    register_automation_session_materializer(
        lambda run, automation: str(run.get("session_id") or f"automation-run-{run['id']}")
    )
    yield
    register_automation_cleanup(None)
    register_automation_session_materializer(None)


@pytest.fixture
def run_pair(isolated_automation_paths):
    """One Automation with an empty grant list, plus a run bound to a known session id."""
    automation = apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Review cash",
                "description": "Review the current cash position.",
                "instructions": "Review my cash position.",
                "trigger": {"kind": "manual"},
                "execution": {"kind": "lucius"},
                "always_allowed": [],
            },
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-grant-test",
            "status": "running",
            "started_at": "2026-08-02T00:00:00+00:00",
        }
    )
    return automation, run


def _granted_tools(automation_id: str) -> set[str]:
    stored = AutomationStore().find_by_id(automation_id)
    return {tool for item in stored.get("always_allowed") or [] for tool in item.get("tools") or []}


def test_always_appends_to_the_automations_always_allowed_list(run_pair):
    automation, run = run_pair
    assert grant_automation_tool(run["session_id"], "telegram_send_message", "always") is True
    assert "telegram_send_message" in _granted_tools(automation["id"])


def test_always_is_idempotent(run_pair):
    automation, run = run_pair
    grant_automation_tool(run["session_id"], "telegram_send_message", "always")
    grant_automation_tool(run["session_id"], "telegram_send_message", "always")
    entries = AutomationStore().find_by_id(automation["id"])["always_allowed"]
    assert len([e for e in entries if "telegram_send_message" in (e.get("tools") or [])]) == 1


def test_once_is_accepted_and_stores_nothing(run_pair):
    automation, run = run_pair
    assert grant_automation_tool(run["session_id"], "telegram_send_message", "once") is True
    assert _granted_tools(automation["id"]) == set()


def test_the_session_scope_is_refused(run_pair):
    """The per-conversation tier is gone; the retired scope must not quietly succeed."""
    automation, run = run_pair
    assert grant_automation_tool(run["session_id"], "telegram_send_message", "session") is False
    assert _granted_tools(automation["id"]) == set()


def test_a_session_that_is_not_an_automation_run_is_not_ours(run_pair):
    assert (
        grant_automation_tool("some-plain-chat-session", "telegram_send_message", "always") is False
    )


def test_the_run_request_carries_the_standing_grants(run_pair):
    automation, run = run_pair
    grant_automation_tool(run["session_id"], "telegram_send_message", "always")

    request = request_for_run(AutomationStore().find_by_id(automation["id"]), run)
    granted = {tool for item in request.always_allowed for tool in (item.get("tools") or [])}
    assert granted == {"telegram_send_message"}


def test_a_legacy_session_grant_on_disk_never_widens_a_run(run_pair):
    """A session_allowed_tools key written before the collapse must be ignored, not honoured
    — reading it would silently hand the run a tool nobody granted durably."""
    automation, run = run_pair
    AutomationRuntimeStore().put_for_automation(
        automation["id"],
        {"session_allowed_tools": {run["session_id"]: ["delete_account"]}},
    )

    request = request_for_run(AutomationStore().find_by_id(automation["id"]), run)
    granted = {tool for item in request.always_allowed for tool in (item.get("tools") or [])}
    assert "delete_account" not in granted


def test_the_runtime_store_no_longer_offers_a_session_grant_writer():
    assert not hasattr(AutomationRuntimeStore, "grant_session_tool")


# ── fetch_hosts: the websites a run may open without asking ─────────────────


def _stage_fetch_card(session_id: str, tool_use_id: str, url: str) -> None:
    from backend.db.pending_plan_store import PendingPlanStore

    PendingPlanStore().stage_pending(tool_use_id, "fetch_page", {"url": url}, session_id=session_id)


def _fetch_hosts(automation_id: str) -> list[str]:
    return AutomationStore().find_by_id(automation_id).get("fetch_hosts") or []


def test_fetch_hosts_are_stored_normalised_and_bad_entries_refused(run_pair):
    from backend.core.errors import ValidationError

    automation, _run = run_pair
    apply_automation_change(
        {
            "op": "update",
            "id": automation["id"],
            "changes": {"fetch_hosts": ["https://Store.SteamPowered.com/x", "*.example.com"]},
        }
    )
    assert _fetch_hosts(automation["id"]) == ["store.steampowered.com", "example.com"]
    with pytest.raises(ValidationError, match="not a website name"):
        apply_automation_change(
            {"op": "update", "id": automation["id"], "changes": {"fetch_hosts": ["localhost"]}}
        )


def test_always_on_a_fetch_card_allows_that_website_not_every_website(run_pair):
    automation, run = run_pair
    _stage_fetch_card(run["session_id"], "tu_a", "https://store.steampowered.com/r?sparams=x")
    _stage_fetch_card(run["session_id"], "tu_b", "https://attacker.example/c?d=1")

    assert grant_automation_tool(run["session_id"], "fetch_page", "always", tool_use_id="tu_a")
    assert _fetch_hosts(automation["id"]) == ["store.steampowered.com"]
    assert "fetch_page" not in _granted_tools(automation["id"])


def test_an_unidentifiable_fetch_card_falls_back_to_the_whole_tool_grant(run_pair):
    """Two open fetch cards and no tool_use_id (an old client): one tap must not consent to
    both websites, so it records exactly what the button has always meant."""
    automation, run = run_pair
    _stage_fetch_card(run["session_id"], "tu_a", "https://one.example/a")
    _stage_fetch_card(run["session_id"], "tu_b", "https://two.example/b")

    assert grant_automation_tool(run["session_id"], "fetch_page", "always")
    assert _fetch_hosts(automation["id"]) == []
    assert "fetch_page" in _granted_tools(automation["id"])


def test_the_run_request_carries_the_fetch_hosts(run_pair):
    automation, run = run_pair
    apply_automation_change(
        {"op": "update", "id": automation["id"], "changes": {"fetch_hosts": ["example.com"]}}
    )
    request = request_for_run(AutomationStore().find_by_id(automation["id"]), run)
    assert request.fetch_hosts == ["example.com"]


def test_lucius_cannot_write_fetch_hosts():
    """The list widens what a run may send out unasked — an owner decision, never something a
    prompt-injected create/update can narrate into existence."""
    from backend.ai.tools.automations import _AI_UPDATABLE_FIELDS

    assert "fetch_hosts" not in _AI_UPDATABLE_FIELDS


def test_always_on_a_provider_fetch_card_also_allows_only_that_website(run_pair):
    """``tavily_fetch_page`` and the other per-provider fetch tools pass the same gate, so their
    Always means the same thing."""
    from backend.db.pending_plan_store import PendingPlanStore

    automation, run = run_pair
    PendingPlanStore().stage_pending(
        "tu_t",
        "tavily_fetch_page",
        {"url": "https://shop.example.com/r"},
        session_id=run["session_id"],
    )
    assert grant_automation_tool(
        run["session_id"], "tavily_fetch_page", "always", tool_use_id="tu_t"
    )
    assert _fetch_hosts(automation["id"]) == ["shop.example.com"]
    assert "tavily_fetch_page" not in _granted_tools(automation["id"])
