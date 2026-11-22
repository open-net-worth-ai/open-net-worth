"""Integration wiring: per-task model assignments reach the job entry points."""

from __future__ import annotations

import pytest

from backend.db.model_config_store import ModelConfigStore


@pytest.fixture
def store():
    return ModelConfigStore()


def test_task_model_override_raw_accessor():
    s = ModelConfigStore()
    assert s.task_model_override("memory") is None
    s.update_provider("anthropic", {"api_key": "sk-ant"})
    s.set_task_model("memory", "anthropic:claude-haiku-4-5")
    assert s.task_model_override("memory") == "anthropic:claude-haiku-4-5"
    s.set_task_model("memory", None)
    assert s.task_model_override("memory") is None


def test_memory_model_uses_assignment(store, monkeypatch):
    store.update_provider("anthropic", {"api_key": "sk-ant"})
    store.set_task_model("memory", "anthropic:claude-haiku-4-5")
    from backend.ai.memory import _memory_model

    assert _memory_model() == "claude-haiku-4-5"


def test_memory_model_default_when_unassigned(store):
    from backend.ai.memory import MEMORY_MODEL, _memory_model

    assert _memory_model() == MEMORY_MODEL


def test_intel_model_uses_assignment(store):
    store.update_provider("anthropic", {"api_key": "sk-ant"})
    store.set_task_model("intel", "anthropic:claude-haiku-4-5")
    from backend.monitoring.intel_agent import _intel_model

    assert _intel_model() == "claude-haiku-4-5"


def test_intel_model_keeps_the_provider_of_a_non_default_assignment(store):
    # The default is Anthropic (conftest). A bare id would run on it — with the Claude Code
    # CLI as default that was `claude --model <id>` and "There's an issue with the selected
    # model", so every intel scan returned 0 stories. The ref must keep its provider.
    store.update_provider("openai", {"api_key": "sk-oai"})
    store.set_task_model("intel", "openai:gpt-4o-mini")
    from backend.monitoring.intel_agent import _intel_model

    assert _intel_model() == "openai:gpt-4o-mini"


def test_suggestions_honors_non_anthropic_assignment(store):
    # Suggestions now route through the provider-agnostic llm_seam, so a non-Anthropic
    # assignment is HONORED (returns the compound ref for the seam to resolve), not
    # skipped-and-rerouted-to-the-instance-default-on-the-Anthropic-client (the 404 bug).
    store.update_provider("openai", {"api_key": "sk-oai"})
    store.set_task_model("suggestions", "openai:gpt-4o-mini")
    from backend.api.chat_suggestions import _suggestions_model

    assert _suggestions_model() == "openai:gpt-4o-mini"


def test_suggestions_uses_anthropic_assignment(store):
    store.update_provider("anthropic", {"api_key": "sk-ant"})
    store.set_task_model("suggestions", "anthropic:claude-haiku-4-5")
    from backend.api.chat_suggestions import _suggestions_model

    # The compound ref is passed to the seam (which resolves the provider), not a bare id.
    assert _suggestions_model() == "anthropic:claude-haiku-4-5"


def test_suggestions_no_assignment_uses_instance_default(store):
    # No task model → '' → the seam uses the instance default provider+model.
    from backend.api.chat_suggestions import _suggestions_model

    assert _suggestions_model() == ""


def test_suggestions_generate_routes_through_seam(store, monkeypatch):
    """_generate goes through the provider-agnostic llm_seam.complete and parses the
    JSON reply — regression for the Anthropic-only 404 when the default is non-Anthropic."""
    import asyncio

    from backend.api import chat_suggestions
    from backend.core import llm_seam

    seen = {}

    async def fake_complete(prompt, *, system="", model="", max_tokens=2048, source="x", **kw):
        seen["source"] = source
        return '{"suggestions": ["Am I diversified?", "How is my cash?", "  ", "Next?"]}'

    monkeypatch.setattr(llm_seam, "complete", fake_complete)
    out = asyncio.run(chat_suggestions._generate("sys", chat_suggestions._STARTER_TOOL, "prompt"))
    # Empties dropped, capped at 4 — and cost is attributed via the 'chat_suggestions' source.
    assert out == ["Am I diversified?", "How is my cash?", "Next?"]
    assert seen["source"] == "chat_suggestions"
