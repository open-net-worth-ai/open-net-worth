"""AI-written conversation titles (``ai/session_titles.py``).

A new in-app conversation is named from its opening message by a small background model
call, replacing the client's first-60-characters title. Three writers race for one field —
the client's automatic derivation, the AI title, and the person's own rename — so ownership
is recorded in ``meta.title_source`` and checked in the same transaction as the write.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.ai import session_titles
from backend.db.session_store import SessionStore


def _meta(sid: str) -> dict:
    return SessionStore().get_meta(sid) or {}


# ── Ownership: who may overwrite whom ──────────────────────────────────────────────


def test_ai_title_replaces_the_clients_automatic_one():
    store = SessionStore()
    store.write_title(
        "s-ai", "can you check why my cash looks off since the last import", source="auto"
    )

    assert store.write_title("s-ai", "Cash drift after import", source="ai") is True
    assert _meta("s-ai")["title"] == "Cash drift after import"
    assert _meta("s-ai")["title_source"] == "ai"


def test_a_late_automatic_write_never_clobbers_the_ai_title():
    """The client PATCHes its first-message title fire-and-forget; if that request lands
    after the AI title, it must lose."""
    store = SessionStore()
    store.write_title("s-late", "New conversation", source="auto")
    store.write_title("s-late", "Cash drift after import", source="ai")

    assert store.write_title("s-late", "can you check why my cash", source="auto") is False
    assert _meta("s-late")["title"] == "Cash drift after import"


def test_a_rename_is_never_replaced_by_the_ai_title():
    store = SessionStore()
    store.write_title("s-user", "New conversation", source="auto")
    store.write_title("s-user", "My tax stuff", source="user")

    assert store.write_title("s-user", "Tax lot review", source="ai") is False
    assert store.write_title("s-user", "whatever the client derived", source="auto") is False
    assert _meta("s-user")["title"] == "My tax stuff"


def test_a_rename_beats_an_ai_title():
    store = SessionStore()
    store.write_title("s-re", "New conversation", source="auto")
    store.write_title("s-re", "Cash drift after import", source="ai")

    assert store.write_title("s-re", "Import mess", source="user") is True
    assert _meta("s-re")["title"] == "Import mess"


def test_the_ai_title_is_written_once():
    store = SessionStore()
    store.write_title("s-once", "New conversation", source="auto")
    store.write_title("s-once", "First topic", source="ai")

    assert store.write_title("s-once", "Second topic", source="ai") is False


def test_the_ai_title_never_resurrects_a_deleted_conversation():
    """Generation runs while the turn does; the person can delete the conversation first."""
    assert SessionStore().write_title("s-gone", "Cash drift", source="ai") is False
    assert SessionStore().get_meta("s-gone") is None


# ── What the model sees and what it may return ──────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('"Cash drift after import."', "Cash drift after import"),
        ("  Rebalance   the\nIRA  ", "Rebalance the IRA"),
        ("/tax-audit review", "tax-audit review"),
        ("Review [account: acc_1|Chase checking] fees", "Review fees"),
        ("**Budget reset for October**", "Budget reset for October"),
        ("", ""),
        ('"..."', ""),
    ],
)
def test_clean_title(raw, expected):
    assert session_titles.clean_title(raw) == expected


def test_a_long_title_is_cut_on_a_word_boundary():
    title = session_titles.clean_title("word " * 30)
    assert len(title) <= session_titles.MAX_TITLE_CHARS
    assert title.endswith("word")


def test_chips_reach_the_model_as_their_labels_and_selectors_are_dropped():
    visible = session_titles._visible_text(
        "/tax-audit how did [account: acc_9|Fidelity IRA] do this year?"
    )
    assert visible == "how did Fidelity IRA do this year?"
    assert session_titles._visible_text("/tax-audit") == ""


async def test_generate_title_uses_the_title_task_model(monkeypatch):
    seen: dict = {}

    async def fake_complete_model(prompt, response_model, **kw):
        seen["prompt"] = prompt
        seen.update(kw)
        return response_model(title="Cash drift after import.")

    monkeypatch.setattr(session_titles, "complete_model", fake_complete_model)
    monkeypatch.setattr(session_titles, "_title_model", lambda: "anthropic:claude-haiku-4-5")

    title = await session_titles.generate_title("why is my cash off since the import?")

    assert title == "Cash drift after import"
    assert seen["model"] == "anthropic:claude-haiku-4-5"
    assert seen["source"] == "chat_title"
    assert "why is my cash off" in seen["prompt"]


async def test_a_failed_generation_leaves_the_title_alone(monkeypatch):
    async def failing(*_a, **_k):
        return None

    monkeypatch.setattr(session_titles, "complete_model", failing)
    SessionStore().write_title("s-fail", "why is my cash off", source="auto")

    await session_titles._title_session("s-fail", "why is my cash off")

    assert _meta("s-fail")["title"] == "why is my cash off"


# ── Scheduling ─────────────────────────────────────────────────────────────────────


@pytest.mark.real_session_title
async def test_schedule_titles_the_session_and_reports_it_as_recent(monkeypatch):
    async def fake_complete_model(prompt, response_model, **kw):
        return response_model(title="Cash drift after import")

    monkeypatch.setattr(session_titles, "complete_model", fake_complete_model)
    SessionStore().write_title("s-sched", "why is my cash off", source="auto")

    assert session_titles.schedule_title("s-sched", "why is my cash off") is True
    assert session_titles.schedule_title("s-sched", "why is my cash off") is False, "one call"
    await asyncio.gather(*list(session_titles._tasks))

    assert _meta("s-sched")["title"] == "Cash drift after import"
    assert "s-sched" in session_titles.recently_titled()
    assert "s-sched" not in session_titles._pending


@pytest.mark.real_session_title
@pytest.mark.parametrize("message", ["__ONBOARDING_INIT__", "/tax-audit", "   "])
async def test_nothing_to_name_schedules_nothing(message):
    assert session_titles.schedule_title("s-none", message) is False


# ── Wiring: the chat POST and the PATCH route ──────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    turns.set_inline_for_tests(True)
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kw: "SYS")

    async def fake_run(session_id, new_messages, system, **kw) -> AsyncGenerator[dict, None]:
        # The real turn appends to the session's history — which is what makes the next
        # POST no longer the opening message.
        assistant.SESSIONS.setdefault(session_id, []).extend(new_messages)
        yield {"type": "turn_done"}

    monkeypatch.setattr(assistant, "run", fake_run)
    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        session_titles, "schedule_title", lambda sid, text: scheduled.append((sid, text)) or True
    )
    app = FastAPI()
    app.include_router(chat_mod.router)
    yield TestClient(app), scheduled
    turns.set_inline_for_tests(False)


def _send(c: TestClient, sid: str, text: str):
    r = c.post(
        "/api/chat", json={"session_id": sid, "messages": [{"role": "user", "content": text}]}
    )
    assert r.status_code == 202, r.text


def test_only_the_opening_message_is_titled(client):
    c, scheduled = client
    _send(c, "s-post", "why is my cash off since the import?")
    _send(c, "s-post", "and what about last month?")

    assert scheduled == [("s-post", "why is my cash off since the import?")]


def test_patch_without_auto_is_a_rename(client):
    c, _ = client
    SessionStore().write_title("s-patch", "New conversation", source="auto")
    SessionStore().write_title("s-patch", "Cash drift", source="ai")

    c.patch("/api/sessions/s-patch", json={"title": "first message text", "auto": True})
    assert _meta("s-patch")["title"] == "Cash drift"

    c.patch("/api/sessions/s-patch", json={"title": "Mine"})
    assert (_meta("s-patch")["title"], _meta("s-patch")["title_source"]) == ("Mine", "user")


def test_live_listing_reports_recent_titles(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(session_titles, "_recent", {"s-live": 9e12})
    body = c.get("/api/sessions/live").json()
    assert body["titled"] == {"s-live": 9e12}


def test_a_call_is_named_from_its_first_real_request(monkeypatch):
    """Both of the owner's calls were titled with their greeting (2026-11-07)."""
    from backend.ai import assistant

    metas = {
        "s1": {"title_source": "auto"},
        "s2": {"title_source": "ai"},
        "s3": {"title_source": "user"},
    }
    store = type("Store", (), {"get_meta": lambda _self, sid: metas.get(sid)})()
    monkeypatch.setattr(assistant, "_store", lambda: store)
    asked = []
    monkeypatch.setattr(
        session_titles, "schedule_title", lambda sid, text: asked.append((sid, text)) or True
    )

    assert session_titles.title_from_request("s1", "Check my ACME spending") is True
    assert asked == [("s1", "Check my ACME spending")]
    assert session_titles.title_from_request("s2", "Another one") is False
    assert session_titles.title_from_request("s3", "Another one") is False
    assert len(asked) == 1
    assert session_titles.title_from_request("new", "A first request") is True


def test_an_unreadable_title_never_fails_the_request(monkeypatch):
    from backend.ai import assistant

    def broken():
        raise RuntimeError("no store")

    monkeypatch.setattr(assistant, "_store", broken)
    assert session_titles.title_from_request("s1", "words") is False
