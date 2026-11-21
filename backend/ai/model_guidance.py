"""Model-family guidance: a few lines of the system prompt only some model families get.

One prompt serves every model, but models fail in different ways. A GPT-family model obeys
its declared function list strictly: when the tool it needs is in the TOOL CATALOG (prose) but
not among its callable functions, it tells the user it cannot do the change instead of loading
the tool with ``find_tools`` (a report on gpt-6-luna, 2026-11-21: asked to correct a reversed
transfer and a duplicate expense, it answered "I can't make the ledger changes from this
session" — ``delete_transactions`` was one ``find_tools`` call away). Claude models, trained
on the deferred-tool pattern, do not need telling.

The block is chosen from the MODEL NAME, never from the transport, so the same model gets the
same guidance through OpenAI, OpenRouter, a compatible endpoint or the Codex CLI. It lives in
the stable (cached) half of the prompt; it is constant per model, so the cache holds.

A family earns an entry only with evidence (``python3 -m backend.evals.tool_discovery``
before and after), the way Hermes keeps model-specific guidance only when its benchmark says
it helps. ADR ``2026-11-21-model-family-guidance-is-chosen-by-model-name``.
"""

from __future__ import annotations

import re

GPT_FAMILY = "gpt"

_GPT_NAME = re.compile(r"^(gpt|o\d)|codex")

GPT_TOOL_LOADING = (
    "LOADING TOOLS — your callable functions are only the loaded part of your toolset; every "
    "tool in the TOOL CATALOG is yours too. When the user asks for a change, make it in this "
    "turn: if the tool you need is not among your callable functions, call "
    'find_tools(names=["<tool>"]) (or find_tools(query="…") when you do not know its name), '
    "then call the tool. Never tell the user you cannot make a change, or that it has to be "
    "done somewhere else, because a tool is not loaded — loading it is always possible. Say you "
    "cannot only after find_tools found no tool that does it."
)

_GUIDANCE: dict[str, str] = {GPT_FAMILY: GPT_TOOL_LOADING}


def model_family(model: str | None) -> str | None:
    """The guidance family of a model id (the turn's bare id, never a ``provider_key:`` ref).

    ``gpt-6-luna``, OpenRouter's ``openai/gpt-6-luna`` and its ``openai/gpt-6-luna:batch``
    variant are one model, so the vendor path and a ``:variant`` suffix are dropped first.
    ``None`` = no family-specific guidance.
    """
    name = (model or "").strip().lower()
    name = name.rsplit("/", 1)[-1].partition(":")[0]
    if name and _GPT_NAME.search(name):
        return GPT_FAMILY
    return None


def guidance_for(model: str | None) -> str:
    """The family block for ``model``, or ``""`` when its family needs none."""
    family = model_family(model)
    return _GUIDANCE.get(family, "") if family else ""
