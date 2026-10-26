"""The message that starts a turn is announced on the live stream (live report 2026-10-26).

A device watching a conversation saw a phone's photo turn as its reply only — the photo itself
never appeared until a refresh, because nothing on ``GET /live`` carried the user's message.
``POST /api/chat`` now publishes a ``user_message`` event keyed by the sender's own
``client_message_id`` (so the sender recognises its optimistic copy), with the attachment
refs a bubble renders. Hermetic: ``assistant.run`` is a scripted fake.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.ai.tool_text import ONBOARDING_INIT


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
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


def _user_messages(c: TestClient, session_id: str) -> list[dict]:
    events = []
    for line in c.get(f"/api/sessions/{session_id}/live").text.splitlines():
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            events.append(json.loads(line[6:]))
    return [e for e in events if e.get("type") == "user_message"]


def test_a_captionless_photo_is_announced_with_its_attachment(monkeypatch):
    async def fake_content(text, ref_ids, session_id=None):
        return [{"type": "text", "text": text}]

    monkeypatch.setattr(chat_mod, "build_user_content", fake_content)
    monkeypatch.setattr(
        "backend.services.attachments.attachment_file_meta",
        lambda ids: {"att_1": {"filename": "IMG_0001.jpg", "media_type": "image/jpeg"}},
    )
    c = _client()
    r = c.post(
        "/api/chat",
        json={
            "session_id": "s-photo",
            "messages": [{"role": "user", "content": ""}],
            "attachments": ["att_1", "att_gone"],
            "client_message_id": "m_phone",
        },
    )
    assert r.status_code == 202

    [announced] = _user_messages(c, "s-photo")
    assert announced["message_id"] == "m_phone"
    assert announced["turn_id"] == r.json()["turn_id"]
    assert announced["text"] == ""
    assert announced["attachments"] == [
        {"ref_id": "att_1", "kind": "image", "summary": "IMG_0001.jpg"}
    ]


def test_a_text_message_is_announced_under_the_senders_id():
    c = _client()
    c.post(
        "/api/chat",
        json={
            "session_id": "s-text",
            "messages": [{"role": "user", "content": "how much did I spend?"}],
            "client_message_id": "m_desk",
        },
    )
    [announced] = _user_messages(c, "s-text")
    assert (announced["message_id"], announced["text"]) == ("m_desk", "how much did I spend?")


@pytest.mark.parametrize("body", [{"retry": True}, {"content": ONBOARDING_INIT}])
def test_a_retry_or_harness_trigger_announces_nothing(body):
    c = _client()
    c.post(
        "/api/chat",
        json={
            "session_id": "s-quiet",
            "messages": [{"role": "user", "content": body.get("content", "again")}],
            "retry": body.get("retry", False),
            "client_message_id": "m_x",
        },
    )
    assert _user_messages(c, "s-quiet") == []
