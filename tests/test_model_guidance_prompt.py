"""A GPT-family model is told, in its own prompt block, to load a tool rather than give up.

Report 2026-11-21 (gpt-6-luna): asked to correct a reversed transfer and remove a duplicate
expense, Lucius answered "I can't make the ledger changes from this session".
``delete_transactions`` was in the TOOL CATALOG, one ``find_tools`` call away, but not among
the functions the model could call, and a GPT-family model treats its function list as the
whole of what it can do. The block is chosen by the model NAME, so every transport serving
that model gets it, and no other family pays for it.
"""

from __future__ import annotations

import pytest

import backend.ai.tools  # noqa: F401  (populates the tool registry the prompt renders)
from backend.ai.model_guidance import GPT_FAMILY, GPT_TOOL_LOADING, guidance_for, model_family
from backend.ai.prompts import build_system_prompt


def _text(prompt) -> str:
    return prompt if isinstance(prompt, str) else "".join(str(part) for part in prompt)


@pytest.mark.parametrize(
    "model",
    [
        "gpt-6-luna",
        "GPT-6-Luna",
        "openai/gpt-6-luna",
        "openai/gpt-6-luna:batch",
        "~openai/gpt-luna-latest",
        "gpt-5.3-codex",
        "openai/gpt-oss-120b",
        "o3",
        "openai/o4-mini-high",
    ],
)
def test_gpt_family_models_are_recognised_whatever_the_route(model):
    assert model_family(model) == GPT_FAMILY


@pytest.mark.parametrize(
    "model",
    [
        "",
        None,
        "sonnet",
        "claude-sonnet-5-5",
        "anthropic/claude-opus-5.5",
        "z-ai/glm-5.3-flash",
        "google/gemini-4-argon",
        "openrouter/auto",
        "llama3.1",
        "qwen/qwen3.8-max-prime",
    ],
)
def test_other_families_get_no_block(model):
    assert model_family(model) is None
    assert guidance_for(model) == ""


def test_a_gpt_turn_prompt_carries_the_block():
    text = _text(build_system_prompt(model="gpt-6-luna", base_url="https://api.openai.com/v1"))
    assert GPT_TOOL_LOADING in text


def test_a_claude_turn_prompt_does_not():
    text = _text(build_system_prompt(model="sonnet", base_url=""))
    assert GPT_TOOL_LOADING not in text


def test_the_block_sits_in_the_cached_half_with_the_catalog_it_points_at():
    """Constant per model, so it belongs before the cache split, after the catalog it names."""
    from backend.ai.prompts import CACHE_SPLIT_MARKER

    text = _text(build_system_prompt(model="openai/gpt-6-luna", base_url=""))
    stable = text.split(CACHE_SPLIT_MARKER, 1)[0]
    assert GPT_TOOL_LOADING in stable
    assert stable.index("TOOL CATALOG") < stable.index(GPT_TOOL_LOADING)


def test_the_block_names_the_loader_and_forbids_the_give_up():
    assert 'find_tools(names=["<tool>"])' in GPT_TOOL_LOADING
    assert "Never tell the user you cannot make a change" in GPT_TOOL_LOADING
