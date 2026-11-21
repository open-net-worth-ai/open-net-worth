"""Every turn's prompt is built for the model that serves it — including the second half of a
turn resumed by approving a card, a headless run and a capture.

Only the chat path built the prompt for the turn's own model. The approval-resume paths (web,
Telegram and other channels, voice) built it for the instance default AND ran the rest of the
turn on the default: a conversation switched to gpt-6-luna under a Claude default lost both
its model and the GPT tool-loading block (ADR
``2026-11-21-model-family-guidance-is-chosen-by-model-name``) the moment a card was approved —
exactly where a model may need to load the next tool. A headless run and a capture ran their
own model with the default's prompt.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

import backend.ai.tools  # noqa: F401  (populates the tool registry the prompt renders)
from backend.ai import conversation
from backend.ai.model_guidance import GPT_TOOL_LOADING

REPO = Path(__file__).resolve().parents[1]
GPT_REF = "openai:gpt-6-luna"


@pytest.fixture
def claude_default(monkeypatch):
    """The instance default is Claude; a ``provider:model`` ref resolves to its bare id."""
    from backend.db.model_config_store import ModelConfigStore

    real_resolved = ModelConfigStore.resolved
    real_override = ModelConfigStore.resolve_override

    def resolved(self):
        return {**real_resolved(self), "model": "sonnet"}

    def resolve_override(self, override):
        key, _, model = (override or "").partition(":")
        if key in ("openai", "claude_cli", "codex_cli") and model:
            return {"provider_key": key, "transport": key, "base_url": "", "model": model}
        return real_override(self, override)

    monkeypatch.setattr(ModelConfigStore, "resolved", resolved)
    monkeypatch.setattr(ModelConfigStore, "resolve_override", resolve_override)


def _fake_resume(seen: dict):
    async def fake(session_id, tool_use_id, decision, system, **kwargs):
        seen.update(kwargs, system=system, session_id=session_id)
        yield {"type": "turn_done", "usage": {}}

    return fake


def _resume(session_id: str = "s-1", **kwargs) -> None:
    async def drain():
        async for _ in conversation.resume_pending(session_id, "toolu_1", "approve", **kwargs):
            pass

    asyncio.run(drain())


# ── the prompt for a model ref ─────────────────────────────────────────────────


@pytest.mark.parametrize("ref", [GPT_REF, "codex_cli:gpt-6-luna", "gpt-6-luna"])
def test_a_gpt_ref_gets_the_block_under_a_claude_default(claude_default, ref):
    assert GPT_TOOL_LOADING in conversation.system_prompt_for(ref)


@pytest.mark.parametrize("ref", [None, "", "claude_cli:sonnet"])
def test_no_ref_or_a_claude_ref_gets_no_block(claude_default, ref):
    assert GPT_TOOL_LOADING not in conversation.system_prompt_for(ref)


def test_a_claude_ref_gets_no_block_under_a_gpt_default(monkeypatch):
    """The other direction: a GPT default must not leak its block into a Claude turn."""
    from backend.db.model_config_store import ModelConfigStore

    real_resolved = ModelConfigStore.resolved
    monkeypatch.setattr(
        ModelConfigStore, "resolved", lambda self: {**real_resolved(self), "model": "gpt-6-luna"}
    )
    monkeypatch.setattr(
        ModelConfigStore,
        "resolve_override",
        lambda self, ref: {"provider_key": "claude_cli", "base_url": "", "model": "sonnet"},
    )
    assert GPT_TOOL_LOADING in conversation.system_prompt_for(None)
    assert GPT_TOOL_LOADING not in conversation.system_prompt_for("claude_cli:sonnet")


# ── resuming after an approval ───────────────────────────────────────────────────


def test_a_resume_runs_on_the_conversations_own_model_with_its_prompt(claude_default, monkeypatch):
    from backend.ai import assistant

    seen: dict = {}
    monkeypatch.setattr(conversation, "session_model_ref", lambda _sid, **_k: GPT_REF)
    monkeypatch.setattr(assistant, "resolve_pending_tool_use", _fake_resume(seen))

    _resume(surface="chat:telegram", sender_id="u-1", turn_id="turn_x")

    assert seen["model"] == GPT_REF, "the rest of the turn runs on the conversation's model"
    assert GPT_TOOL_LOADING in seen["system"]
    assert (seen["surface"], seen["sender_id"], seen["turn_id"]) == (
        "chat:telegram",
        "u-1",
        "turn_x",
    )


def test_a_resume_without_a_model_of_its_own_is_unchanged(claude_default, monkeypatch):
    """No stored model: the default, and its prompt — the old path, byte for byte."""
    from backend.ai import assistant
    from backend.ai.prompts import build_system_prompt

    seen: dict = {}
    monkeypatch.setattr(conversation, "session_model_ref", lambda _sid, **_k: None)
    monkeypatch.setattr(assistant, "resolve_pending_tool_use", _fake_resume(seen))

    _resume()

    assert seen["model"] is None
    assert seen["system"] == build_system_prompt()


def test_approving_on_the_web_resumes_on_the_conversations_model(claude_default, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.ai.live_sessions as live_sessions
    import backend.ai.turns as turns
    import backend.api.chat as chat_mod
    import backend.services.automations.runs as runs_mod
    from backend.ai import assistant

    seen: dict = {}
    monkeypatch.setattr(runs_mod, "automation_run_for_session", lambda _sid: None)
    monkeypatch.setattr(conversation, "session_model_ref", lambda _sid, **_k: GPT_REF)
    monkeypatch.setattr(assistant, "resolve_pending_tool_use", _fake_resume(seen))
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(True)
    try:
        app = FastAPI()
        app.include_router(chat_mod.router)
        r = TestClient(app).post(
            "/api/chat/s-web/resolve", json={"tool_use_id": "toolu_1", "decision": "approve"}
        )
    finally:
        live_sessions._reset_for_tests()
        turns.set_inline_for_tests(False)
        turns._turns.clear()
        turns._by_session.clear()

    assert r.status_code == 202
    assert seen["model"] == GPT_REF
    assert GPT_TOOL_LOADING in seen["system"]


@pytest.mark.parametrize(
    "path",
    [
        "backend/api/chat.py",
        "backend/connectors/processor.py",
        "backend/connectors/chat/confirm.py",
        "backend/connectors/voice/desk.py",
    ],
)
def test_every_interactive_approval_path_resumes_through_the_one_helper(path):
    """A new caller of the raw resume would bring the instance-default fallback back."""
    source = (REPO / path).read_text()
    assert not re.search(r"\bresolve_pending_tool_use\(", source)
    assert "resume_pending(" in source


# ── headless runs and captures ───────────────────────────────────────────────────


def _fake_run(seen: dict):
    async def fake(*, session_id, new_messages, system, model, **_kw):
        seen.update(system=system, model=model)
        yield {"type": "text_delta", "text": "ok"}

    return fake


def test_a_headless_run_gets_the_prompt_of_the_model_it_runs(
    db_isolated, claude_default, monkeypatch
):
    from backend import main

    seen: dict = {}
    monkeypatch.setattr("backend.ai.assistant.run", _fake_run(seen))

    asyncio.run(main._headless_turn_runner("check the book", model=GPT_REF))
    assert seen["model"] == GPT_REF
    assert GPT_TOOL_LOADING in seen["system"]

    asyncio.run(main._headless_turn_runner("check the book"))
    assert GPT_TOOL_LOADING not in seen["system"], "no model → the Claude default's prompt"


@pytest.mark.asyncio
async def test_a_capture_gets_the_prompt_of_the_model_it_runs(
    db_isolated, claude_default, monkeypatch
):
    from backend.ai import capture as capture_mod

    seen: dict = {}
    monkeypatch.setattr("backend.ai.assistant.run", _fake_run(seen))

    await capture_mod.run_capture("coffee 4.50", model=GPT_REF)
    assert seen["model"] == GPT_REF
    assert GPT_TOOL_LOADING in seen["system"]

    await capture_mod.run_capture("coffee 4.50")
    assert GPT_TOOL_LOADING not in seen["system"]
