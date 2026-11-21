"""``POST /api/chat/{id}/resolve`` must bind the ``automation`` cost surface for an
Automation-owned session, exactly like ``POST /api/chat`` already does — it used to
hardcode ``"web"`` regardless of ownership, misattributing spend on every approval
resolved inside an Automation's conversation.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.live_sessions as live_sessions
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.core.cost_context import get_cost_surface


@pytest.fixture(autouse=True)
def _isolate():
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(True)
    yield
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_mod.router)
    return TestClient(app)


def test_resolve_binds_the_automation_cost_surface_for_an_automation_owned_session(
    monkeypatch,
):
    import backend.services.automations.runs as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "automation_run_for_session",
        lambda sid: ({"id": "auto1"}, {"id": "run1", "status": "waiting"}),
    )

    seen_surfaces: list[str | None] = []

    async def fake_stream_automation_resolution(session_id, tool_use_id, decision, *, turn_id):
        seen_surfaces.append(get_cost_surface())
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(
        "backend.ai.automation_runner_adapter.stream_automation_resolution",
        fake_stream_automation_resolution,
    )

    c = _client()
    r = c.post(
        "/api/chat/automation-run-resolve/resolve",
        json={"tool_use_id": "toolu_1", "decision": "approve"},
    )
    assert r.status_code == 202
    assert seen_surfaces == ["automation"]


def test_resolve_still_binds_the_web_cost_surface_for_an_ordinary_session(monkeypatch):
    import backend.services.automations.runs as runs_mod

    monkeypatch.setattr(runs_mod, "automation_run_for_session", lambda sid: None)

    seen_surfaces: list[str | None] = []

    async def fake_resolve_pending_tool_use(session_id, tool_use_id, decision, system, **_kw):
        seen_surfaces.append(get_cost_surface())
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(
        chat_mod.assistant, "resolve_pending_tool_use", fake_resolve_pending_tool_use
    )

    c = _client()
    r = c.post(
        "/api/chat/s-ordinary/resolve",
        json={"tool_use_id": "toolu_1", "decision": "approve"},
    )
    assert r.status_code == 202
    assert seen_surfaces == ["web"]
