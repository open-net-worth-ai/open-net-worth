"""ModelConfigStore — runtime AI provider/model config.

A single dict (the ``"model_config"`` namespace row in the ``secret_config`` table)
holding the instance default (active provider + model) PLUS optional per-provider
credentials so different conversations and agents can run on different providers
simultaneously:

    {
        "provider": "anthropic",        # instance-default transport
        "model":    "claude-opus-5",    # instance-default model
        "base_url": "",                 # instance-default base_url (display-only after migration)
        "providers": {                  # credential map, keyed by PROVIDER KEY
            "anthropic": {"api_key": "sk-ant-…"},
            "openai":    {"api_key": "sk-openai-…"},
            "ollama":    {"base_url": "http://localhost:11434/v1"}
        }
    }

The ``providers`` map is keyed by PROVIDER KEY — the ``key`` of a ``PROVIDER_CATALOG``
entry (``anthropic``, ``openai``, ``gemini``, ``ollama``, ``xai``, ``glm``,
``openrouter``, ``litellm``, ``custom``). ``custom`` is the generic OpenAI-compatible escape hatch
(user supplies the base_url) and doubles as the storage bucket for any endpoint that
matches no other preset. Only providers with a non-empty api_key or non-default
base_url need an entry; the rest are omitted.

**Migration.** On first read of a legacy config that carries a top-level ``api_key``
but no matching entry in ``providers``, the key is silently moved into the
``providers`` map and the row is re-saved.  After migration ``resolved()`` reads
credentials exclusively from the ``providers`` map; the legacy top-level ``api_key``
field is ignored for credential resolution (kept stored for display compat only).

**Resolution for an override.** A per-conversation or per-agent model override is
stored as ``"<provider_key>:<model_id>"`` (e.g. ``"anthropic:claude-haiku-4-5"``)
so the provider is pinned at save time and immune to global Settings changes.
Plain ``"<model_id>"`` strings (no colon) fall back to the instance default provider
for unrecognised local model names (e.g. ``"llama3.1"`` on Ollama).
``ModelConfigStore.resolve_override(override)`` returns ``(transport, base_url,
api_key, model_id)`` — the fully resolved tuple the runtime needs.

This deliberately mirrors the ``EmailConfigStore`` / ``AppConfigStore`` pattern:

  - env-seeded on first read → the store is then the single source of truth;
  - api_key fields are stored but NEVER returned to the frontend —
    ``public()`` exposes only ``has_api_key``; a blank key in an update leaves the
    saved one unchanged;
  - runtime state, checkpoint-EXCLUDED, SECRET (holds per-provider API keys).

WAVE 5D2a (JSON->DB): replaces the hand-rolled ``model_config.json`` file I/O with a
row in the ``secret_config`` table, namespace ``"model_config"`` — checkpoint-EXCLUDED
(``ai.checkpoint._policy._EXCLUDED_TABLES``, via the whole ``secret_config`` table),
SECRET and NON-RESTORABLE (``core.sensitive`` — likewise via ``secret_config``'s
existing membership; no new entry needed). Diff is MINIMAL: only ``__init__``/
``_read_raw``/``_write_raw`` change; every migration/resolution/task-model/
vision-capability method is byte-identical to before. The seven write call sites
that used to duplicate ``atomic_write_text(...) + chmod(0o600)`` inline now all
route through the one ``_write_raw`` helper (``update()`` already did) — the DB has
no file to ``atomic_write_text`` into, so every one of those sites had to change
regardless; consolidating them onto one write path is the natural consequence, not
scope creep.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

from backend.core.config import get_settings
from backend.core.errors import ConfigurationError
from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import secret_config
from backend.models.model_config import VALID_PROVIDERS

# Background tasks that can have an independent model assignment.
VALID_TASKS: frozenset[str] = frozenset(
    {
        "memory",
        "intel",
        "suggestions",
        "briefing",
        # "news_screen" (INT5): the significance-screen consumer's cheap headline-only materiality
        # call — deliberately its OWN task, not reusing "intel" (a much larger/pricier call), so it
        # can be pinned to a cheaper model independently.
        "news_screen",
        # "analysis" (INT19-follow-up, owner-diagnosed 2026-08-25): the Analyst sweep's full
        # synthesis call (`monitoring/analyst.py::_process_row`) — the "why"/impact/actions/
        # decision_relevant verdict AND the actual pushed-alert prose, the highest-stakes
        # structured-output call in the Intel pipeline. `analyst._analysis_model()` has ALWAYS
        # called `task_model_override("analysis")` — but "analysis" was never added to this
        # set, so the call silently, permanently returned None (`task_model_override` doesn't
        # validate against VALID_TASKS, per its own docstring) and every analysis ran on
        # whatever the INSTANCE DEFAULT provider/model happened to be, invisibly, with no field
        # in `TaskModelsUpdate` and no row in the Settings panel to ever let anyone pin it —
        # textbook instance of the exact failure class `tests/test_task_models_coverage.py`'s
        # own docstring describes for `news_screen`'s 2026-07-31 incident, just never closed for
        # THIS task. Live symptom: the instance default resolved to `claude_cli:sonnet`
        # (a full agentic CLI subprocess, not a lean completion endpoint) with reasoning
        # enabled — measured 32% of analysis calls in a 26h window failing with
        # `error_max_structured_output_retries` (the CLI's own structured-output retry budget
        # exhausted, `num_turns=6` every time, `thinking_tokens` often over half the output
        # budget), matching a 42% failure rate independently measured in 3 days of real
        # production `pending_analysis` rows. `_ANALYSIS_MAX_TOKENS` (INT17's fix for a
        # DIFFERENT provider's truncation) has NO effect on this transport — the CLI accepts no
        # output-token cap. Registering the task here is the actual fix: it makes "analysis"
        # poll-able/pinnable exactly like `news_screen`, so it can be routed off the CLI
        # transport onto a reliable completion-style provider.
        "analysis",
        # "price_monitor": narration of a price alert whose firing was already
        # decided by a deterministic threshold comparison — the model only writes
        # it up, so it does not need a flagship. Was hardcoded to a Sonnet-class
        # model AND to the Anthropic provider (a direct client, bypassing the
        # seam), so it could not be re-routed at all until it was added here.
        "price_monitor",
        # ONE key for the whole statement-import pipeline (owner, 2026-08-09: "merge all of
        # these into one settings only"). It was five — csv_schema, statement_parse,
        # statement_vision, statement_metadata, statement_lucius_pass — on the theory that an
        # owner would route cheap schema inference and strong vision independently. In
        # practice they were set to the same model every time, so five rows of settings bought
        # nothing but five ways to get it inconsistent. The stages still report their own
        # `source` for COST attribution; only the model knob merged.
        #
        # Vision is the one stage that can't simply inherit it: a scan needs a model that can
        # see. Rather than a second knob, it falls back to the instance default image model —
        # see `vision_capability.resolve_extractor_ref`.
        "statement_import",
        # "chat_vision" (ADR 2026-07-26): reads an image attached to a CHAT turn whose own
        # model has no vision. Its OWN task, not a reuse of statement_vision, for the same
        # reason news_screen isn't a reuse of intel — different prompt, different size, and
        # a user who routes bulk statement pages to a cheap model may well want the one-off
        # receipt they just photographed read by a better one (or vice versa).
        "chat_vision",
        # "title": names a new in-app conversation from its opening message
        # (`ai/session_titles.py`). A ~200-token call, so the instance default is fine
        # unpinned; pin a fast model if the default is a slow CLI transport.
        "title",
        # "approval": the Auto-approval judge (`ai/auto_judge.py`) — decides per call whether
        # Auto mode must ask the user. Unpinned = the conversation's own model, not the
        # instance default (resolved by the judge, not here).
        "approval",
    }
)

# Well-known OpenAI-compatible base URLs, surfaced as UI hints (NOT defaults — the
# user must pick one). These are the common local / aggregator endpoints.
PROVIDER_HINTS: dict = {
    "anthropic": {
        "label": "Anthropic",
        "needs_base_url": False,
        "base_url_hints": [],
        "model_hints": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
    },
    "openai_compatible": {
        "label": "OpenAI-compatible (OpenAI, Ollama, LM Studio, OpenRouter, vLLM)",
        "needs_base_url": True,
        "base_url_hints": [
            {"label": "OpenAI", "base_url": "https://api.openai.com/v1"},
            {"label": "Ollama (local)", "base_url": "http://localhost:11434/v1"},
            {"label": "LM Studio (local)", "base_url": "http://localhost:1234/v1"},
            {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
        ],
        "model_hints": ["gpt-5", "gpt-5-mini", "llama3.1", "qwen2.5"],
    },
    "claude_cli": {
        "label": "Claude Code CLI (uses your Claude subscription)",
        "needs_base_url": False,
        "base_url_hints": [],
        "model_hints": ["sonnet", "opus", "haiku"],
    },
    "codex_cli": {
        "label": "Codex CLI (uses your ChatGPT subscription)",
        "needs_base_url": False,
        "base_url_hints": [],
        "model_hints": ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
    },
    "antigravity_cli": {
        "label": "Antigravity CLI (uses your Google Antigravity account)",
        "needs_base_url": False,
        "base_url_hints": [],
        "model_hints": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.7-flash"],
    },
}

# Presentation catalog — named provider presets the UI shows in the provider
# <Select>. Each entry maps a user-visible label to the underlying TRANSPORT
# (``anthropic`` | ``openai_compatible``) + the default base_url for that provider.
# ORDER matters: the UI renders them in this order.
#
# ``key``             — unique catalog id (used as the per-provider config key in
#                       the ``providers`` map AND by the frontend to infer the active
#                       entry from a saved {provider, base_url} pair)
# ``label``           — user-facing name shown in the <Select>
# ``transport``       — the real implementation: ``anthropic`` or ``openai_compatible``
# ``base_url``        — default base URL (empty string for Anthropic / custom)
# ``needs_api_key``   — whether an API key is required / expected
# ``editable_base_url`` — True only for entries where the user must supply a URL
# ``model_hints``     — suggested model names for the <datalist>
# ``cli_binary``      — LOCAL-CLI transports only: the executable to resolve on PATH when
#                       the user leaves the (repurposed) base_url blank. Its presence is
#                       what marks an entry as a CLI transport — every consumer asks the
#                       catalog instead of keeping its own list of CLI keys.
PROVIDER_CATALOG: list = [
    {
        "key": "anthropic",
        "label": "Anthropic",
        "transport": "anthropic",
        "base_url": "",
        # PRESENTATION ONLY — never used for resolution. `base_url` must stay "" because
        # _key_for() matches a stored config back to its catalog entry on (transport, base_url),
        # and every saved Anthropic config carries "". But a read-only Endpoint field rendered
        # from "" is just an empty box, which reads as "something is missing" rather than
        # "this endpoint is fixed". This is the host the SDK actually targets:
        # anthropic_provider.py builds AsyncAnthropic(api_key=...) with no override, and the
        # SDK's own default is https://api.anthropic.com.
        "display_base_url": "https://api.anthropic.com",
        "needs_api_key": True,
        "editable_base_url": False,
        # Current ids, most-recommended FIRST — the first entry is what first run prefills.
        # Refreshed 2026-10-04 (was 2026-08-24, and already a generation behind): Opus 5.5 /
        # Sonnet 5.5 / Fable 5.1 exist; ids checked against LiteLLM's public price list.
        "model_hints": [
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-4-5",
            "claude-fable-5-1",
        ],
    },
    {
        # The locally-installed Claude Code headless CLI (`claude -p`). Spends the
        # user's EXISTING Claude Pro/Max subscription instead of API credits, so there
        # is no api_key to store. ``base_url`` is repurposed as the CLI PATH (blank =
        # resolve `claude` on PATH) — it is the one free-form per-provider string the
        # store already round-trips, and "where is the endpoint" is exactly what it
        # means for the other two transports.
        # RFC: .claude/rfcs/2026-07-22-claude-code-cli-provider.md
        "key": "claude_cli",
        "label": "Claude Code CLI",
        "transport": "claude_cli",
        "base_url": "",
        "needs_api_key": False,
        "editable_base_url": True,
        "cli_binary": "claude",
        # CLI aliases resolve to the current model of that tier — deliberately preferred
        # over pinned ids so a self-hoster tracks the CLI's own defaults.
        "model_hints": [
            "sonnet",
            "opus",
            "haiku",
            "claude-opus-4-8",
            "claude-sonnet-5",
            "claude-haiku-4-5",
            "claude-fable-5",
        ],
    },
    {
        "key": "openai",
        "label": "OpenAI",
        "transport": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "needs_api_key": True,
        "editable_base_url": False,
        # Refreshed 2026-10-04 (was 2026-08-24, a family behind): every id here must work on
        # /v1/chat/completions, which is all this provider calls. `gpt-5-pro` / `gpt-5.5-pro`
        # are Responses-API-only, so they are NOT hints — and the vendored price map does not
        # record the endpoint, so check LiteLLM's `supported_endpoints` before adding an id.
        "model_hints": [
            "gpt-6.1-sol",
            "gpt-6-luna",
            "gpt-6-astra",
            "gpt-5.6-terra",
            "gpt-5.5",
        ],
    },
    {
        # The locally-installed Codex CLI (`codex exec`) — the OpenAI twin of the
        # claude_cli entry above. Spends the user's EXISTING ChatGPT Plus/Pro/Business
        # plan via `codex login` instead of API credits, so there is no api_key to store,
        # and ``base_url`` is repurposed as the CLI PATH (blank = resolve `codex` on PATH).
        # RFC: .claude/rfcs/2026-07-22-codex-cli-provider.md
        "key": "codex_cli",
        "label": "Codex CLI",
        "transport": "codex_cli",
        "base_url": "",
        "needs_api_key": False,
        "editable_base_url": True,
        "cli_binary": "codex",
        # Seeds only — replaced by the CLI's OWN catalog ($CODEX_HOME/models_cache.json)
        # as soon as it is readable; see ai/models/codex_cli_catalog.py.
        "model_hints": ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
    },
    {
        # The locally-installed Antigravity CLI (`agy -p`). Spends the user's
        # EXISTING Google Antigravity account instead of API credits, so there is
        # no api_key to store, and ``base_url`` is repurposed as the CLI PATH
        # (blank = resolve `agy` on PATH).
        "key": "antigravity_cli",
        "label": "Antigravity CLI",
        "transport": "antigravity_cli",
        "base_url": "",
        "needs_api_key": False,
        "editable_base_url": True,
        "cli_binary": "agy",
        "model_hints": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.7-flash"],
    },
    {
        # Google Gemini via its OpenAI-compatibility endpoint — no native adapter needed.
        "key": "gemini",
        "label": "Google Gemini",
        "transport": "openai_compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "needs_api_key": True,
        "editable_base_url": False,
        # Refreshed 2026-10-04: gemini-2.0-flash is gone from LiteLLM's list. The GA Pro stays
        # first (what first run prefills); 3.1 Pro is still a preview.
        "model_hints": [
            "gemini-2.5-pro",
            "gemini-3.1-pro-preview",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
        ],
    },
    {
        "key": "ollama",
        "label": "Ollama (local)",
        "transport": "openai_compatible",
        "base_url": "http://localhost:11434/v1",
        "needs_api_key": False,
        "editable_base_url": True,
        "model_hints": ["llama3.1", "qwen2.5", "mistral"],
    },
    {
        # xAI Grok via its OpenAI-compatibility endpoint for interactive chat — no
        # native adapter needed there. Its BATCH API is a different shape though
        # (requires `name`, refuses `endpoint`/`completion_window` — see the 2026-07-15
        # incident); ai/batch/xai_batch.py + ai/batch/routing.py route batch calls to a
        # dedicated adapter keyed off this exact base_url instead of assuming
        # openai_batch's OpenAI-native shape applies.
        "key": "xai",
        "label": "xAI (Grok)",
        "transport": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
        "needs_api_key": True,
        "editable_base_url": False,
        # Seeds only — replaced by the live /v1/models list once a key is saved.
        "model_hints": ["grok-4.5", "grok-4.3", "grok-4.20-0309-reasoning"],
    },
    {
        # Zhipu GLM via its OpenAI-compatible endpoint. Default is the international
        # (api.z.ai) host; base_url is editable so China users can point at
        # https://open.bigmodel.cn/api/paas/v4/ instead.
        "key": "glm",
        "label": "Zhipu (GLM)",
        "transport": "openai_compatible",
        "base_url": "https://api.z.ai/api/paas/v4/",
        "needs_api_key": True,
        "editable_base_url": True,
        # Seeds only — replaced by the live /v1/models list once a key is saved.
        "model_hints": ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-4.5-air"],
    },
    {
        # OpenRouter aggregator — one key, many upstream models (namespaced ids).
        "key": "openrouter",
        "label": "OpenRouter",
        "transport": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "needs_api_key": True,
        # Listing models does NOT authenticate here: /models is public and answers 200 with
        # the full catalogue for any key at all, so a random string validated cleanly and
        # only failed on the first real request. This endpoint requires the credential.
        "auth_probe_path": "/key",
        "editable_base_url": False,
        # A FLOOR, not a catalogue: OpenRouter serves hundreds and first run now fetches the
        # real list once the key is stored. These only show when that fetch cannot run.
        # Refreshed 2026-10-04 (was 2026-08-24) — every id checked against OpenRouter's public
        # model list. The first entry is what first run prefills.
        "model_hints": [
            "anthropic/claude-opus-5.5",
            "anthropic/claude-sonnet-5.5",
            "openai/gpt-6.1-sol",
            "z-ai/glm-5.3-flash",
            "deepseek/deepseek-chat",
        ],
    },
    {
        # LiteLLM proxy (the local AI-gateway) — an OpenAI-compatible server that fans
        # out to 100+ providers. A user who wants LiteLLM's breadth runs the proxy and
        # points us at it (BYO gateway; we take no litellm dependency). Default port
        # 4000; models are the aliases defined in the user's LiteLLM config.
        "key": "litellm",
        "label": "LiteLLM Proxy (local)",
        "transport": "openai_compatible",
        "base_url": "http://localhost:4000",
        "needs_api_key": True,
        # ...but only if the proxy was started with a master key. A local LiteLLM commonly
        # runs open, so REQUIRING one blocked a perfectly good setup. The field is offered;
        # nothing refuses to continue without it.
        "optional_api_key": True,
        "editable_base_url": True,
        "model_hints": [],
    },
    {
        # Generic escape hatch for ANY OpenAI-compatible endpoint (DeepSeek, Moonshot,
        # vLLM, LM Studio, self-hosted gateways). The user supplies the base_url.
        # NOTE: "custom" is also the internal storage sentinel _infer_provider_key
        # returns for an unmatched openai_compatible base_url — promoting it to a real
        # catalog entry makes that sentinel visible/configurable and lets such an
        # instance-default resolve instead of returning None.
        "key": "custom",
        "label": "Custom (OpenAI-compatible)",
        "transport": "openai_compatible",
        "base_url": "",
        "needs_api_key": True,
        "editable_base_url": True,
        "model_hints": [],
    },
]

# Index for fast catalog lookups (built once at module load).
_PROVIDERS_BY_KEY: dict[str, dict] = {e["key"]: e for e in PROVIDER_CATALOG}


def _provider_entry(key: str) -> Optional[dict]:
    """Return the catalog entry for a key, or None if unknown."""
    return _PROVIDERS_BY_KEY.get(key)


def _stored_base_url(entry: dict, creds: dict) -> str:
    """The base_url a provider's stored creds may contribute. A preset whose endpoint is not
    editable is pinned to its catalog URL: a stored override is ignored, so a poisoned or
    stale row can never point a preset's key at another host."""
    if not entry.get("editable_base_url"):
        return entry["base_url"]
    return (creds.get("base_url") or "").strip() or entry["base_url"]


def _drop_key_if_rebound(entry: dict, creds: dict, new_url: str) -> None:
    """A stored api_key belongs to the endpoint it was saved against. Moving the endpoint
    without supplying a key in the same write clears it, so a new URL can never receive the
    old secret."""
    if new_url != _stored_base_url(entry, creds):
        creds.pop("api_key", None)


def provider_entry(key: str) -> Optional[dict]:
    """Public accessor for one catalog entry — for callers outside this module that need to
    map a provider KEY onto its label/transport (e.g. the cost service attributing spend)."""
    return _PROVIDERS_BY_KEY.get(key)


# Catalog keys whose transport is a locally-installed CLI, in catalog order.
CLI_PROVIDER_KEYS: tuple[str, ...] = tuple(
    e["key"] for e in PROVIDER_CATALOG if e.get("cli_binary")
)


def cli_binary_path(provider_key: str, cli_path: str = "") -> str:
    """The ABSOLUTE path a local-CLI provider resolves to on THIS machine, or "".

    ``shutil.which`` already computes this; ``cli_is_installed`` used to throw it away and
    return only a bool, so the Settings row could say "installed" but not WHERE — while every
    URL-based provider showed its endpoint. Same question, same answer shape.
    """
    entry = _provider_entry(provider_key)
    binary = (entry or {}).get("cli_binary") or ""
    if not binary:
        return ""
    return shutil.which((cli_path or "").strip() or binary) or ""


def cli_is_installed(provider_key: str, cli_path: str = "") -> bool:
    """True when a local-CLI provider's binary resolves on THIS machine.

    ``cli_path`` is the provider's stored ``base_url`` — repurposed as the CLI PATH on
    these transports, where blank means "resolve the catalog's binary name on PATH".

    This is a PATH lookup, not an execution: it is cheap enough to run on every model-list
    request. Whether the binary is new enough to actually use is a separate, more expensive
    question answered by the version probe in ``api/models._probe_cli``.
    """
    return bool(cli_binary_path(provider_key, cli_path))


def _refuse_on_demo() -> None:
    """A public demo's visitors are all the operator; none may move or replace its AI
    credentials (a repointed base_url would carry the stored key to the visitor's server).
    The demo's own host changes them from a shell with DEMO_MODE=0."""
    if get_settings().demo_mode:
        raise ConfigurationError("AI provider settings are locked on the public demo.")


class ModelConfigStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("model_config", table=secret_config, engine=engine)

    def _read_raw(self) -> dict:
        """The stored object, with both migrations applied. No row → ``{}``: the model is
        chosen in Settings → AI Model, never seeded from the environment (2026-10-10)."""
        data = super().get()
        if data:
            data = self._migrate_legacy_api_key(data)
            migrated = self._migrate_default_model(data)
            # PERSIST the composition. Recomposing on every read would leave the old trio
            # stored indefinitely — a second, stale copy of the default that anything
            # writing the raw row could contradict. Migrate once, then the row IS the truth.
            if migrated is not data:
                self._write_raw(migrated)
            return migrated
        return {}

    def _write_raw(self, raw: dict) -> None:
        self.save(raw)

    def _migrate_default_model(self, data: dict) -> dict:
        """Compose the legacy {provider, base_url, model} trio into ONE pinned ref.

        ``default_model`` is the authoritative instance default: a
        ``"<provider_key>:<model_id>"`` string that resolves to a provider on its own.
        The trio could disagree with itself (edit the default provider's model and the
        top-level copy stayed put) and its ``model`` half was BARE — carrying no provider,
        so the same string meant different things as the default provider changed.

        The trio is DELETED once composed — three fields that can disagree is the defect,
        so leaving them stored as a "compat copy" would just reintroduce it.
        """
        if (data.get("default_model") or "").strip():
            return data
        provider = (data.get("provider") or "").strip().lower()
        model = (data.get("model") or "").strip()
        if not provider:
            return data
        # A provider we cannot map to a catalogue entry still goes into the ref verbatim,
        # so resolved() can raise the precise "Unknown AI provider 'bogus'" rather than
        # degrading to the much less useful "not configured".
        key = _infer_provider_key(provider, (data.get("base_url") or "").strip()) or provider
        # codex_cli legitimately has a blank model ("use the CLI's own default"), and that
        # survives as an empty tail — `codex_cli:` still names its provider.
        migrated = {k: v for k, v in data.items() if k not in ("provider", "base_url", "model")}
        migrated["default_model"] = f"{key}:{model}"
        return migrated

    def _migrate_legacy_api_key(self, data: dict) -> dict:
        """One-time migration: move a top-level api_key into the providers map.

        If the config has a top-level ``api_key`` but the matching ``providers``
        entry for the inferred provider key is absent or has no key, the key is
        moved into ``providers[provider_key]`` and the row is re-saved.
        This eliminates the dual-format shim and keeps the resolution path clean.

        After migration the top-level ``api_key`` is cleared in storage so it cannot
        be mistakenly used for cross-provider fallback.
        """
        legacy_key = (data.get("api_key") or "").strip()
        if not legacy_key:
            return data

        provider = (data.get("provider") or "").strip().lower()
        if provider not in VALID_PROVIDERS:
            return data
        base_url = (data.get("base_url") or "").strip()
        provider_key = _infer_provider_key(provider, base_url)
        if provider_key is None:
            return data

        providers: dict = dict(data.get("providers") or {})
        existing = providers.get(provider_key) or {}
        if (existing.get("api_key") or "").strip():
            # Already have a key in the per-provider map — just clear the top-level
            # stale copy without touching anything else.
            migrated = dict(data)
            migrated["api_key"] = ""
            migrated["providers"] = providers
            self._write_raw(migrated)
            return migrated

        # Move the key into the providers map.
        providers[provider_key] = dict(existing)
        providers[provider_key]["api_key"] = legacy_key
        migrated = dict(data)
        migrated["api_key"] = ""  # cleared — providers map is now authoritative
        migrated["providers"] = providers
        self._write_raw(migrated)
        return migrated

    def resolved(self) -> dict:
        """The active config for the RUNTIME (carries the secret api_key).

        Everything is derived from ONE stored field, ``default_model``
        (``"<provider_key>:<model_id>"``), so the instance default cannot disagree with
        itself and always names the provider it runs on. Missing/invalid configuration
        RAISES rather than silently selecting a vendor or model.

        NOT for the API response — use ``public()`` there.
        """
        ref = (self._read_raw().get("default_model") or "").strip()
        if not ref or ":" not in ref:
            raise ConfigurationError(
                "AI provider is not configured. Choose one in Settings → AI Models."
            )
        key, _, model = ref.partition(":")
        entry = _provider_entry(key)
        if entry is None:
            raise ConfigurationError(
                f"Unknown AI provider {key!r}. Choose a valid provider in Settings → AI Models."
            )
        # A blank Codex CLI / Antigravity CLI model deliberately means "use the CLI's current
        # account-compatible default"; every other transport requires an explicit model.
        if not model and key not in ("codex_cli", "antigravity_cli"):
            raise ConfigurationError(
                "AI model is not configured. Choose one in Settings → AI Models."
            )
        # Deliberately NOT resolve_for_provider(): that applies the disabled gate, and the
        # INSTANCE DEFAULT must keep resolving regardless (disabling the active default is
        # refused by the PUT guard, so this state is only reachable by writing the store
        # directly). An explicit per-conversation pick still gates — see resolve_override.
        creds = (self._read_raw().get("providers") or {}).get(key) or {}
        api_key = (creds.get("api_key") or "").strip()
        transport = entry["transport"]

        # Env fallback when no key is stored — supports QA/deployment scenarios where the
        # key is not persisted (clone_data_for_qa strips it; QA passes ANTHROPIC_API_KEY).
        # The file always wins when present.
        if not api_key:
            env_var = {
                "anthropic": "ANTHROPIC_API_KEY",
                "openai_compatible": "OPENAI_API_KEY",
            }.get(transport)
            if env_var:
                api_key = (os.getenv(env_var) or "").strip()

        return {
            "provider": transport,
            "model": model,
            "base_url": _stored_base_url(entry, creds),
            "api_key": api_key,
        }

    def public(self) -> dict:
        """The non-secret view for the API/UI — never includes the api_key.

        ``provider``/``model``/``base_url`` are DERIVED from ``default_model``; they are a
        view for the UI, not storage. ``default_model`` itself is exposed so a caller can
        round-trip the default without re-composing it from three fields.
        """
        raw = self._read_raw()
        ref = (raw.get("default_model") or "").strip()
        key, _, model = ref.partition(":")
        entry = _provider_entry(key) if key else None
        creds = (raw.get("providers") or {}).get(key) or {} if key else {}
        transport = (entry or {}).get("transport") or ""
        base_url = (creds.get("base_url") or "").strip() or (entry or {}).get("base_url") or ""

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            env_var = {
                "anthropic": "ANTHROPIC_API_KEY",
                "openai_compatible": "OPENAI_API_KEY",
            }.get(transport)
            api_key = (os.getenv(env_var) or "").strip() if env_var else ""

        return {
            "default_model": ref,
            "provider": transport,
            "model": model,
            "base_url": base_url,
            "has_api_key": bool(api_key),
            "task_models": raw.get("task_models") or {},
            # Image-input capability per "<provider>:<model>" — non-secret, and the UI
            # needs it to render (and let the owner correct) what we believe.
            "vision": raw.get("vision") or {},
        }

    def provider_label(self) -> str:
        """A human-facing provider name for the active model (e.g. ``"OpenRouter"``, ``"Anthropic"``),
        or ``""`` when there is no clean name — the generic ``openai_compatible`` transport is an
        internal label, never surfaced. Resolves base_url → catalog entry; callers get clean data
        instead of reaching into ``_infer_provider_key``/``_provider_entry`` themselves."""
        r = self.resolved()
        key = _infer_provider_key(r.get("provider"), r.get("base_url"))
        label = ((_provider_entry(key) or {}).get("label") or "") if key else ""
        return "" if "compatible" in label.lower() else label

    # ── Per-provider credential store ─────────────────────────────────────────

    def _providers_raw(self) -> dict:
        """The raw ``providers`` map from disk (may be absent on old configs)."""
        return self._read_raw().get("providers") or {}

    def providers_public(self) -> dict:
        """The non-secret view of the per-provider map.

        Returns ``{provider_key: {has_api_key, base_url, model, key_status, checked_at, enabled}}``
        for every provider that has been explicitly configured.  api_key is never returned.

        ``key_status`` defaults to ``"unchecked"`` for legacy entries that predate
        validation.  ``checked_at`` is None when not yet validated.

        ``enabled`` defaults to ``True`` for legacy entries (absent field means enabled).

        ``base_url`` is always the stored value (empty string when absent) — not
        suppressed when it equals the catalog default.  The frontend relies on the
        round-trip to populate editable URL fields after save/reload.
        """
        result: dict = {}
        for key, creds in self._providers_raw().items():
            if not isinstance(creds, dict):
                continue
            base_url = (creds.get("base_url") or "").strip()
            api_key = (creds.get("api_key") or "").strip()
            model = (creds.get("model") or "").strip()
            key_status = creds.get("key_status") or "unchecked"
            checked_at = creds.get("checked_at") or None
            # ``enabled`` absent → True (legacy entries are enabled by default).
            enabled = creds.get("enabled", True)
            if not isinstance(enabled, bool):
                enabled = bool(enabled)
            result[key] = {
                "has_api_key": bool(api_key),
                "base_url": base_url,
                "model": model,
                "key_status": key_status,
                "checked_at": checked_at,
                "enabled": enabled,
            }
        return result

    def provider_enabled(self, provider_key: str) -> bool:
        """Return True when the provider is enabled (or absent — default True).

        A provider entry that exists but has ``enabled=False`` is disabled.
        Providers not yet configured (no entry in the map) are considered enabled
        so the instance default (which may have no entry) always works.
        """
        creds = self._providers_raw().get(provider_key)
        if creds is None or not isinstance(creds, dict):
            return True
        enabled = creds.get("enabled", True)
        if not isinstance(enabled, bool):
            return bool(enabled)
        return enabled

    def update_provider(self, provider_key: str, changes: dict) -> dict:
        """Patch a single provider's credentials and return the public view.

        ``changes`` may contain ``api_key`` (write-only; blank keeps saved),
        ``base_url``, ``model``, ``key_status``, ``checked_at``, and/or ``enabled``.
        The instance default ``provider``/``model``/``base_url``/``api_key`` top-level
        fields are NOT touched here — use ``update()`` for those.
        """
        _refuse_on_demo()
        raw = self._read_raw()
        providers: dict = raw.get("providers") or {}
        creds: dict = dict(providers.get(provider_key) or {})

        entry = _provider_entry(provider_key)
        if "base_url" in changes and changes["base_url"] is not None:
            new_url = str(changes["base_url"]).strip()
            if entry is not None and new_url and not (changes.get("api_key") or "").strip():
                _drop_key_if_rebound(entry, creds, new_url)
            creds["base_url"] = new_url
        if "api_key" in changes and (changes["api_key"] or "").strip():
            creds["api_key"] = str(changes["api_key"]).strip()
        if "model" in changes and changes["model"] is not None:
            creds["model"] = str(changes["model"]).strip()
        if "key_status" in changes and changes["key_status"] is not None:
            creds["key_status"] = str(changes["key_status"])
        if "checked_at" in changes and changes["checked_at"] is not None:
            creds["checked_at"] = str(changes["checked_at"])
        if "enabled" in changes and changes["enabled"] is not None:
            creds["enabled"] = bool(changes["enabled"])

        # Editing the DEFAULT provider's model moves the instance default with it. There is
        # nothing to keep "in sync" any more — the default is one ref whose prefix already
        # names this provider, so only its tail changes. A blank model is ignored: clearing
        # a provider's remembered model must not blank the default (see update()'s guard).
        default_key, _, _ = (raw.get("default_model") or "").partition(":")
        if default_key == provider_key and (creds.get("model") or "").strip():
            raw["default_model"] = f"{provider_key}:{creds['model'].strip()}"

        # Clean up: if the resulting creds are empty AND enabled (default), remove the
        # entry entirely. A disabled-only entry (enabled=False, nothing else) is kept so
        # the disabled state survives even when credentials are cleared. A recorded
        # key_status is likewise a keep-signal: it marks an explicitly set-up/validated
        # row (e.g. keyless Ollama on its default URL) that must stay configured.
        has_key = bool((creds.get("api_key") or "").strip())
        has_url = bool((creds.get("base_url") or "").strip())
        has_model = bool((creds.get("model") or "").strip())
        has_status = bool(creds.get("key_status"))
        is_disabled = creds.get("enabled") is False
        if not (has_key or has_url or has_model or has_status or is_disabled):
            providers.pop(provider_key, None)
        else:
            providers[provider_key] = creds
        raw["providers"] = providers

        self._write_raw(raw)
        return self.providers_public()

    def remove_provider(self, provider_key: str) -> dict:
        """Drop a provider's WHOLE entry — credentials, base_url, model, enabled flag.

        Distinct from ``remove_provider_key``, which deletes only ``api_key`` and
        deliberately preserves everything else (a disabled provider keeps its
        ``enabled=False``, which is why the row never disappeared from Settings).
        This is the "I am done with this provider" action behind Settings → AI → Edit
        → Remove; ``+ Add provider`` re-adds it from the catalog.

        Note the consequence for ``provider_enabled()``: an ABSENT entry reads as
        enabled (that default exists so an unconfigured instance default still works).
        So a model pinned as ``"<removed_key>:<model>"`` no longer raises
        ProviderDisabledError — it fails at the transport as unauthenticated instead.
        That is the truthful state once the provider is gone, but it IS a behaviour
        change for any agent or automation still pinned to it.

        Returns the public (no-key) providers view. Removing an absent key is a no-op.
        """
        _refuse_on_demo()
        raw = self._read_raw()
        providers: dict = raw.get("providers") or {}
        if provider_key in providers:
            providers.pop(provider_key, None)
            raw["providers"] = providers
            self._write_raw(raw)
        return self.providers_public()

    def remove_provider_key(self, provider_key: str) -> dict:
        """Explicitly DELETE a provider's stored api_key (and its validation status).

        This is the only deletion path — the write-only update contract treats a
        blank ``api_key`` as "keep the saved key" so an accidental empty save can
        never wipe credentials. ``base_url``/``model``/``enabled`` survive; the
        entry is pruned entirely when nothing meaningful remains.
        """
        _refuse_on_demo()
        raw = self._read_raw()
        providers: dict = raw.get("providers") or {}
        creds: dict = dict(providers.get(provider_key) or {})
        for field in ("api_key", "key_status", "checked_at"):
            creds.pop(field, None)

        has_url = bool((creds.get("base_url") or "").strip())
        has_model = bool((creds.get("model") or "").strip())
        is_disabled = creds.get("enabled") is False
        if not (has_url or has_model or is_disabled):
            providers.pop(provider_key, None)
        else:
            providers[provider_key] = creds
        raw["providers"] = providers

        self._write_raw(raw)
        return self.providers_public()

    def resolve_for_provider(
        self, provider_key: str, *, require_enabled: bool = True
    ) -> Optional[dict]:
        """Resolve the full credentials for a provider key.

        Returns ``{transport, base_url, api_key}`` or ``None`` if the key is not a
        known catalog entry.

        Raises ``ProviderDisabledError`` when the provider key is known but the
        provider has been explicitly disabled (``enabled=False``).  The caller must
        handle this distinctly from ``None`` (unknown key) — a disabled provider must
        NOT silently fall back to another provider.

        Credentials come only from the explicitly selected provider's stored entry.
        Missing values remain empty so the caller surfaces a configuration error.
        """
        from backend.core.errors import ProviderDisabledError

        entry = _provider_entry(provider_key)
        if entry is None:
            return None

        # `require_enabled=False` reads the credentials WITHOUT the gate. `enabled` governs
        # whether a provider may SERVE a turn — it is not a lock on the owner's own
        # credentials. Checking a switched-off provider's key before turning it back on is
        # exactly when that check is wanted.
        if require_enabled and not self.provider_enabled(provider_key):
            label = entry.get("label", provider_key)
            raise ProviderDisabledError(
                f"Provider {label!r} is disabled in Settings",
                provider_key=provider_key,
                label=label,
            )
        transport: str = entry["transport"]

        # Per-provider stored creds take precedence.
        creds = self._providers_raw().get(provider_key) or {}
        stored_base_url = _stored_base_url(entry, creds)
        stored_api_key = (creds.get("api_key") or "").strip()

        return {
            "transport": transport,
            "base_url": stored_base_url,
            "api_key": stored_api_key,
        }

    def resolve_override(self, override: str) -> Optional[dict]:
        """Parse a model override string into (transport, base_url, api_key, model_id).

        Two forms are accepted:
        - ``"<provider_key>:<model_id>"`` — provider-pinned; e.g. ``"anthropic:claude-haiku-4-5"``
        - ``"<model_id>"``               — plain model id, resolves within the instance default provider

        The canonical form is the compound string — agents store their model as
        ``"provider_key:model_id"`` so provider resolution is immune to global
        Settings changes.  Plain model ids fall through to the instance default
        (backward-compat for unrecognised local model names like ``"llama3.1"``).

        Returns a dict ``{transport, base_url, api_key, model, provider_key}`` or
        ``None`` when the override is blank/invalid (caller falls back to the
        instance default).  An unknown provider_key falls back to the plain-model
        path to stay forward-compatible with future catalog additions.

        Raises ``ProviderDisabledError`` when the referenced provider key is known but
        the provider has been explicitly disabled.  The error must NOT be swallowed —
        an explicit model must never silently fall back to another provider.
        """
        if not (override or "").strip():
            return None
        override = override.strip()

        # Compound "provider_key:model_id" — provider-pinned.
        if ":" in override:
            prefix, _, rest = override.partition(":")
            entry = _provider_entry(prefix)
            if entry is not None and (rest or prefix in ("codex_cli", "antigravity_cli")):
                creds = self.resolve_for_provider(prefix)
                if creds is None:
                    return None  # should not happen after the entry check
                return {
                    "provider_key": prefix,
                    "transport": creds["transport"],
                    "base_url": creds["base_url"],
                    "api_key": creds["api_key"],
                    "model": rest,
                }

        # Plain model id — resolve within the instance default provider.
        inst = self.resolved()
        return {
            "provider_key": None,
            "transport": inst["provider"],
            "base_url": inst["base_url"],
            "api_key": inst["api_key"],
            "model": override,
        }

    def read_stored_api_key(self, provider_key: str) -> Optional[str]:
        """Return the stored plaintext API key for a provider key, or None when absent.

        Reads ONLY ``providers[provider_key]`` from model_config.json. This is the
        single storage location post-migration: ``_migrate_legacy_api_key`` (run on
        every read) folds any legacy top-level ``api_key`` into the providers map,
        and ``update()`` writes instance-default keys there too — so no separate
        instance-default fallback is needed (it would read the same map entry).
        ENV-derived keys (ANTHROPIC_API_KEY etc.) are intentionally excluded — they
        are operator-level config not managed by the web UI.
        Returns ``None`` (not an empty string) when no stored key exists.
        """
        if _provider_entry(provider_key) is None:
            return None
        creds = self._providers_raw().get(provider_key) or {}
        return (creds.get("api_key") or "").strip() or None

    def detected_clis(self) -> dict[str, bool]:
        """``{cli_provider_key: is_installed}`` for every local-CLI catalog entry.

        Read-only detection for the Settings UI — it writes nothing, so a row can say
        "Detected" or "Not installed" without first enrolling the provider.
        """
        return {k: bool(v) for k, v in self.detected_cli_paths().items()}

    def detected_cli_paths(self) -> dict[str, str]:
        """``{cli_provider_key: absolute_path}`` — "" when the binary is not on this machine.

        The row renders this the way a URL provider renders its endpoint: both answer
        "where does this actually run from".
        """
        providers = self._providers_raw()
        out: dict[str, str] = {}
        for key in CLI_PROVIDER_KEYS:
            creds = providers.get(key)
            stored_path = (creds or {}).get("base_url") or "" if isinstance(creds, dict) else ""
            out[key] = cli_binary_path(key, stored_path)
        return out

    def configured_provider_keys(self) -> list[str]:
        """Catalog keys that have been explicitly configured (non-empty api_key or base_url)
        AND are enabled.

        Also includes the instance default provider's provider key (inferred by matching
        transport + base_url to the catalog) so ``GET /api/models?all=1`` always includes
        at least the active provider.  The instance default is always included regardless
        of the ``enabled`` flag on its entry — disabling the active default is refused by
        the PUT guard, so this path only sees a disabled default in a transient/corrupted
        state.

        **Local-CLI transports are auto-detected**: they have no api_key and no URL to
        store, so for them "configured" means INSTALLED (ADR 2026-07-31). An enabled CLI
        whose binary resolves on this machine is offered with zero setup; a CLI the owner
        has toggled off, or one that is not installed, is not.
        """
        providers = self._providers_raw()
        keys: list[str] = []
        # Per-provider map — skip disabled entries.
        for key, creds in providers.items():
            if not isinstance(creds, dict):
                continue
            if creds.get("enabled") is False:
                continue
            if key in CLI_PROVIDER_KEYS:
                # Decided ONLY by the detection branch below. A stored entry for a CLI holds
                # a binary PATH or a stale key_status, neither of which proves the binary is
                # still there — letting them count would offer a CLI the owner has since
                # uninstalled or repointed at a path that no longer exists.
                continue
            # Configured = has credentials, OR was explicitly set up/validated
            # (key_status recorded). The latter covers keyless providers on their
            # catalog-default base_url (e.g. Ollama: no key, default URL → the entry
            # holds only enabled/key_status, but its models must still be offered).
            if (
                (creds.get("api_key") or "").strip()
                or (creds.get("base_url") or "").strip()
                or creds.get("key_status")
            ):
                keys.append(key)
        # Local CLIs — installed + not toggled off is the whole enrolment story.
        for key in CLI_PROVIDER_KEYS:
            creds = providers.get(key)
            creds = creds if isinstance(creds, dict) else {}
            if creds.get("enabled") is False:
                continue
            if cli_is_installed(key, creds.get("base_url") or ""):
                keys.append(key)
        # Instance default — always included. Its provider is the ref's own prefix now,
        # so there is no transport+base_url round-trip to get it wrong.
        default_key, _, _ = (self._read_raw().get("default_model") or "").partition(":")
        if default_key and default_key not in keys:
            keys.insert(0, default_key)
        return keys

    # ── Per-task model assignment ──────────────────────────────────────────────

    def model_for_task(self, task: str) -> Optional[dict]:
        """Return resolved credentials for the task-pinned model, or None.

        ``task`` must be one of ``VALID_TASKS`` (memory, intel, suggestions, briefing).
        Returns the same ``{provider_key, transport, base_url, api_key, model}`` dict
        that ``resolve_override`` produces, or ``None`` when no override is stored
        (callers fall back to their own hard-coded default).

        CONTRACT — skip, don't reroute:
        Raises ``ProviderDisabledError`` when the assigned provider is disabled.
        Background jobs MUST catch this error, log it, set their ``last_status``, and
        skip the run.  They MUST NOT fall back to another provider — an explicit
        assignment that breaks should be visible, not silently rerouted.
        """
        raw = self._read_raw()
        override = (raw.get("task_models") or {}).get(task)
        if not override:
            return None
        return self.resolve_override(override)

    def task_model_override(self, task: str) -> Optional[str]:
        """The raw compound "provider_key:model_id" string pinned for a task, or None.

        Unlike ``model_for_task`` this does NOT resolve credentials (and so never
        raises) — callers that route through ``get_provider``/``llm_seam`` pass the
        compound string along and let resolution (incl. the disabled-provider error)
        happen at call time.
        """
        return (self._read_raw().get("task_models") or {}).get(task) or None

    def _validate_task_override(self, task: str, override: str) -> None:
        """Raise ValueError / ProviderDisabledError when an override is unusable."""
        if task not in VALID_TASKS:
            raise ValueError(f"unknown task {task!r}; must be one of {sorted(VALID_TASKS)}")
        prefix, _, model_part = override.partition(":")
        if ":" not in override or not model_part:
            raise ValueError(
                f"task model override must be '<provider_key>:<model_id>', got {override!r}"
            )
        if _provider_entry(prefix) is None:
            raise ValueError(f"unknown provider key {prefix!r} in task model override {override!r}")
        # Validate the provider is enabled (raises ProviderDisabledError if not).
        self.resolve_for_provider(prefix)

    def set_task_models(self, updates: dict) -> dict:
        """Bulk-assign task models in ONE validated, atomic write; returns the public view.

        ``updates`` maps task name -> compound ``"<provider_key>:<model_id>"`` string
        (or ``None``/empty to clear that task).  ALL entries are validated before
        anything is written, so a bad entry fails the whole request — no partial state.

        Raises ``ValueError`` for an invalid task name / malformed override / unknown
        provider key; ``ProviderDisabledError`` when a referenced provider is disabled
        (surface as 409 — re-enable the provider first).
        """
        cleaned: dict[str, Optional[str]] = {}
        for task, override in updates.items():
            if task not in VALID_TASKS:
                raise ValueError(f"unknown task {task!r}; must be one of {sorted(VALID_TASKS)}")
            value = (override or "").strip()
            if value:
                self._validate_task_override(task, value)
            cleaned[task] = value or None

        raw = self._read_raw()
        task_models: dict = dict(raw.get("task_models") or {})
        for task, value in cleaned.items():
            if value is None:
                task_models.pop(task, None)
            else:
                task_models[task] = value

        raw["task_models"] = task_models
        self._write_raw(raw)
        return self.public()

    def set_task_model(self, task: str, override: Optional[str]) -> dict:
        """Pin (or clear) the model for ONE background task — see ``set_task_models``."""
        return self.set_task_models({task: override})

    def vision_capabilities(self) -> dict:
        """The stored image-input capability map: ``{"<provider>:<model>": {supports, source}}``.

        Read by ``services.vision_capability.supports_vision`` before every image-bearing turn, so
        it must never raise — a malformed value is simply skipped there.
        """
        rows = self._read_raw().get("vision")
        return rows if isinstance(rows, dict) else {}

    def set_vision_capability(self, key: str, supports: bool, source: str) -> None:
        """Record one model's image-input support. No public() round-trip — this is
        called on a hot path (a provider refusal mid-turn), not from a settings form.

        The provenance ratchet (a weaker source never overwrites a stronger one) lives
        in ``services.vision_capability.record_vision_capability``, the single caller: the store
        stays a dumb writer, like every other repository here.
        """
        key = (key or "").strip()
        if not key:
            return
        raw = self._read_raw()
        rows: dict = dict(raw.get("vision") or {})
        rows[key] = {"supports": bool(supports), "source": source}
        raw["vision"] = rows
        self._write_raw(raw)

    def update(self, changes: dict) -> dict:
        """Persist a partial patch to the instance default and return the public view.

        Accepts either ``default_model`` (the canonical ``"<provider_key>:<model_id>"``
        ref) or the caller-friendly ``provider``/``base_url``/``model`` triple, which is
        COMPOSED into that one ref — the store never keeps three fields that can disagree.
        A blank/omitted ``api_key`` leaves the saved one unchanged (the UI submits a blank
        field to "keep current").
        """
        _refuse_on_demo()
        raw = self._read_raw()
        key, _, model = (raw.get("default_model") or "").strip().partition(":")

        if changes.get("default_model") is not None:
            key, _, model = str(changes["default_model"]).strip().partition(":")
        else:
            # Compose the triple. `provider` is a TRANSPORT, so it needs base_url to pin
            # down WHICH catalogue entry it means (openai vs xai vs ollama all speak
            # openai_compatible) — that inference happens once, here, not on every read.
            if changes.get("provider") is not None or changes.get("base_url") is not None:
                transport = str(changes.get("provider") or "").strip().lower()
                base_url = str(changes.get("base_url") or "").strip()
                if transport:
                    if transport not in PROVIDER_HINTS:
                        raise ConfigurationError(f"Unknown AI provider {transport!r}.")
                    key = _infer_provider_key(transport, base_url) or "custom"
                    # A URL that matches no preset lands on `custom`, whose catalogue
                    # base_url is empty — so the URL has to be persisted on the provider
                    # entry, which is where every reader looks for it. Storing it only at
                    # the top level is what made the old trio necessary in the first place.
                    if base_url:
                        providers_map: dict = dict(raw.get("providers") or {})
                        entry_creds = dict(providers_map.get(key) or {})
                        catalog = _provider_entry(key)
                        if catalog is not None and not (changes.get("api_key") or "").strip():
                            _drop_key_if_rebound(catalog, entry_creds, base_url)
                        entry_creds["base_url"] = base_url
                        providers_map[key] = entry_creds
                        raw["providers"] = providers_map
            if changes.get("model") is not None:
                model = str(changes["model"]).strip()

        if not key:
            raise ConfigurationError(
                "AI provider is not configured. Choose one in Settings → AI Models."
            )
        # A blank default model is WRITABLE but unusable: resolved() raises on the NEXT
        # turn while Settings still looks fine, so the failure lands far from the action
        # that caused it. "Set as default" on a provider with no remembered model sent
        # exactly that. Refuse it at the choke — every writer (REST, the
        # update_model_config tool, Telegram /model) goes through here. codex_cli / antigravity_cli are the
        # documented exceptions: blank means "use the CLI's own default".
        if not model and key not in ("codex_cli", "antigravity_cli"):
            raise ConfigurationError(
                "AI model is not configured. Pick a model for this provider first."
            )
        raw["default_model"] = f"{key}:{model}"

        # Empty api_key → "leave the saved one unchanged".
        new_key = (changes.get("api_key") or "").strip() if "api_key" in changes else ""
        if new_key:
            providers: dict = dict(raw.get("providers") or {})
            creds = dict(providers.get(key) or {})
            creds["api_key"] = new_key
            providers[key] = creds
            raw["providers"] = providers
            raw["api_key"] = ""  # providers map is authoritative

        self._write_raw(raw)
        return self.public()


def _infer_provider_key(transport: str, base_url: str) -> Optional[str]:
    """Infer the best-matching provider key from a (transport, base_url) pair.

    Used to map the legacy instance default back to a provider key for the
    multi-provider model union endpoint.  Exact match wins; for an editable-base-url
    entry (ollama) a base_url match is accepted.

    For an openai_compatible URL that does not match any catalog preset, returns
    ``"custom"`` as an internal storage sentinel (NOT in PROVIDER_CATALOG — the
    UI shows only the four named presets, but arbitrary endpoints still need a
    bucket for their api_key in the providers map).
    """
    # Exact match on both transport + base_url.
    for entry in PROVIDER_CATALOG:
        if entry["transport"] == transport and entry["base_url"] == base_url:
            return entry["key"]
    # Partial match for editable-base-url entries: only match when the stored base_url
    # equals the catalog entry's default URL (e.g. http://localhost:11434/v1 → "ollama").
    # An unknown URL that doesn't match any preset uses "custom" as a storage key — NOT
    # the first editable entry in catalog order (which happens to be "ollama").
    if transport == "openai_compatible":
        for entry in PROVIDER_CATALOG:
            if (
                entry["transport"] == "openai_compatible"
                and entry["editable_base_url"]
                and entry["base_url"]
                and entry["base_url"] == base_url
            ):
                return entry["key"]
        return "custom"
    if transport == "anthropic":
        return "anthropic"
    if transport in ("claude_cli", "codex_cli", "antigravity_cli"):
        # base_url is the CLI PATH on these transports, so a non-default path must still
        # resolve to the one catalog entry that owns the transport (the exact-match loop
        # above only fires when the path is blank).
        return transport
    return None


def provider_label_for(transport: str, base_url: str, provider_key: str = "") -> str:
    """Resolve a provider instance's own identity to its catalog display LABEL.

    ``provider_key`` wins when the caller already resolved one (a pinned
    ``"<key>:<model>"`` override); otherwise it's inferred from ``(transport, base_url)`` —
    the pair that actually identifies WHICH endpoint/credential is billing a call. A bare
    model id cannot do this reliably: an aggregator (OpenRouter) namespaces hundreds of
    upstream vendors behind one endpoint (e.g. ``z-ai/glm-5.3-flash``, ``google/gemini-…``),
    so guessing the provider from the model string alone mislabels — or fails to label at
    all — anything outside the catalog's small model-hint seed list.

    Every AI provider class calls this once per turn/call to attribute its own
    ``cost_tracker.record()`` write, so spend is billed to the endpoint that actually
    served it instead of being reverse-engineered from the model id at read time.
    """
    pk = provider_key or _infer_provider_key(transport, base_url) or ""
    entry = _provider_entry(pk) if pk else None
    return str(entry["label"]) if entry else (pk or transport or "Unknown")
