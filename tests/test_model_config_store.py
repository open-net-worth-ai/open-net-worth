"""Runtime AI model/provider config — ModelConfigStore CRUD over its DB-backed row.

Hermetic: ModelConfigStore is a NamespacedSettingsStore over the ``secret_config``
table (Wave 5D2a, JSON->DB); tests/conftest.py's autouse per-test table clear gives
every test a fresh row (never the live data dir), and each test also clears the
AI_* / OPENAI_* env so seeding is deterministic. The load-bearing invariant:
``.public()`` NEVER leaks the api_key, and a blank api_key on update keeps the
saved one.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ConfigurationError
from backend.db.model_config_store import (
    VALID_TASKS,
    ModelConfigStore,
    _infer_provider_key,
)


def test_valid_tasks_includes_news_screen():
    """INT5: news_screen is its OWN task, distinct from the (larger/pricier) intel task.

    Membership, not exact equality — VALID_TASKS is deliberately additive (statement-import
    stages, future consumers) and an exact-set pin breaks on every unrelated addition."""
    assert "news_screen" in VALID_TASKS
    assert {"memory", "intel", "suggestions", "briefing", "news_screen"} <= VALID_TASKS


@pytest.fixture(autouse=True)
def _clear_ai_env(monkeypatch):
    for key in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)


def _store(tmp_path=None):
    # tmp_path is vestigial (kept so every existing `_store(tmp_path)` call site below
    # still reads naturally) — the store is DB-backed now, and tests/conftest.py's
    # autouse per-test table clear already gives each test a fresh row, the same
    # isolation the old per-test tmp file gave.
    return ModelConfigStore()


def test_public_is_unconfigured_when_absent(tmp_path):
    pub = _store(tmp_path).public()
    assert pub == {
        # The instance default is ONE resolvable ref now; provider/model/base_url are a
        # derived view of it for the UI, not storage (ADR 2026-08-06-one-default-model).
        "default_model": "",
        "provider": "",
        "model": "",
        "base_url": "",
        "has_api_key": False,
        "task_models": {},
        # Image-input capability per "<provider>:<model>" (ADR 2026-07-26) — empty until a
        # provider declares one, a refusal teaches us one, or the owner sets one.
        "vision": {},
    }


def test_resolved_refuses_missing_provider_and_model(tmp_path):
    with pytest.raises(ConfigurationError, match="AI provider is not configured"):
        _store(tmp_path).resolved()


def test_environment_never_seeds_the_model(tmp_path, monkeypatch):
    """The model is chosen in Settings → AI Model only — AI_PROVIDER/AI_MODEL/OPENAI_BASE_URL
    are no longer read (2026-10-10)."""
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("AI_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    store = _store(tmp_path)
    pub = store.public()
    assert pub["model"] == "" and pub["provider"] == "" and pub["base_url"] == ""
    assert store.get() == {}


def test_update_round_trips_via_disk(tmp_path):
    out = _store(tmp_path).update(
        {"provider": "openai_compatible", "model": "gpt-4o", "base_url": "http://x/v1"}
    )
    assert out["provider"] == "openai_compatible"
    assert out["model"] == "gpt-4o"
    # File is authoritative; a fresh store reads it back.
    assert _store(tmp_path).public()["model"] == "gpt-4o"


def test_public_never_leaks_api_key(tmp_path):
    store = _store(tmp_path)
    out = store.update(
        {"provider": "openai_compatible", "model": "gpt-test", "api_key": "sk-secret-123"}
    )
    assert "api_key" not in out
    assert out["has_api_key"] is True
    # The resolved() (runtime) view DOES carry it; public() never does.
    assert store.resolved()["api_key"] == "sk-secret-123"
    assert "api_key" not in store.public()


def test_blank_api_key_keeps_saved(tmp_path):
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-test", "api_key": "sk-keep-me"})
    # A later update with a blank/whitespace key leaves the saved one intact.
    store.update({"model": "gpt-4o-mini", "api_key": ""})
    assert store.resolved()["api_key"] == "sk-keep-me"
    store.update({"api_key": "   "})
    assert store.resolved()["api_key"] == "sk-keep-me"


def test_file_is_authoritative_over_env(tmp_path, monkeypatch):
    # A model needs a provider: the default is stored as "<provider_key>:<model_id>", so
    # "set a model without saying where it runs" is refused rather than stored bare.
    _store(tmp_path).update({"provider": "anthropic", "model": "from-file"})
    monkeypatch.setenv("AI_MODEL", "from-env")
    assert _store(tmp_path).public()["model"] == "from-file"


def test_unknown_provider_on_disk_is_rejected():
    store = ModelConfigStore()
    store.save({"provider": "bogus", "model": "x"})
    with pytest.raises(ConfigurationError, match="Unknown AI provider"):
        store.resolved()


# ── _infer_provider_key regression (SHOULD-FIX #1) ─────────────────────────────


def test_infer_provider_key_unknown_openai_compatible_url_returns_custom():
    """An unknown openai_compatible URL must map to 'custom', not 'ollama'.

    Before the fix the partial-match loop returned the first editable_base_url
    entry in PROVIDER_CATALOG order (ollama), mislabelling any private/cloud key
    bucket as "Ollama (local) — configured" in the UI.
    """
    key = _infer_provider_key("openai_compatible", "https://private.llm.example.com/v1")
    assert key == "custom"


def test_infer_provider_key_ollama_default_url_still_maps_to_ollama():
    """The ollama default URL must still resolve correctly after the fix."""
    key = _infer_provider_key("openai_compatible", "http://localhost:11434/v1")
    assert key == "ollama"


# ── provider_label_for — the write-time cost-attribution helper ──────────────────
#
# Every AI provider class calls this once per turn/call to attribute its own
# cost_tracker.record() write to the endpoint that actually served it, instead of
# leaving services/cost.py to reverse-engineer the provider from a bare model id
# (which fails for aggregator-routed ids — see the OpenRouter cases below).


def test_provider_label_for_pinned_key_wins():
    from backend.db.model_config_store import provider_label_for

    assert provider_label_for("openai_compatible", "", "openrouter") == "OpenRouter"


def test_provider_label_for_infers_from_base_url():
    from backend.db.model_config_store import provider_label_for

    assert provider_label_for("openai_compatible", "https://api.x.ai/v1") == "xAI (Grok)"


def test_provider_label_for_openrouter_base_url():
    """The case that motivated this helper: OpenRouter routes hundreds of
    vendor-namespaced models (z-ai/glm-…, google/gemini-…) behind ONE endpoint — the
    model id can't identify the billing provider, but base_url always can."""
    from backend.db.model_config_store import provider_label_for

    assert provider_label_for("openai_compatible", "https://openrouter.ai/api/v1") == "OpenRouter"


def test_provider_label_for_anthropic():
    from backend.db.model_config_store import provider_label_for

    assert provider_label_for("anthropic", "") == "Anthropic"


def test_provider_label_for_claude_cli_any_path():
    """base_url is repurposed as the CLI path on this transport — any value (including a
    custom path) still resolves to the one catalog entry that owns the transport."""
    from backend.db.model_config_store import provider_label_for

    assert provider_label_for("claude_cli", "") == "Claude Code CLI"
    assert provider_label_for("claude_cli", "/custom/path/to/claude") == "Claude Code CLI"


def test_provider_label_for_codex_cli():
    from backend.db.model_config_store import provider_label_for

    assert provider_label_for("codex_cli", "") == "Codex CLI"


def test_provider_label_for_unknown_openai_compatible_url_is_custom():
    from backend.db.model_config_store import provider_label_for

    assert (
        provider_label_for("openai_compatible", "https://private.llm.example.com/v1")
        == "Custom (OpenAI-compatible)"
    )


# ── Model precedence: stored choice beats env, env beats hardcoded constant ──────
#
# These regression tests capture the "user stores haiku, env says sonnet, chat bills
# as sonnet" bug.  The invariant: the stored user choice ALWAYS wins over any env var;
# the env is a first-boot seed only.


def test_stored_model_beats_ai_model_env(tmp_path, monkeypatch):
    """The central regression: model stored via update() is returned by resolved()
    even when AI_MODEL env is a different value.

    This is the exact bug scenario: user changes to Haiku in Settings, but AI_MODEL
    env is still claude-sonnet-4-6 from the original .env file.  Execution must use
    the stored Haiku, not the env Sonnet.
    """
    store = ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-haiku-4-5"})
    monkeypatch.setenv("AI_MODEL", "claude-sonnet-4-6")
    assert store.resolved()["model"] == "claude-haiku-4-5"
    assert store.public()["model"] == "claude-haiku-4-5"


def test_existing_file_missing_model_is_rejected_instead_of_using_env(tmp_path, monkeypatch):
    """AI_MODEL env is a SEED for fresh instances only — an existing config file
    with no model field fails closed rather than consulting env or inventing a model."""

    # Row exists but has no model field.
    store = ModelConfigStore()
    store.save({"provider": "anthropic"})
    monkeypatch.setenv("AI_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    with pytest.raises(ConfigurationError, match="AI model is not configured"):
        store.resolved()


def test_env_not_consulted_after_explicit_store_write(tmp_path, monkeypatch):
    """End-to-end regression: set env to sonnet, then explicitly store haiku,
    then verify resolved() returns haiku in both public and runtime views.

    This is the precise sequence that triggered the bug: env seeds sonnet, user
    changes to haiku in Settings (update call), subsequent turn must use haiku.
    """
    monkeypatch.setenv("AI_MODEL", "claude-sonnet-4-6")
    store = ModelConfigStore()
    # Simulate the user changing the model in Settings.
    store.update({"provider": "anthropic", "model": "claude-haiku-4-5"})
    # Both public() (what the UI shows) and resolved() (what execution uses)
    # must agree on haiku — NOT the env's sonnet.
    assert store.public()["model"] == "claude-haiku-4-5"
    assert store.resolved()["model"] == "claude-haiku-4-5"


def test_legacy_migration_unknown_url_buckets_to_custom_and_resolves():
    """A legacy config with an unknown openai_compatible base_url migrates to
    providers['custom'], not providers['ollama'].  resolved() still finds the key.
    """
    store = ModelConfigStore()
    store.save(
        {
            "provider": "openai_compatible",
            "base_url": "https://private.llm.example.com/v1",
            "model": "my-model",
            "api_key": "sk-private-key",
        }
    )
    resolved = store.resolved()
    assert resolved["api_key"] == "sk-private-key"
    # The credential must land in the 'custom' bucket, not 'ollama'.
    raw = store.get()
    assert "custom" in raw.get("providers", {})
    assert "ollama" not in raw.get("providers", {})


# ── Catalog key lock ──────────────────────────────────────────────────────────
#
# Locks the catalog to its supported keys: the four originals (anthropic, openai,
# gemini, ollama) plus the added OpenAI-compatible presets (xai, glm, openrouter,
# litellm), the generic custom row, the two local coding-CLI transports
# (claude_cli, codex_cli) and Open Net Worth AI (onw_ai) — and nothing else.


def test_catalog_has_expected_provider_keys():
    from backend.db.model_config_store import PROVIDER_CATALOG

    keys = [e["key"] for e in PROVIDER_CATALOG]
    assert sorted(keys) == [
        "anthropic",
        "antigravity_cli",
        "claude_cli",
        "codex_cli",
        "custom",
        "gemini",
        "glm",
        "litellm",
        "ollama",
        "onw_ai",
        "openai",
        "openrouter",
        "xai",
    ], f"PROVIDER_CATALOG keys changed unexpectedly; got {keys}"


def test_provider_keys_are_unique():
    from backend.db.model_config_store import PROVIDER_CATALOG

    keys = [e["key"] for e in PROVIDER_CATALOG]
    assert len(keys) == len(set(keys)), f"Duplicate keys in PROVIDER_CATALOG: {keys}"


# ── Stale-stored-key graceful fallback ───────────────────────────────────────
#
# A model_config.json written before the prune may have providers entries for
# removed keys (groq, deepseek, lmstudio, …) or override strings like "groq:model".
# These must not crash; they must degrade gracefully to the instance default.


def test_stale_providers_key_does_not_crash_configured_provider_keys():
    """configured_provider_keys() skips entries for keys that no longer exist in
    the catalog — the removed provider contributes nothing, not an exception."""
    store = ModelConfigStore()
    store.save(
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "providers": {
                "anthropic": {"api_key": "sk-ant"},
                "groq": {"api_key": "sk-groq"},  # stale
                "deepseek": {"api_key": "sk-ds"},  # stale
                "lmstudio": {"base_url": "http://localhost:1234/v1"},  # stale
            },
        }
    )
    keys = store.configured_provider_keys()
    # Stale keys may appear (the store still iterates the raw map) but must not crash.
    # The important thing: no KeyError / AttributeError.
    assert isinstance(keys, list)
    assert "anthropic" in keys


def test_stale_provider_key_resolve_for_provider_returns_none(tmp_path):
    """resolve_for_provider() returns None for any key not in the current
    catalog (genuinely unknown keys, not catalog presets like xai/openrouter)."""
    store = ModelConfigStore()
    store.update({"provider": "openai_compatible", "model": "configured-model"})
    for stale_key in (
        "groq",
        "deepseek",
        "mistral",
        "together",
        "perplexity",
        "lmstudio",
        "vllm",
    ):
        result = store.resolve_for_provider(stale_key)
        assert result is None, (
            f"resolve_for_provider({stale_key!r}) must return None for removed provider keys"
        )


def test_stale_override_falls_back_gracefully(tmp_path):
    """resolve_override('groq:llama-3') must not crash even though 'groq' is no
    longer in the catalog — it falls back to the within-provider (model-only) path."""
    store = ModelConfigStore()
    store.update({"provider": "openai_compatible", "model": "configured-model"})
    # 'groq' is not in the catalog so _provider_entry returns None; the override
    # falls through to the model-only path and returns a dict keyed on the instance default.
    result = store.resolve_override("groq:llama-3")
    # Should not raise; returns either None or the model-only fallback dict.
    assert result is None or isinstance(result, dict)


# ── Compound-string resolution ─────────────────────────────────────────────────
#
# The AgentEditor stores agent models as "<provider_key>:<model_id>" so that the
# provider is pinned at save time and is immune to global Settings changes.
# These tests verify the full resolver chain for all four catalog providers.


def test_resolve_override_compound_anthropic():
    store = ModelConfigStore()
    store.save(
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "providers": {"anthropic": {"api_key": "sk-ant-test"}},
        }
    )
    result = store.resolve_override("anthropic:claude-haiku-4-5-20251001")
    assert result is not None
    assert result["transport"] == "anthropic"
    assert result["model"] == "claude-haiku-4-5-20251001"
    assert result["provider_key"] == "anthropic"
    assert result["api_key"] == "sk-ant-test"


def test_resolve_override_compound_openai():
    store = ModelConfigStore()
    store.save(
        {
            "provider": "anthropic",
            "providers": {"openai": {"api_key": "sk-oai-test"}},
        }
    )
    result = store.resolve_override("openai:gpt-4o")
    assert result is not None
    assert result["transport"] == "openai_compatible"
    assert result["model"] == "gpt-4o"
    assert result["provider_key"] == "openai"
    assert result["api_key"] == "sk-oai-test"


def test_resolve_override_compound_ollama():
    store = ModelConfigStore()
    result = store.resolve_override("ollama:llama3.1")
    assert result is not None
    assert result["transport"] == "openai_compatible"
    assert result["model"] == "llama3.1"
    assert result["provider_key"] == "ollama"
    assert "11434" in result["base_url"]


def test_resolve_override_compound_gemini():
    store = ModelConfigStore()
    store.save({"providers": {"gemini": {"api_key": "AIza-test"}}})
    result = store.resolve_override("gemini:gemini-2.5-flash")
    assert result is not None
    assert result["transport"] == "openai_compatible"
    assert result["model"] == "gemini-2.5-flash"
    assert result["provider_key"] == "gemini"
    assert result["api_key"] == "AIza-test"


def test_compound_override_immune_to_global_provider_change(tmp_path):
    """Changing the global provider must not affect an agent that stores
    'anthropic:claude-haiku' — the compound prefix pins the provider."""
    store = ModelConfigStore()
    store.save(
        {
            "provider": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "providers": {
                "anthropic": {"api_key": "sk-ant-correct"},
                "openai": {"api_key": "sk-oai"},
            },
        }
    )
    result = store.resolve_override("anthropic:claude-haiku-4-5-20251001")
    assert result is not None
    assert result["transport"] == "anthropic"
    assert result["api_key"] == "sk-ant-correct"
    assert result["model"] == "claude-haiku-4-5-20251001"


def test_provider_label_friendly_name():
    """provider_label() gives a human name (OpenRouter/Anthropic), '' for the generic transport."""
    from backend.db.model_config_store import ModelConfigStore

    s = ModelConfigStore()
    s.update(
        {
            "provider": "openai_compatible",
            "model": "openrouter/test",
            "base_url": "https://openrouter.ai/api/v1",
        }
    )
    assert s.provider_label() == "OpenRouter"
    s.update({"provider": "anthropic", "base_url": ""})
    assert s.provider_label() == "Anthropic"
    # a generic/unknown openai_compatible endpoint → no clean name → ''
    s.update({"provider": "openai_compatible", "base_url": "https://my-custom-llm.local/v1"})
    assert s.provider_label() == ""
