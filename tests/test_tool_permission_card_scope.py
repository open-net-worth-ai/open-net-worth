"""A card's Always allow is scoped by the conversation it was tapped in, and never widens.

``PUT /api/tool-permissions/{tool}`` with a ``session_id`` lands on THAT Automation's own Always
allowed list when the session is an Automation run's. When the Automation (or its run) is gone
the grant cannot land there, and falling through to the global table would turn "allow for this
Automation" into "allow everywhere" without anyone choosing that: the route refuses (409) and
writes nothing. A chat conversation has no Automation behind it, so it keeps the global write.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_store import AutomationStore
from backend.db.tool_permission_store import ToolPermissionStore
from backend.services.automations.runs import AUTOMATION_SESSION_PREFIX, is_automation_session
from backend.services.automations.service import (
    apply_automation_change,
    register_automation_cleanup,
)

TOOL = "add_transactions"
OWNER = "core"
GONE = "This Automation no longer exists, so nothing was changed."


@pytest.fixture()
def client(db_isolated):
    from backend.core.automation_runner import register_automation_session_materializer
    from backend.main import app

    register_automation_cleanup(None)
    register_automation_session_materializer(
        lambda run, automation: str(
            run.get("session_id") or f"{AUTOMATION_SESSION_PREFIX}{run['id']}"
        )
    )
    yield TestClient(app)
    register_automation_cleanup(None)
    register_automation_session_materializer(None)


def _automation_with_run(status: str = "running") -> tuple[dict, str]:
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
    session_id = f"{AUTOMATION_SESSION_PREFIX}card-scope"
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": session_id,
            "status": "running",
            "started_at": "2026-10-20T00:00:00+00:00",
        }
    )
    if status != "running":
        AutomationRunStore().transition(run["id"], {"running"}, status, {})
    return automation, session_id


def _put(client, session_id: str | None):
    body: dict = {"level": "always_allow"}
    if session_id is not None:
        body["session_id"] = session_id
    return client.put(f"/api/tool-permissions/{TOOL}", json=body)


def _granted(automation_id: str) -> set[str]:
    stored = AutomationStore().find_by_id(automation_id)
    return {t for item in stored.get("always_allowed") or [] for t in item.get("tools") or []}


def test_the_prefix_names_an_automation_session_whether_or_not_its_run_exists():
    assert is_automation_session(f"{AUTOMATION_SESSION_PREFIX}run_abc")
    assert not is_automation_session("a-chat-conversation")
    assert not is_automation_session("")
    assert not is_automation_session(None)


def test_an_existing_automation_gets_the_grant_on_its_own_list_and_the_table_is_untouched(client):
    automation, session_id = _automation_with_run()
    before = ToolPermissionStore().get_row(OWNER, TOOL)

    response = _put(client, session_id)

    assert response.status_code == 200
    assert _granted(automation["id"]) == {TOOL}
    assert ToolPermissionStore().get_row(OWNER, TOOL) == before


def test_a_deleted_automation_refuses_with_409_and_writes_no_row(client):
    automation, session_id = _automation_with_run(status="completed")
    register_automation_cleanup(
        lambda context, *, phase: {"ok": True, "owned_session_ids": [session_id]}
    )
    apply_automation_change({"op": "delete", "id": automation["id"]})
    before = ToolPermissionStore().get_row(OWNER, TOOL)

    response = _put(client, session_id)

    assert response.status_code == 409
    assert response.json()["detail"] == GONE
    assert ToolPermissionStore().get_row(OWNER, TOOL) == before
    assert before is None or before["level"] != "always_allow"


def test_a_session_with_the_prefix_but_no_run_row_refuses_with_409_and_writes_no_row(client):
    before = ToolPermissionStore().get_row(OWNER, TOOL)

    response = _put(client, f"{AUTOMATION_SESSION_PREFIX}run_that_was_trimmed")

    assert response.status_code == 409
    assert response.json()["detail"] == GONE
    assert ToolPermissionStore().get_row(OWNER, TOOL) == before


def test_a_chat_conversation_still_writes_the_global_row(client):
    response = _put(client, "a-chat-conversation")

    assert response.status_code == 200
    row = ToolPermissionStore().get_row(OWNER, TOOL)
    assert row is not None and row["level"] == "always_allow"


def test_settings_without_a_session_still_writes_the_global_row(client):
    response = _put(client, None)

    assert response.status_code == 200
    row = ToolPermissionStore().get_row(OWNER, TOOL)
    assert row is not None and row["level"] == "always_allow"
