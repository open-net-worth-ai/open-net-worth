"""Behavioral proof for catalog-driven and direct inline skill activation.

The provider is deterministic, but the lifecycle is real: the chat route builds the
catalog, the skill repository resolves the record, the assistant injects the hidden
instructions, and registry dispatch executes the follow-up tool.  The provider only
chooses its next action from the system/messages/tools payload it actually receives.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.pending_injection as pending_injection
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.ai.tools import _skill_scope
from backend.api.chat import ChatRequest
from backend.db import skill_store
from backend.db.skill_store import SkillStore
from tests._asgi import make_request

_AUTO_TRIGGER = "AUTO-TRIGGER-SIGNATURE"
_INSTRUCTION = "DISTINCTIVE-RUNTIME-INSTRUCTION"
_MATCHING_PROMPT = "Prepare the orbital variance checksum report."
_ADJACENT_PROMPT = "What does variance mean in statistics?"


def _tool_result(messages: list[dict], tool_use_id: str) -> dict | None:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") == tool_use_id
            ):
                try:
                    payload = json.loads(block.get("content") or "")
                except (TypeError, json.JSONDecodeError):
                    return None
                return payload if isinstance(payload, dict) else None
    return None


class _CatalogFollowingProvider:
    """Tiny scripted model whose branches depend on the real provider payload."""

    def __init__(self):
        self.calls: list[dict] = []

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        call = {
            "messages": deepcopy(messages),
            "tools": deepcopy(tools),
            "system": system,
        }
        self.calls.append(call)
        message_text = json.dumps(messages)
        offered = {tool["name"] for tool in tools}
        calculated = _tool_result(messages, "runtime-calculate")

        if calculated is not None:
            result = calculated.get("result")
            yield {"type": "text_delta", "text": f"ORBITAL CHECKSUM: {result}"}
            yield {"type": "done", "stop_reason": "end_turn"}
            return

        if _INSTRUCTION in message_text:
            assert "calculate" in offered
            yield {
                "type": "tool_use",
                "id": "runtime-calculate",
                "name": "calculate",
                "input": {"expression": "6 * 7"},
            }
            yield {"type": "done", "stop_reason": "tool_use"}
            return

        if _AUTO_TRIGGER in system and _MATCHING_PROMPT in message_text:
            assert "activate_skill" in offered
            yield {
                "type": "tool_use",
                "id": "runtime-skill",
                "name": "activate_skill",
                "input": {"skill_id": "orbital-checksum"},
            }
            yield {"type": "done", "stop_reason": "tool_use"}
            return

        yield {"type": "text_delta", "text": "Ordinary answer; no skill activated."}
        yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture(autouse=True)
def _isolate_runtime(tmp_path, monkeypatch, db_isolated):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    # SKILLS_STATE_JSON is still a real (if no-longer-I/O) path — skill_store.py uses its
    # .parent purely as a stable directory-lock key (data_maintenance_lock), so this patch
    # still matters for per-test lock isolation even though skill STATE itself is DB-backed
    # (SkillStateStore) now. Sessions are DB-backed too — db_isolated (above) covers both.
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    monkeypatch.setattr(assistant, "_record_cost", lambda *args, **kwargs: None)

    assistant.SESSIONS.clear()
    assistant._SESSION_LOCKS.clear()
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    _skill_scope.clear_scope()
    turns.set_inline_for_tests(True)
    yield
    assistant.SESSIONS.clear()
    assistant._SESSION_LOCKS.clear()
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    _skill_scope.clear_scope()
    turns.set_inline_for_tests(False)


def _skill(*, enabled: bool = True, manual_only: bool = False) -> dict:
    return SkillStore().create(
        {
            "name": "orbital-checksum",
            "description": f"Build the orbital variance checksum report. {_AUTO_TRIGGER}",
            "body": (
                f"{_INSTRUCTION}: call calculate with expression exactly `6 * 7`, then "
                "report its result under `ORBITAL CHECKSUM`."
            ),
            "allowed_tools": ["calculate"],
            "enabled": enabled,
            "openai": {"policy": {"allow_implicit_invocation": not manual_only}},
        }
    )


async def _chat(monkeypatch, provider: _CatalogFollowingProvider, session_id: str, text: str):
    context = assistant.ModelTurnContext(
        model="test-model",
        provider_key="",
        transport="anthropic",
        base_url="",
        provider=provider,
    )
    monkeypatch.setattr(assistant, "resolve_model_context", lambda _model: context)
    response = await chat_mod.chat(
        ChatRequest(session_id=session_id, messages=[{"role": "user", "content": text}]),
        make_request(),
    )
    assert response.status_code == 202


def _tool_uses(session_id: str) -> list[dict]:
    return [
        block
        for message in assistant._load_session(session_id) or []
        if message.get("role") == "assistant" and isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _assistant_text(session_id: str) -> str:
    chunks: list[str] = []
    for message in assistant._load_session(session_id) or []:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            chunks.extend(
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
    return "".join(chunks)


async def test_catalog_activation_drives_real_tool_output_but_disabled_skill_does_not(
    monkeypatch,
):
    _skill()
    enabled_provider = _CatalogFollowingProvider()

    await _chat(monkeypatch, enabled_provider, "enabled-fresh", _MATCHING_PROMPT)

    assert [block["name"] for block in _tool_uses("enabled-fresh")] == [
        "activate_skill",
        "calculate",
    ]
    assert _tool_result(enabled_provider.calls[-1]["messages"], "runtime-calculate") == {
        "expression": "6 * 7",
        "result": 42,
    }
    assert "ORBITAL CHECKSUM: 42" in _assistant_text("enabled-fresh")
    assert any(
        entry.get("_onw_skill", {}).get("skill_id") == "orbital-checksum"
        for entry in assistant._load_session("enabled-fresh") or []
    )
    assert "You may ONLY use the tools this skill allows" not in json.dumps(
        enabled_provider.calls[1]["messages"]
    )

    SkillStore().update_skill("orbital-checksum", {"enabled": False})
    disabled_provider = _CatalogFollowingProvider()
    await _chat(monkeypatch, disabled_provider, "disabled-fresh", _MATCHING_PROMPT)

    assert len(disabled_provider.calls) == 1
    assert _AUTO_TRIGGER not in disabled_provider.calls[0]["system"]
    assert _INSTRUCTION not in json.dumps(disabled_provider.calls[0]["messages"])
    assert _tool_uses("disabled-fresh") == []
    assert "ORBITAL CHECKSUM" not in _assistant_text("disabled-fresh")


async def test_adjacent_wording_keeps_catalog_visible_without_activating_skill(monkeypatch):
    _skill()
    provider = _CatalogFollowingProvider()

    await _chat(monkeypatch, provider, "adjacent-fresh", _ADJACENT_PROMPT)

    assert _AUTO_TRIGGER in provider.calls[0]["system"]
    assert len(provider.calls) == 1
    assert _INSTRUCTION not in json.dumps(provider.calls[0]["messages"])
    assert _tool_uses("adjacent-fresh") == []


async def test_manual_only_skill_skips_automatic_activation_but_direct_slash_executes(
    monkeypatch,
):
    _skill(manual_only=True)
    automatic_provider = _CatalogFollowingProvider()

    await _chat(monkeypatch, automatic_provider, "manual-auto", _MATCHING_PROMPT)

    assert _AUTO_TRIGGER not in automatic_provider.calls[0]["system"]
    assert _tool_uses("manual-auto") == []

    direct_provider = _CatalogFollowingProvider()
    await _chat(monkeypatch, direct_provider, "manual-direct", "/orbital-checksum")

    assert _INSTRUCTION in json.dumps(direct_provider.calls[0]["messages"])
    assert [block["name"] for block in _tool_uses("manual-direct")] == ["calculate"]
    assert _tool_result(direct_provider.calls[-1]["messages"], "runtime-calculate") == {
        "expression": "6 * 7",
        "result": 42,
    }


async def test_structured_direct_skill_is_loaded_before_the_first_provider_call(monkeypatch):
    _skill(manual_only=True)
    provider = _CatalogFollowingProvider()
    context = assistant.ModelTurnContext(
        model="test-model",
        provider_key="",
        transport="anthropic",
        base_url="",
        provider=provider,
    )
    monkeypatch.setattr(assistant, "resolve_model_context", lambda _model: context)

    response = await chat_mod.chat(
        ChatRequest(
            session_id="structured-direct",
            messages=[
                {
                    "role": "user",
                    "content": "[skill: orbital-checksum|Orbital checksum]",
                }
            ],
            skills=["orbital-checksum"],
        ),
        make_request(),
    )

    assert response.status_code == 202
    first_call = provider.calls[0]
    instruction_message = next(
        message
        for message in first_call["messages"]
        if _INSTRUCTION in json.dumps(message.get("content"))
    )
    assert instruction_message["role"] == "user"
    assert _INSTRUCTION not in first_call["system"]
    assert not any(block["name"] == "activate_skill" for block in _tool_uses("structured-direct"))
    assert any(
        entry.get("_onw_skill", {}).get("skill_id") == "orbital-checksum"
        for entry in assistant._load_session("structured-direct") or []
    )


async def test_multiple_structured_skills_load_in_order_as_user_context(monkeypatch):
    _skill(manual_only=True)
    SkillStore().create(
        {
            "name": "report-format",
            "description": "Format a report.",
            "body": "SECOND-DISTINCTIVE-INSTRUCTION",
            "openai": {"policy": {"allow_implicit_invocation": False}},
        }
    )
    provider = _CatalogFollowingProvider()
    context = assistant.ModelTurnContext(
        model="test-model",
        provider_key="",
        transport="anthropic",
        base_url="",
        provider=provider,
    )
    monkeypatch.setattr(assistant, "resolve_model_context", lambda _model: context)

    response = await chat_mod.chat(
        ChatRequest(
            session_id="structured-multiple",
            messages=[{"role": "user", "content": "Build it."}],
            skills=["orbital-checksum", "report-format", "orbital-checksum"],
        ),
        make_request(),
    )

    assert response.status_code == 202
    first_messages = provider.calls[0]["messages"]
    skill_messages = [
        message
        for message in first_messages
        if "SKILL INVOCATION" in json.dumps(message.get("content"))
    ]
    assert skill_messages
    assert all(message["role"] == "user" for message in skill_messages)
    rendered = json.dumps(first_messages)
    assert rendered.index(_INSTRUCTION) < rendered.index("SECOND-DISTINCTIVE-INSTRUCTION")


async def test_direct_skill_instructions_remain_active_on_a_later_user_turn(monkeypatch):
    _skill(manual_only=True)
    provider = _CatalogFollowingProvider()
    context = assistant.ModelTurnContext(
        model="test-model",
        provider_key="",
        transport="anthropic",
        base_url="",
        provider=provider,
    )
    monkeypatch.setattr(assistant, "resolve_model_context", lambda _model: context)

    await _chat(monkeypatch, provider, "structured-followup", "/orbital-checksum")
    calls_after_activation = len(provider.calls)
    await _chat(monkeypatch, provider, "structured-followup", "Continue the workflow.")

    followup_first_call = provider.calls[calls_after_activation]
    assert _INSTRUCTION in json.dumps(followup_first_call["messages"])
    assert _INSTRUCTION not in followup_first_call["system"]
