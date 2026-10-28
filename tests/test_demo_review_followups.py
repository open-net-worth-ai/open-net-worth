"""Second security review of the passwordless demo: the doors the route lockdown left open.

1. A visitor could name any model (or provider) on the operator's key — token budgets bound
   tokens, not price. Every call is now pinned to the instance model at ≤ medium effort.
2. Lucius's own tools reached what the routes lock (other visitors' checkpoints, logs, shared
   skills/automations, outbound messages, instance config).
3. Chat suggestions read any conversation by id.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from starlette.requests import Request

import backend.core.config as config
from backend.ai import assistant
from backend.ai.tools import registry
from backend.api import chat_suggestions
from backend.db import sql_console
from backend.services import demo_quota, demo_visitors

_ALICE = "a" * 32
_BOB = "b" * 32
_ENV = ("DEMO_MODE", "DEMO_OPEN_ACCESS", "DEMO_TRUSTED_PROXY_HOPS")


def _reset():
    config._settings = None
    demo_quota.reset()
    demo_visitors.reset()


@pytest.fixture
def demo(monkeypatch, db_isolated):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_OPEN_ACCESS", "1")
    monkeypatch.setenv("AUTH_PASSWORD", "unpublished-random-secret-for-this-test")
    _reset()
    yield
    _reset()


@pytest.fixture
def not_demo(monkeypatch, db_isolated):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    _reset()
    yield
    _reset()


class _FakeProvider:
    def __init__(self, transport):
        self.transport = transport
        self.calls = []

    async def complete(self, prompt, system="", model="", **kwargs):
        self.calls.append(("complete", model, kwargs.get("effort")))
        return "ok"

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.calls.append(("stream_chat", model, effort))
        yield {"type": "done"}


@pytest.fixture
def instance_model(monkeypatch):
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setattr(
        ModelConfigStore,
        "resolved",
        lambda self: {
            "provider": "openai_compatible",
            "base_url": "https://models.example",
            "api_key": "operator-key",
            "model": "cheap-default",
        },
    )
    built = []

    def fake_build(*, transport, base_url, api_key):
        provider = _FakeProvider(transport)
        built.append(provider)
        return provider

    monkeypatch.setattr(assistant, "_build_provider", fake_build)
    return built


def test_demo_runs_every_call_on_the_instance_model_at_most_medium_effort(demo, instance_model):
    provider = assistant._provider_from_resolved(
        transport="claude_cli", base_url="/usr/local/bin/claude", api_key=""
    )
    assert instance_model[0].transport == "openai_compatible"

    async def run():
        await provider.complete("hi", model="openrouter:the-dearest-model", effort="max")
        async for _ in provider.stream_chat([], [], "", "the-dearest-model", effort="high"):
            pass

    asyncio.run(run())
    assert instance_model[0].calls == [
        ("complete", "cheap-default", "medium"),
        ("stream_chat", "cheap-default", "medium"),
    ]


def test_off_a_demo_the_requested_provider_and_model_are_used(not_demo, instance_model):
    provider = assistant._provider_from_resolved(transport="anthropic", base_url="", api_key="k")
    assert instance_model[0].transport == "anthropic"
    asyncio.run(provider.complete("hi", model="chosen", effort="max"))
    assert instance_model[0].calls == [("complete", "chosen", "max")]


@pytest.mark.parametrize(
    "tool",
    [
        "list_checkpoints",
        "restore_checkpoint",
        "get_recent_logs",
        "create_skill",
        "create_automation",
        "run_automation",
        "email_send_message",
        "update_model_config",
    ],
)
def test_demo_refuses_tools_that_reach_past_the_book(demo, tool):
    short_circuit, _ = registry._permission_gate(tool, {})
    assert short_circuit is not None
    text, proposed = short_circuit
    assert proposed is None
    assert "turned off in the public demo" in json.loads(text)["error"]


def test_every_demo_refused_tool_is_a_real_tool():
    import backend.ai.tools  # noqa: F401 — registers every tool

    assert registry._DEMO_REFUSED_TOOLS <= set(registry.registered_tool_names())


def test_off_a_demo_those_tools_are_not_demo_refused(not_demo):
    short_circuit, _ = registry._permission_gate("list_checkpoints", {})
    if short_circuit is not None:
        assert "public demo" not in (short_circuit[0] or "")


def _request_as(visitor: str) -> Request:
    cookie = f"{demo_visitors.COOKIE}={visitor}".encode()
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat/suggestions",
            "headers": [(b"cookie", cookie)],
            "client": ("203.0.113.9", 1234),
            "query_string": b"",
        }
    )


def test_suggestions_never_read_another_visitors_conversation(demo, monkeypatch):
    demo_visitors.claim("alice-chat", f"c:{_ALICE}")
    monkeypatch.setattr(assistant, "_load_session", lambda sid: [{"role": "user"}])
    monkeypatch.setattr(chat_suggestions, "_last_exchange", lambda h: ("SECRET Q", "SECRET A"))
    monkeypatch.setattr(chat_suggestions, "read_profile", lambda: {})
    prompts = []

    async def fake_generate(system, tool, prompt):
        prompts.append(prompt)
        return ["follow-up"]

    monkeypatch.setattr(chat_suggestions, "_generate", fake_generate)
    chat_suggestions._STARTER_CACHE.update(value=["starter"], at=time.time())
    req = chat_suggestions.SuggestionsRequest(session_id="alice-chat")

    bob = asyncio.run(chat_suggestions.chat_suggestions(req, _request_as(_BOB)))
    assert bob == {"suggestions": ["starter"]}
    assert prompts == []

    alice = asyncio.run(chat_suggestions.chat_suggestions(req, _request_as(_ALICE)))
    assert alice == {"suggestions": ["follow-up"]}
    assert "SECRET Q" in prompts[0]


def test_demo_sql_cannot_read_the_cost_log_or_telemetry(demo):
    denied = sql_console.denied_tables()
    assert {"cost_log", "cost_log_daily", "model_telemetry"} <= denied


def test_demo_sql_cannot_read_other_visitors_conversations(demo):
    assert {"sessions", "session_messages"} <= sql_console.denied_tables()


def test_an_operator_can_read_their_own_conversations(not_demo):
    assert not ({"sessions", "session_messages"} & sql_console.denied_tables())
