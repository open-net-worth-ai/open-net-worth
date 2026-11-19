"""Runtime AI provider/model config tools (UI↔Lucius parity for Settings → AI Model).

The Settings → AI Model section (REST ``GET/PUT /api/model-config``) lets a self-hoster
switch which provider + model the assistant itself runs on — including pointing at a LOCAL
OpenAI-compatible endpoint (Ollama). These two tools give Lucius the SAME read + change
ability, thin over ``ModelConfigStore`` and the same two-transport validation
(``VALID_PROVIDERS``) the REST PUT uses.

  * ``get_model_config`` — a pure READ (the active provider/model/base_url + has_api_key +
    the provider catalog). On the headless allowlist.
  * ``update_model_config`` — sets provider/model/base_url. The ``api_key`` is a SECRET:
    accepted as WRITE-ONLY-if-provided (blank/omitted keeps the saved one) and NEVER echoed
    back — but the UI is the normal place to set it. Changing the model the assistant runs on
    is a behavior change, so the prompt confirms intent first; it is NOT on the headless
    ceiling (an unattended turn must not re-point the instance's model).
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of


class GetModelConfigInput(ToolInput):
    """Schema-of-record for ``get_model_config`` (see ``schema_from_model``)."""


@tool(
    "get_model_config",
    description=(
        "Read the AI provider/model config. Returns the INSTANCE DEFAULT (provider, model, "
        "base_url, has_api_key — key never returned), the provider catalog, "
        "configured_providers, and — when called mid-conversation — an 'active_turn' block "
        "with the model ACTUALLY serving this turn (which may differ from the default via a "
        "per-conversation pick, per-agent model, or auto-routing). "
        "When answering 'which model are you / am I on?', report active_turn.model / "
        "active_turn.provider_label if present, and only fall back to the default fields when "
        "it is absent."
    ),
    input_schema=schema_of(GetModelConfigInput, tool_name="get_model_config"),
)
async def get_model_config(input_data: dict):
    from backend.core.model_context import get_active_model
    from backend.db.model_config_store import PROVIDER_CATALOG, ModelConfigStore

    store = ModelConfigStore()
    out = store.public()
    out["catalog"] = PROVIDER_CATALOG
    out["configured_providers"] = store.providers_public()
    # The model actually running THIS turn (composer pick / per-agent / auto-route),
    # which may differ from the instance default above. Absent outside a live turn.
    active = get_active_model()
    if active:
        out["active_turn"] = {
            "model": active.get("model"),
            "provider_key": active.get("provider_key"),
            "provider_label": active.get("provider_label"),
        }
    return json.dumps(out, ensure_ascii=False), None


def _base_url_refusal() -> tuple[str, None]:
    """An endpoint URL (or a CLI provider's binary path, stored in the same field) decides
    where a stored key is sent and what gets executed, so only the owner's own UI sets it."""
    return (
        json.dumps(
            {
                "ok": False,
                "error": (
                    "base_url cannot be changed by the assistant. Ask the user to set the "
                    "endpoint (or CLI path) in Settings → AI Models."
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )


class UpdateModelConfigInput(ToolInput):
    """Schema-of-record for ``update_model_config`` (see ``schema_from_model``)."""

    provider: Optional[Literal["anthropic", "openai_compatible"]] = Field(
        default=None,
        description=(
            "The transport: 'anthropic' = the Claude SDK; 'openai_compatible' = the "
            "OpenAI SDK pointed at any base_url (OpenAI, Gemini, Ollama, or any "
            "OpenAI-compatible endpoint)."
        ),
    )
    model: Optional[str] = Field(
        default=None, description="The model id to run, e.g. 'claude-sonnet-4-6' or 'gpt-4o'."
    )
    api_key: Optional[str] = Field(
        default=None,
        description=(
            "WRITE-ONLY API key — never returned. Blank/omitted keeps the saved key "
            "unchanged. Prefer the Settings → AI Model UI for entering secrets."
        ),
    )


@tool(
    "update_model_config",
    description=(
        "Change which provider/model the assistant runs on. Takes effect next turn — no restart. "
        "api_key is WRITE-ONLY (never returned; blank keeps saved key) — prefer the UI for secrets. "
        "The endpoint URL (base_url) cannot be set here — it is changed in Settings → AI Models only. "
        "Call get_model_config first to see valid providers and model hints."
    ),
    input_schema=schema_of(UpdateModelConfigInput, tool_name="update_model_config"),
    mutates=("config",),
)
async def update_model_config(input_data: dict):
    from backend.ai.tools._shared import no_op_write_result
    from backend.db.model_config_store import ModelConfigStore
    from backend.models.model_config import VALID_PROVIDERS

    if input_data.get("base_url") is not None:
        return _base_url_refusal()
    changes: dict = {}
    for key in ("provider", "model", "api_key"):
        if key in input_data and input_data[key] is not None:
            changes[key] = input_data[key]
    provider = changes.get("provider")
    if provider is not None:
        provider = str(provider).strip().lower()
        if provider not in VALID_PROVIDERS:
            return (
                json.dumps(
                    {"ok": False, "error": f"provider must be one of {list(VALID_PROVIDERS)}"},
                    ensure_ascii=False,
                ),
                None,
            )
        changes["provider"] = provider

    if not changes:
        # No changeable field was provided — ModelConfigStore().update({}) would still
        # rewrite the file byte-identically, and a plain {"ok": True, "config": ...}
        # return is indistinguishable from a real config change. Read-only echo instead.
        public = ModelConfigStore().public()
        return json.dumps(no_op_write_result({"config": public}), ensure_ascii=False), None

    # ModelConfigStore.update never echoes the api_key (only has_api_key) — safe to return.
    public = ModelConfigStore().update(changes)
    return json.dumps({"ok": True, "config": public}, ensure_ascii=False), None


class SetProviderCredentialsInput(ToolInput):
    """Schema-of-record for ``set_provider_credentials`` (see ``schema_from_model``)."""

    provider_key: str = Field(
        description=(
            "The provider catalog key from get_model_config's catalog list: "
            "'anthropic', 'openai', 'gemini', 'ollama', 'xai', 'glm', "
            "'openrouter', 'litellm' (a local LiteLLM proxy), or 'custom' (a "
            "generic OpenAI-compatible endpoint where you also supply base_url)."
        ),
    )
    api_key: Optional[str] = Field(
        default=None,
        description=(
            "WRITE-ONLY API key for this provider — never returned. "
            "Blank/omitted keeps the saved key unchanged. "
            "Prefer asking the user to enter it in Settings → AI Model."
        ),
    )


@tool(
    "set_provider_credentials",
    description=(
        "Store the api_key for a secondary AI provider (for per-conversation overrides). "
        "Does NOT change the instance default (use update_model_config for that). "
        "api_key is WRITE-ONLY. The endpoint URL (base_url) cannot be set here — it is changed "
        "in Settings → AI Models only, because a stored key is bound to its endpoint. NOT headless."
    ),
    input_schema=schema_of(SetProviderCredentialsInput, tool_name="set_provider_credentials"),
    mutates=("config",),
)
async def set_provider_credentials(input_data: dict):
    from backend.ai.tools._shared import no_op_write_result
    from backend.db.model_config_store import ModelConfigStore, _provider_entry

    provider_key = str(input_data.get("provider_key") or "").strip()
    if not provider_key:
        return json.dumps(
            {"ok": False, "error": "provider_key is required"}, ensure_ascii=False
        ), None
    if _provider_entry(provider_key) is None:
        return (
            json.dumps(
                {"ok": False, "error": f"unknown provider key: {provider_key!r}"},
                ensure_ascii=False,
            ),
            None,
        )
    if input_data.get("base_url") is not None:
        return _base_url_refusal()
    changes: dict = {}
    if input_data.get("api_key") is not None:
        changes["api_key"] = input_data["api_key"]

    if not changes:
        # No credential field was provided — update_provider({}) would still perform a
        # disk write for an entry that isn't changing, and a plain {"ok": True, ...}
        # return is indistinguishable from a real credential save. Read-only echo instead.
        configured = ModelConfigStore().providers_public()
        return (
            json.dumps(
                no_op_write_result(
                    {"provider_key": provider_key, "configured_providers": configured}
                ),
                ensure_ascii=False,
            ),
            None,
        )

    # update_provider returns the public providers map — api_key is NEVER included.
    providers_public = ModelConfigStore().update_provider(provider_key, changes)
    return (
        json.dumps(
            {"ok": True, "provider_key": provider_key, "configured_providers": providers_public},
            ensure_ascii=False,
        ),
        None,
    )
