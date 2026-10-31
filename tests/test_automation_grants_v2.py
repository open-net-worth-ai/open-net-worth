"""Automation-local tool grants: Always allow, and nothing per-conversation.

The intended flow, and the one this file pins: an Automation starts with no tool cap; it
calls a tool; a gated tool raises a confirm card in the run's conversation; tapping
**Always allow** records the tool on that Automation so it never asks again.

The per-conversation tier that used to sit beside it (``session_allowed_tools``) was removed
on 2026-08-02 — a card offers Allow once and Always allow, nothing else.
"""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_runtime_store import AutomationRuntimeStore
from backend.db.automation_store import AutomationStore
from backend.services.automations.runs import grant_automation_tool, request_for_run
from backend.services.automations.service import apply_automation_change

pytestmark = pytest.mark.flags(permissions_v2=True)


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


# ── The Blocked list: the other half of an Automation's own say ───────────────────────────


def _blocked(automation_id: str) -> list[str]:
    return AutomationStore().find_by_id(automation_id).get("blocked") or []


def _set_blocked(automation_id: str, tools: list[str]) -> None:
    """How a Blocked entry gets there: the Automation page's Tool permissions section (no card
    action writes it)."""
    from backend.services.automations.service import apply_automation_change

    apply_automation_change({"op": "update", "id": automation_id, "changes": {"blocked": tools}})


def test_always_allow_takes_the_tool_off_the_blocked_list(run_pair):
    automation, run = run_pair
    _set_blocked(automation["id"], ["email_send_message"])

    grant_automation_tool(run["session_id"], "email_send_message", "always", clear_blocked=True)

    assert _blocked(automation["id"]) == []
    assert "email_send_message" in _granted_tools(automation["id"])


def test_the_run_request_carries_the_blocked_list(run_pair):
    automation, run = run_pair
    _set_blocked(automation["id"], ["email_send_message"])

    request = request_for_run(AutomationStore().find_by_id(automation["id"]), run)

    assert request.blocked == ["email_send_message"]


def test_an_automation_stored_before_the_list_existed_has_nothing_blocked(run_pair):
    automation, run = run_pair
    stored = AutomationStore().find_by_id(automation["id"])
    stored.pop("blocked", None)

    assert request_for_run(stored, run).blocked == []


def test_a_tool_cannot_be_both_always_allowed_and_blocked(run_pair):
    from backend.core.errors import ValidationError

    automation, _ = run_pair
    with pytest.raises(ValidationError, match="both always allowed and blocked"):
        apply_automation_change(
            {
                "op": "update",
                "id": automation["id"],
                "changes": {
                    "always_allowed": [
                        {"src": "Open Net Worth", "desc": "Allow", "tools": ["email_send_message"]}
                    ],
                    "blocked": ["email_send_message"],
                },
            }
        )


def test_blocked_must_be_a_list_of_tool_names(run_pair):
    from backend.core.errors import ValidationError

    automation, _ = run_pair
    for bad in ("email_send_message", [1], [""], {"a": 1}):
        with pytest.raises(ValidationError):
            apply_automation_change(
                {"op": "update", "id": automation["id"], "changes": {"blocked": bad}}
            )


def test_a_blocked_list_round_trips_and_dedupes(run_pair):
    automation, _ = run_pair

    saved = apply_automation_change(
        {
            "op": "update",
            "id": automation["id"],
            "changes": {"blocked": ["email_send_message", " email_send_message ", "create_payees"]},
        }
    )

    assert saved["blocked"] == ["email_send_message", "create_payees"]


def test_the_adapter_keeps_only_live_tool_names_on_either_list():
    from backend.ai.automation_runner_adapter import (
        _canonical_always_allowed_tools,
        _canonical_tool_names,
    )

    assert _canonical_tool_names(["email_send_message", "not_a_tool", 3, " create_payees "]) == {
        "email_send_message",
        "create_payees",
    }
    assert _canonical_always_allowed_tools(
        [{"tools": ["create_payees", "nope"], "capability": "email_send_message"}]
    ) == {"create_payees", "email_send_message"}


# ── the app-wide route sends a run card's Always allow to the Automation ──────────────────


def test_the_route_sends_a_run_cards_always_allow_to_the_automations_own_list(
    run_pair, monkeypatch
):
    from fastapi.testclient import TestClient

    published: list = []
    monkeypatch.setattr(
        "backend.core.data_events.publish", lambda domains: published.append(domains)
    )

    from backend.main import app
    from backend.services import tool_permissions_v2 as tool_permissions

    automation, run = run_pair
    # No lifespan: booting the app would seed the starter skills into the shared skills dir.
    with nullcontext(TestClient(app)) as client:
        client.put(
            "/api/tool-permissions/create_payees",
            json={"level": "always_allow", "session_id": run["session_id"]},
        )

    assert "create_payees" in _granted_tools(automation["id"])
    # An open Automation page is told, so it cannot save a stale list back over the card's change.
    assert published.count(["automations"]) == 1
    # It did not touch the app-wide table: the tool still has no row and reads its seed.
    assert tool_permissions.stored_tool_level("core", "create_payees") is None
    assert tool_permissions.unrowed_level("create_payees") == "needs_approval"


def test_a_block_is_never_routed_to_an_automation_from_a_card_session(run_pair):
    """A card has no Block action. The route given a Block names the tool's own row, so the
    Automation's Blocked list is untouched (that list is set on the Automation page)."""
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services import tool_permissions_v2 as tool_permissions

    automation, run = run_pair
    # No lifespan: booting the app would seed the starter skills into the shared skills dir.
    with nullcontext(TestClient(app)) as client:
        client.put(
            "/api/tool-permissions/email_send_message",
            json={"level": "blocked", "session_id": run["session_id"]},
        )

    assert _blocked(automation["id"]) == []
    assert tool_permissions.stored_tool_level("email", "email_send_message") == "blocked"


def test_the_route_writes_the_global_value_for_a_chat_session(run_pair):
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services import tool_permissions_v2 as tool_permissions

    # No lifespan: booting the app would seed the starter skills into the shared skills dir.
    with nullcontext(TestClient(app)) as client:
        client.put(
            "/api/tool-permissions/email_send_message",
            json={"level": "always_allow", "session_id": "some-plain-chat-session"},
        )

    assert tool_permissions.stored_tool_level("email", "email_send_message") == "always_allow"
