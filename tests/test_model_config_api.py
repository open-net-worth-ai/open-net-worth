"""GET/PUT /api/model-config + GET /api/model-config/providers.

Hermetic: model_config is DB-backed (Wave 5, JSON->DB) and isolated via db_isolated;
resets the Settings singleton, clears AI_*/AUTH_* env. The key invariant: the api_key is
write-only — it round-trips into ``resolved()`` but is NEVER present in any API response.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.api.models as models_api
import backend.core.config as config
import backend.db.model_config_store as store_mod
from backend.models.model_config import VALID_PROVIDERS


@pytest.fixture
def cfg_env(tmp_path, monkeypatch, db_isolated):
    # model_config is DB-backed now (Wave 5) — db_isolated already gives it a fresh, empty
    # row, no CONFIG_FILE monkeypatch needed.
    monkeypatch.setattr(models_api, "MODELS_CACHE_JSON", tmp_path / "models_cache.json")
    # The picker reads the preferred shortlist for the preferred flag — PreferredModelsStore
    # is DB-backed now (NamespacedSettingsStore); db_isolated (above) isolates it.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for key in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "AUTH_DISABLED",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    store_mod.ModelConfigStore().update({"provider": "anthropic", "model": "test-model"})
    config._settings = None
    yield monkeypatch
    config._settings = None


def _client() -> TestClient:
    """A minimal app gated EXACTLY like main.py (the single app-wide require_auth
    dependency) with only the model-config router mounted. The orchestrator wires the
    router into main.py for real."""
    from fastapi import Depends, FastAPI

    from backend.api._deps import require_auth
    from backend.api.models import router as models_router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(models_router)
    return TestClient(app)


def test_get_explicit_test_config(cfg_env):
    body = _client().get("/api/model-config").json()
    assert body == {
        # The stored instance default: ONE resolvable ref. provider/model/base_url below
        # are a derived view of it (ADR 2026-08-06-one-default-model).
        "default_model": "anthropic:test-model",
        "provider": "anthropic",
        "model": "test-model",
        "effective_model": "test-model",
        "base_url": "",
        "has_api_key": False,
        "task_models": {},
        # Image-input capability per "<provider>:<model>" — empty until something is
        # declared by a provider, learned from a refusal, or set by the owner.
        "vision": {},
        # DERIVED (not stored): which model would read an image for a chat model with no
        # vision. "" here because "test-model" is unknown to the heuristic, so the default
        # is not positively blind and resolves as its own reader... which it is: an unknown
        # model is attempted rather than refused (ADR 2026-07-26 fail-open).
        "image_reader": "anthropic:test-model",
    }


def test_providers_lists_kinds_and_hints(cfg_env):
    body = _client().get("/api/model-config/providers").json()
    providers = body["providers"]
    assert "anthropic" in providers
    assert "openai_compatible" in providers
    # Ollama base_url hint is surfaced for the local-model story.
    hints = [h["base_url"] for h in providers["openai_compatible"]["base_url_hints"]]
    assert "http://localhost:11434/v1" in hints


@pytest.mark.parametrize(
    ("host_system", "expected"),
    [("Darwin", "macos"), ("Windows", "windows"), ("Linux", "linux"), ("FreeBSD", "other")],
)
def test_provider_catalog_reports_the_server_platform_not_the_browser(
    cfg_env, monkeypatch, host_system, expected
):
    monkeypatch.setattr("platform.system", lambda: host_system)
    monkeypatch.setattr(store_mod, "cli_binary_path", lambda *_args: "")
    response = _client().get(
        "/api/model-config/providers",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    assert response.status_code == 200
    assert response.json()["cli_host_platform"] == expected


def test_provider_catalog_platform_metadata_never_executes_a_cli_or_returns_credentials(
    cfg_env, monkeypatch
):
    import subprocess

    store_mod.ModelConfigStore().update_provider("anthropic", {"api_key": "sk-catalog-secret"})
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(store_mod, "cli_binary_path", lambda *_args: "")

    def reject_execution(*_args, **_kwargs):
        raise AssertionError("Reading the provider catalog must not execute a CLI")

    monkeypatch.setattr(subprocess, "run", reject_execution)
    response = _client().get("/api/model-config/providers")
    assert response.status_code == 200
    body = response.json()
    assert body["cli_host_platform"] == "macos"
    assert body["configured_providers"]["anthropic"]["has_api_key"] is True
    assert "api_key" not in body["configured_providers"]["anthropic"]
    assert "sk-catalog-secret" not in response.text


def test_put_round_trip_no_key_in_response(cfg_env):
    client = _client()
    r = client.put(
        "/api/model-config",
        json={
            "provider": "openai_compatible",
            "model": "gpt-4o",
            "base_url": "http://localhost:11434/v1",
            "api_key": "sk-secret-xyz",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "openai_compatible"
    assert body["model"] == "gpt-4o"
    assert body["base_url"] == "http://localhost:11434/v1"
    assert body["has_api_key"] is True
    assert "api_key" not in body
    # A fresh GET also never exposes the key.
    got = client.get("/api/model-config").json()
    assert "api_key" not in got
    assert got["has_api_key"] is True


def test_put_bad_provider_rejected(cfg_env):
    r = _client().put("/api/model-config", json={"provider": "skynet"})
    assert r.status_code == 422


def test_put_blank_key_keeps_saved(cfg_env):
    client = _client()
    client.put("/api/model-config", json={"provider": "openai_compatible", "api_key": "sk-1"})
    # Re-PUT without a key (UI submits blank to keep current).
    client.put("/api/model-config", json={"model": "gpt-4o-mini"})
    assert store_mod.ModelConfigStore().resolved()["api_key"] == "sk-1"


# ── catalog ───────────────────────────────────────────────────────────────────


def test_providers_response_includes_catalog(cfg_env):
    """GET /api/model-config/providers includes a ``catalog`` list alongside
    the legacy ``providers`` map (back-compat)."""
    body = _client().get("/api/model-config/providers").json()
    assert "catalog" in body
    catalog = body["catalog"]
    assert isinstance(catalog, list)
    keys = [e["key"] for e in catalog]
    # The supported presets must be present (four originals + the added providers).
    for expected in (
        "anthropic",
        "openai",
        "gemini",
        "ollama",
        "xai",
        "glm",
        "openrouter",
        "litellm",
        "custom",
    ):
        assert expected in keys, f"catalog missing key: {expected}"
    # Non-preset keys must not appear in the catalog.
    for removed in ("groq", "deepseek", "mistral", "together", "perplexity", "lmstudio", "vllm"):
        assert removed not in keys, f"catalog must not contain non-preset key: {removed}"


def test_catalog_entries_have_required_fields(cfg_env):
    body = _client().get("/api/model-config/providers").json()
    for entry in body["catalog"]:
        for field in (
            "key",
            "label",
            "transport",
            "base_url",
            "needs_api_key",
            "editable_base_url",
            "model_hints",
        ):
            assert field in entry, f"catalog entry {entry.get('key')!r} missing field {field!r}"
        # Derived, not re-listed: a new transport is added in ONE place
        # (models.model_config.VALID_PROVIDERS) and this stays honest.
        assert entry["transport"] in VALID_PROVIDERS


def test_put_ollama_style_round_trips(cfg_env):
    """PUT-ing an Ollama-style config (transport=openai_compatible + localhost base_url)
    is accepted and returned correctly — the catalog is presentation-only, the transport
    validation is unchanged."""
    client = _client()
    r = client.put(
        "/api/model-config",
        json={
            "provider": "openai_compatible",
            "model": "llama3.1",
            "base_url": "http://localhost:11434/v1",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "openai_compatible"
    assert body["base_url"] == "http://localhost:11434/v1"
    assert body["model"] == "llama3.1"


def test_catalog_anthropic_no_editable_base_url(cfg_env):
    """Anthropic catalog entry must not have editable_base_url (it has no base_url)."""
    body = _client().get("/api/model-config/providers").json()
    anthropic = next(e for e in body["catalog"] if e["key"] == "anthropic")
    assert anthropic["editable_base_url"] is False
    assert anthropic["transport"] == "anthropic"


def test_catalog_ollama_editable_base_url(cfg_env):
    """Ollama runs on a user-chosen host:port, so the endpoint must be editable
    (with the localhost default as the prefill)."""
    body = _client().get("/api/model-config/providers").json()
    entry = next(e for e in body["catalog"] if e["key"] == "ollama")
    assert entry["editable_base_url"] is True
    assert entry["base_url"] == "http://localhost:11434/v1"


# ── GET /api/models (live model list) ──────────────────────────────────────────


class _FakeResp:
    """A minimal httpx.Response stand-in for the mocked model-list calls."""

    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_models_live_anthropic(cfg_env):
    """With a key stored in model config + the Anthropic HTTP mocked, the endpoint
    returns the LIVE list (source=live) parsed from data[].id — never the static fallback."""
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResp({"data": [{"id": "claude-opus-4-8"}, {"id": "claude-sonnet-4-6"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    # Store the key via the API (no env fallback — key must come from the store).
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})
    body = client.get("/api/models").json()
    assert body["source"] == "live"
    assert [m["id"] for m in body["models"]] == ["claude-opus-4-8", "claude-sonnet-4-6"]
    # The Anthropic auth headers are sent (x-api-key + version), not a Bearer.
    assert captured["url"] == models_api._ANTHROPIC_MODELS_URL
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert "anthropic-version" in captured["headers"]
    # effort_levels are derived PER MODEL from the live list — modern Opus 4.8 exposes
    # the native xhigh ("Extra") tier; Sonnet 4.6 does not.
    by_id = {m["id"]: m for m in body["models"]}
    assert by_id["claude-opus-4-8"]["effort_levels"] == ["low", "medium", "high", "xhigh", "max"]
    assert by_id["claude-sonnet-4-6"]["effort_levels"] == ["low", "medium", "high", "max"]


def test_models_fallback_when_no_key(cfg_env):
    """No Anthropic key + no env → the endpoint NEVER raises; it returns the static
    fallback floor with source=fallback (no network touched)."""

    def boom(*a, **k):  # any network call would be a bug here
        raise AssertionError("must not hit the network without a key")

    cfg_env.setattr(models_api.httpx, "get", boom)
    body = _client().get("/api/models").json()
    assert body["source"] == "fallback"
    ids = [m["id"] for m in body["models"]]
    assert "claude-opus-5-5" in ids  # the current Opus, not a stale id


def test_models_fallback_on_http_error(cfg_env):
    """A live call that errors falls back gracefully (source=fallback), never 500s."""
    cfg_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def fake_get(*a, **k):
        raise RuntimeError("network down")

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    r = _client().get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback"
    assert body["models"]  # never empty


def test_models_openai_compatible_live(cfg_env):
    """An openai_compatible provider hits {base_url}/models with a Bearer key and
    parses data[].id (source=live)."""
    client = _client()
    client.put(
        "/api/model-config",
        json={
            "provider": "openai_compatible",
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-openai",
        },
    )
    # A direct OpenAI/xAI list now ALSO cross-references OpenRouter's public catalog
    # (a second httpx.get), so capture every call and assert against the provider one.
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, "headers": kwargs.get("headers", {})})
        return _FakeResp({"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    body = client.get("/api/models").json()
    assert body["source"] == "live"
    assert [m["id"] for m in body["models"]] == ["gpt-4o", "gpt-4o-mini"]
    provider_call = next(c for c in calls if c["url"] == "https://api.openai.com/v1/models")
    assert provider_call["headers"]["Authorization"] == "Bearer sk-openai"


def test_models_openrouter_filters_non_tool_and_non_text(cfg_env):
    """OpenRouter's /models carries supported_parameters + architecture, so a model
    that can't call tools or can't output text is dropped from the picker (a broken
    tool loop, not a real option). A model WITHOUT that metadata is always kept
    (unknown → trust), so other providers' lists are unaffected."""
    client = _client()
    client.put(
        "/api/model-config",
        json={
            "provider": "openai_compatible",
            "model": "anthropic/claude-sonnet-5",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-or",
        },
    )
    payload = {
        "data": [
            {
                "id": "anthropic/claude-sonnet-5",
                "supported_parameters": ["tools", "reasoning"],
                "architecture": {"output_modalities": ["text"]},
            },
            {
                "id": "black-forest/flux-image",  # no "tools" → dropped
                "supported_parameters": ["max_tokens"],
                "architecture": {"output_modalities": ["image", "text"]},
            },
            {
                "id": "some/tts-model",  # text not in output → dropped
                "supported_parameters": ["tools"],
                "architecture": {"output_modalities": ["audio"]},
            },
            {"id": "legacy/no-metadata"},  # no capability metadata → kept
        ]
    }
    cfg_env.setattr(models_api.httpx, "get", lambda url, **kw: _FakeResp(payload))
    ids = [m["id"] for m in client.get("/api/models").json()["models"]]
    assert ids == ["anthropic/claude-sonnet-5", "legacy/no-metadata"]


def test_models_metadataless_provider_drops_media_by_name(cfg_env):
    """xAI/OpenAI-direct /models return no capability metadata, so obvious media models
    (grok-imagine-image/-video, dall-e, tts, whisper, embeddings) are dropped by a
    modality-NAME heuristic while plain text ids (grok-4.5) are kept."""
    client = _client()
    client.put(
        "/api/model-config",
        json={
            "provider": "openai_compatible",
            "model": "grok-4.5",
            "base_url": "https://api.x.ai/v1",
            "api_key": "xai-key",
        },
    )
    payload = {
        "data": [
            {"id": "grok-4.5"},  # plain text → kept
            {"id": "grok-4.3"},  # plain text → kept
            {"id": "grok-imagine-image"},  # media → dropped
            {"id": "grok-imagine-video-1.5"},  # media → dropped
            {"id": "dall-e-3"},  # media → dropped
            {"id": "tts-1"},  # media → dropped
            {"id": "text-embedding-3-large"},  # embeddings → dropped
        ]
    }
    # api.x.ai also cross-refs OpenRouter's public catalog for efforts — tolerate any URL.
    cfg_env.setattr(models_api.httpx, "get", lambda url, **kw: _FakeResp(payload))
    ids = [m["id"] for m in client.get("/api/models").json()["models"]]
    assert ids == ["grok-4.5", "grok-4.3"]


def test_models_preferred_flag_from_allowlist(cfg_env):
    """A model in the routing allowlist is marked preferred:true; others preferred:false.
    A 'provider:model' allowlist ref matches a single-provider row by its bare id tail."""
    from backend.db.preferred_models_store import PreferredModelsStore

    PreferredModelsStore().replace(["openrouter:anthropic/claude-sonnet-5"])
    client = _client()
    client.put(
        "/api/model-config",
        json={
            "provider": "openai_compatible",
            "model": "anthropic/claude-sonnet-5",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-or",
        },
    )
    payload = {
        "data": [
            {
                "id": "anthropic/claude-sonnet-5",
                "supported_parameters": ["tools"],
                "architecture": {"output_modalities": ["text"]},
            },
            {
                "id": "meta/llama-x",
                "supported_parameters": ["tools"],
                "architecture": {"output_modalities": ["text"]},
            },
        ]
    }
    cfg_env.setattr(models_api.httpx, "get", lambda url, **kw: _FakeResp(payload))
    by_id = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert by_id["anthropic/claude-sonnet-5"]["preferred"] is True
    assert by_id["meta/llama-x"]["preferred"] is False


# ── Model-precedence regression (UI shows X, execution uses Y) ────────────────


def test_get_model_config_reflects_stored_model_not_env(cfg_env):
    """GET /api/model-config must return the STORED model even when AI_MODEL env is
    a different value.  This is the 'UI shows Haiku, billing is Sonnet' regression:
    the endpoint must never expose the env-overridden value to the UI.
    """
    client = _client()
    # Store haiku explicitly (simulating the user's Settings save).
    client.put("/api/model-config", json={"model": "claude-haiku-4-5"})
    # Set the env to a different model (what's in the .env file).
    cfg_env.setenv("AI_MODEL", "claude-sonnet-4-6")
    body = client.get("/api/model-config").json()
    assert body["model"] == "claude-haiku-4-5", (
        "GET /api/model-config returned env model instead of stored model — "
        "this means the UI would show the wrong model"
    )


# ── PUT /api/model-config/providers/{key} with model field ───────────────────


def test_put_provider_model_persists_and_round_trips(cfg_env):
    """PUT providers/{key} with a ``model`` field must persist it and surface it
    through GET /api/model-config/providers (providers_public).

    NOTE: PUT now returns {configured_providers, key_status, ...} instead of the
    flat providers_public() dict — check via configured_providers."""

    def fake_get(*a, **k):
        return _FakeResp({"data": [{"id": "claude-haiku-4-5"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    r = client.put(
        "/api/model-config/providers/anthropic",
        json={"api_key": "sk-ant-test", "model": "claude-haiku-4-5"},
    )
    assert r.status_code == 200
    body = r.json()
    # Response now carries configured_providers wrapper.
    configured = body["configured_providers"]
    assert "anthropic" in configured
    assert configured["anthropic"]["model"] == "claude-haiku-4-5"
    assert configured["anthropic"]["has_api_key"] is True

    # Round-trip via GET /api/model-config/providers.
    providers_resp = client.get("/api/model-config/providers").json()
    configured = providers_resp["configured_providers"]
    assert configured.get("anthropic", {}).get("model") == "claude-haiku-4-5"


def test_put_provider_model_without_key_still_persists(cfg_env):
    """A PUT that only carries ``model`` (no api_key) must persist the model."""

    def fake_get(*a, **k):
        return _FakeResp({"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    # First store a key so the entry exists.
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-openai"})
    # Then update only the model.
    r = client.put("/api/model-config/providers/openai", json={"model": "gpt-4o-mini"})
    assert r.status_code == 200
    body = r.json()
    configured = body["configured_providers"]
    assert configured["openai"]["model"] == "gpt-4o-mini"
    assert configured["openai"]["has_api_key"] is True  # key preserved


def test_put_provider_model_pydantic_does_not_strip_it(cfg_env):
    """Regression: Pydantic must NOT strip the ``model`` field (it was absent from
    ProviderCredentialsUpdate before the fix)."""
    client = _client()
    # The key symptom was that PUT returned 200 but model was never written.
    r = client.put(
        "/api/model-config/providers/anthropic",
        json={"api_key": "sk-ant-xyz", "model": "claude-opus-4-8"},
    )
    assert r.status_code == 200
    # Verify persistence — a fresh store read must see the model.
    import backend.db.model_config_store as store_mod

    store = store_mod.ModelConfigStore()
    raw_providers = store._providers_raw()
    assert raw_providers.get("anthropic", {}).get("model") == "claude-opus-4-8"


# ── GET /api/model-config/providers/{key}/reveal-key ─────────────────────────


def test_reveal_returns_stored_per_provider_key(cfg_env):
    """Reveal returns the key written via PUT providers/{key}."""
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-reveal-me"})
    r = client.get("/api/model-config/providers/anthropic/reveal-key")
    assert r.status_code == 200
    assert r.json()["api_key"] == "sk-ant-reveal-me"


def test_reveal_key_stored_via_instance_default_put(cfg_env):
    """A key saved through PUT /api/model-config (instance default) is revealable:
    update() writes it into providers[inferred_provider_key], the single storage
    location read_stored_api_key reads from."""
    client = _client()
    client.put("/api/model-config", json={"provider": "anthropic", "api_key": "sk-ant-default"})
    r = client.get("/api/model-config/providers/anthropic/reveal-key")
    assert r.status_code == 200
    assert r.json()["api_key"] == "sk-ant-default"


def test_reveal_key_from_legacy_top_level_format(cfg_env, tmp_path):
    """A pre-migration model_config row with a legacy top-level api_key is folded
    into the providers map by _migrate_legacy_api_key on first read — and is then
    revealable. Regression for the (removed) dead instance-default fallback branch."""
    # model_config is DB-backed now (Wave 5) — plant the legacy-shaped row directly via
    # the store's raw save() (bypasses update()'s own migration-aware merge, since the
    # whole point here is an UN-migrated row for the read path to fold).
    store_mod.ModelConfigStore().save(
        {"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-legacy"}
    )
    r = _client().get("/api/model-config/providers/anthropic/reveal-key")
    assert r.status_code == 200
    assert r.json()["api_key"] == "sk-legacy"
    # The migration must have cleared the top-level key in storage.
    raw = store_mod.ModelConfigStore().get()
    assert not (raw.get("api_key") or "")


def test_reveal_404_when_nothing_stored(cfg_env):
    """No stored key of any kind → 404 (not an empty payload)."""
    r = _client().get("/api/model-config/providers/anthropic/reveal-key")
    assert r.status_code == 404


def test_reveal_404_unknown_provider_key(cfg_env):
    """A provider key that doesn't exist → 404."""
    r = _client().get("/api/model-config/providers/definitely-not-a-provider/reveal-key")
    assert r.status_code == 404


def test_reveal_cache_control_no_store(cfg_env):
    """The reveal response must carry Cache-Control: no-store."""
    client = _client()
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-openai-xyz"})
    r = client.get("/api/model-config/providers/openai/reveal-key")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "").lower()


def test_reveal_key_not_in_providers_public(cfg_env):
    """providers_public (GET /api/model-config/providers) must never expose a key
    even after a reveal call has been made."""
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-secret"})
    client.get("/api/model-config/providers/anthropic/reveal-key")
    body = client.get("/api/model-config/providers").json()
    configured = body["configured_providers"]
    assert "api_key" not in configured.get("anthropic", {})
    assert configured["anthropic"]["has_api_key"] is True


def test_reveal_env_key_not_exposed(cfg_env):
    """ANTHROPIC_API_KEY env key must NOT be returned — env is operator config."""
    cfg_env.setenv("ANTHROPIC_API_KEY", "sk-ant-env-only")
    # No stored key in model_config.json at all.
    r = _client().get("/api/model-config/providers/anthropic/reveal-key")
    assert r.status_code == 404


def test_models_cache_hit_skips_network(cfg_env):
    """A second call within the TTL serves from the cache and makes NO new HTTP call."""
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _FakeResp({"data": [{"id": "claude-opus-4-8"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    # Store the key via the API — env fallback has been removed.
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})
    first = client.get("/api/models").json()
    second = client.get("/api/models").json()
    assert first["source"] == "live" and second["source"] == "live"
    assert calls["n"] == 1  # cache served the second call


def test_models_union_caches_every_provider(cfg_env):
    """?all=1 caches EACH provider (multi-key file), so a second union call makes ZERO
    new HTTP calls — regression for the single-slot cache, where every provider's write
    clobbered the previous one and the union re-fetched them all every time."""
    import threading

    calls = {"n": 0}
    lock = threading.Lock()

    def fake_get(url, **k):
        # Thread-safe: providers are now fetched concurrently.
        with lock:
            calls["n"] += 1
            n = calls["n"]
        return _FakeResp({"data": [{"id": f"m-{n}"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant"})
    client.put("/api/model-config/providers/gemini", json={"api_key": "g-key"})

    first = client.get("/api/models?all=1").json()
    n_after_first = calls["n"]
    assert n_after_first >= 2  # both providers fetched on the cold union
    second = client.get("/api/models?all=1").json()
    assert calls["n"] == n_after_first  # every provider cache-hit → no new HTTP calls
    assert first["source"] == second["source"] == "live"
    assert [m["id"] for m in first["models"]] == [m["id"] for m in second["models"]]


# ── Bug-fix regressions ───────────────────────────────────────────────────────


def test_base_url_round_trips_for_ollama(cfg_env):
    """Bug 3 regression: a saved base_url for an editable-url provider (ollama) must
    survive the providers_public() round-trip unchanged — it must NOT be suppressed
    when it equals the catalog default.  The frontend relies on this to pre-populate
    the Base URL field after save + reload."""
    client = _client()
    client.put(
        "/api/model-config/providers/ollama",
        json={"base_url": "http://localhost:11434/v1"},
    )
    body = client.get("/api/model-config/providers").json()
    configured = body["configured_providers"]
    assert configured.get("ollama", {}).get("base_url") == "http://localhost:11434/v1"


def test_base_url_round_trips_custom_ollama_url(cfg_env):
    """A non-default Ollama base_url (e.g. a remote host) must also round-trip."""
    client = _client()
    client.put(
        "/api/model-config/providers/ollama",
        json={"base_url": "http://192.168.1.50:11434/v1"},
    )
    body = client.get("/api/model-config/providers").json()
    configured = body["configured_providers"]
    assert configured.get("ollama", {}).get("base_url") == "http://192.168.1.50:11434/v1"


def test_catalog_entries_carry_model_hints(cfg_env):
    """Every named-preset catalog entry carries non-empty model_hints so the UI can
    show a dropdown even before the user connects. The generic "custom" and "litellm"
    rows are the exceptions — their models are user-supplied (a LiteLLM proxy's aliases
    / an arbitrary endpoint), so they deliberately have no hints and render a free-text
    model field instead."""
    body = _client().get("/api/model-config/providers").json()
    for entry in body["catalog"]:
        assert isinstance(entry.get("model_hints"), list), (
            f"{entry.get('key')!r} missing model_hints list"
        )
        if entry["key"] in ("custom", "litellm"):
            continue
        assert len(entry["model_hints"]) > 0, (
            f"{entry.get('key')!r} has empty model_hints — dropdown would be blank"
        )


# ── Key-validation gating ─────────────────────────────────────────────────────
#
# Tests for PUT /api/model-config/providers/{key} (on-save validation) and
# POST /api/model-config/providers/{key}/validate (re-check endpoint).
# All tests mock the fetch functions — no real network (except the manual Ollama
# test that runs against localhost:11434 outside this suite).


def test_put_valid_key_persists_status_valid(cfg_env):
    """Saving a key that the mocked fetch accepts → key_status=valid in response
    and persisted in the store."""

    def fake_get(url, **kwargs):
        return _FakeResp({"data": [{"id": "claude-sonnet-4-6"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    secret = "sk-ant-good"
    r = client.put("/api/model-config/providers/anthropic", json={"api_key": secret})
    assert r.status_code == 200
    body = r.json()
    assert body["key_status"] == "valid"
    assert "models" in body
    assert "claude-sonnet-4-6" in body["models"]
    # The field name "api_key" must not appear as a top-level key (has_api_key OK).
    assert "api_key" not in body
    # The secret VALUE must never appear in the response text.
    assert secret not in r.text
    # Persisted in the public view.
    configured = body["configured_providers"]
    assert configured["anthropic"]["key_status"] == "valid"
    assert configured["anthropic"].get("checked_at") is not None


def test_put_401_persists_status_invalid(cfg_env):
    """A 401 from the provider → key_status=invalid persisted and echoed."""
    import httpx as _httpx

    class _Resp401:
        status_code = 401
        text = "{}"

        def raise_for_status(self):
            raise _httpx.HTTPStatusError("401", request=None, response=self)

        def json(self):
            return {}

    cfg_env.setattr(models_api.httpx, "get", lambda *a, **k: _Resp401())
    client = _client()
    secret = "sk-ant-bad"
    r = client.put("/api/model-config/providers/anthropic", json={"api_key": secret})
    assert r.status_code == 200
    body = r.json()
    assert body["key_status"] == "invalid"
    assert "error" in body
    # The field name must not appear as a top-level key; the secret VALUE must not appear.
    assert "api_key" not in body
    assert secret not in r.text
    configured = body["configured_providers"]
    assert configured["anthropic"]["key_status"] == "invalid"


def test_put_403_persists_status_invalid(cfg_env):
    """A 403 from the provider → key_status=invalid."""
    import httpx as _httpx

    class _Resp403:
        status_code = 403
        text = "{}"

        def raise_for_status(self):
            raise _httpx.HTTPStatusError("403", request=None, response=self)

        def json(self):
            return {}

    cfg_env.setattr(models_api.httpx, "get", lambda *a, **k: _Resp403())
    client = _client()
    r = client.put("/api/model-config/providers/openai", json={"api_key": "sk-fake"})
    assert r.status_code == 200
    assert r.json()["key_status"] == "invalid"


def test_put_timeout_persists_status_unreachable(cfg_env):
    """A timeout on the fetch → key_status=unreachable (not invalid or error)."""

    def fake_get(*a, **k):
        import httpx as _httpx

        raise _httpx.TimeoutException("timed out")

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    r = client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-slow"})
    assert r.status_code == 200
    body = r.json()
    assert body["key_status"] == "unreachable"
    assert "error" in body
    configured = body["configured_providers"]
    assert configured["anthropic"]["key_status"] == "unreachable"


def test_put_connection_error_persists_unreachable(cfg_env):
    """A connection error → key_status=unreachable."""

    def fake_get(*a, **k):
        import httpx as _httpx

        raise _httpx.ConnectError("refused")

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    r = client.put(
        "/api/model-config/providers/ollama", json={"base_url": "http://localhost:11434/v1"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["key_status"] == "unreachable"


def test_ollama_401_is_unreachable_not_invalid(cfg_env):
    """Ollama is keyless — a 401 from Ollama must map to unreachable, never invalid."""
    import httpx as _httpx

    class _Resp401:
        status_code = 401
        text = "{}"

        def raise_for_status(self):
            raise _httpx.HTTPStatusError("401", request=None, response=self)

        def json(self):
            return {}

    cfg_env.setattr(models_api.httpx, "get", lambda *a, **k: _Resp401())
    client = _client()
    r = client.put(
        "/api/model-config/providers/ollama", json={"base_url": "http://localhost:11434/v1"}
    )
    assert r.status_code == 200
    # Ollama has needs_api_key=False — a 401 means the endpoint is there but
    # the config is wrong; classify as unreachable, never invalid (no key to reject).
    assert r.json()["key_status"] == "unreachable"


def test_get_providers_includes_key_status(cfg_env):
    """GET /api/model-config/providers must include key_status + checked_at for each
    configured provider.  Legacy entries that have no stored status return 'unchecked'."""

    def fake_get(*a, **k):
        return _FakeResp({"data": [{"id": "claude-sonnet-4-6"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    # Save a provider via PUT (which validates and persists status).
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-good"})
    body = client.get("/api/model-config/providers").json()
    configured = body["configured_providers"]
    assert "anthropic" in configured
    assert configured["anthropic"]["key_status"] == "valid"
    assert configured["anthropic"]["checked_at"] is not None


def test_legacy_entry_shows_unchecked(cfg_env, tmp_path):
    """A pre-existing model_config row with no key_status field must surface
    key_status='unchecked' via GET /api/model-config/providers."""
    # model_config is DB-backed now (Wave 5) — write the legacy row directly via the
    # store's raw save().
    store_mod.ModelConfigStore().save(
        {
            "provider": "anthropic",
            "providers": {"anthropic": {"api_key": "sk-legacy-nocheck"}},
        }
    )
    body = _client().get("/api/model-config/providers").json()
    configured = body["configured_providers"]
    assert configured["anthropic"]["key_status"] == "unchecked"


def test_validate_endpoint_rechecks_and_bypasses_cache(cfg_env):
    """POST .../validate re-runs the check, updates persisted status, and bypasses cache."""
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _FakeResp({"data": [{"id": "claude-opus-4-8"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    # First PUT saves the key (call 1) and validates (call 2).
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-v"})
    before = calls["n"]

    # POST /validate must bypass the cache (call before + 1).
    r = client.post("/api/model-config/providers/anthropic/validate")
    assert r.status_code == 200
    body = r.json()
    assert body["key_status"] == "valid"
    assert "models" in body
    assert calls["n"] == before + 1  # one additional live fetch, not a cache hit


def test_validate_works_on_a_switched_off_provider(cfg_env):
    """Regression: `enabled` governs whether a provider SERVES turns, not whether the owner
    may check its key — checking one before turning it back on is exactly when you want to.

    This endpoint went through resolve_for_provider, which RAISES for a disabled provider,
    and called it unguarded: testing a real key on a switched-off provider 500'd, and the UI
    (seeing no `error` field) reported "unreachable" — blaming the network."""
    cfg_env.setattr(
        models_api.httpx, "get", lambda *a, **k: _FakeResp({"data": [{"id": "gpt-4o"}]})
    )
    # NOT the instance default (anthropic is, under the test env) — disabling the active
    # default is refused by its own guard, so it could never reach this path.
    client = _client()
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-real"})
    assert (
        client.put("/api/model-config/providers/openai", json={"enabled": False}).status_code == 200
    )

    r = client.post("/api/model-config/providers/openai/validate")

    assert r.status_code == 200
    assert r.json()["key_status"] == "valid"
    # Testing must not switch it back on as a side effect.
    providers = client.get("/api/model-config/providers").json()["configured_providers"]
    assert providers["openai"]["enabled"] is False


def test_validate_endpoint_404_unknown_key(cfg_env):
    """POST .../validate on an unknown provider key → 404."""
    r = _client().post("/api/model-config/providers/definitely-unknown/validate")
    assert r.status_code == 404


def test_validate_endpoint_updates_invalid_to_valid(cfg_env):
    """If a row was previously invalid, a re-check that succeeds must update
    the persisted status to valid."""
    calls_n = [0]

    def invalid_then_valid(*a, **k):
        calls_n[0] += 1
        import httpx as _httpx

        if calls_n[0] <= 1:

            class _R:
                status_code = 401
                text = "{}"

                def raise_for_status(self):
                    raise _httpx.HTTPStatusError("401", request=None, response=self)

                def json(self):
                    return {}

            return _R()
        return _FakeResp({"data": [{"id": "gpt-4o"}]})

    cfg_env.setattr(models_api.httpx, "get", invalid_then_valid)
    client = _client()
    put_r = client.put("/api/model-config/providers/openai", json={"api_key": "sk-openai-x"})
    assert put_r.json()["key_status"] == "invalid"

    recheck = client.post("/api/model-config/providers/openai/validate")
    assert recheck.status_code == 200
    assert recheck.json()["key_status"] == "valid"

    # Persisted status is now valid.
    prov = client.get("/api/model-config/providers").json()
    assert prov["configured_providers"]["openai"]["key_status"] == "valid"


def test_key_never_in_any_response(cfg_env):
    """The api_key must NEVER appear in any validation-related response body."""

    def fake_get(*a, **k):
        return _FakeResp({"data": [{"id": "claude-sonnet-4-6"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    secret = "sk-ant-super-secret-key-12345"
    put_body = client.put("/api/model-config/providers/anthropic", json={"api_key": secret}).text
    assert secret not in put_body

    validate_body = client.post("/api/model-config/providers/anthropic/validate").text
    assert secret not in validate_body

    get_body = client.get("/api/model-config/providers").text
    assert secret not in get_body


# ── Provider enable/disable toggle (API-level) ────────────────────────────────


def test_put_provider_enabled_false_no_validation(cfg_env):
    """PUT providers/{key} with enabled=False disables the provider without running
    validation (no network call), and echoes enabled=False in configured_providers."""

    def boom(*a, **k):
        raise AssertionError("must not call network when disabling")

    cfg_env.setattr(models_api.httpx, "get", boom)
    client = _client()
    # Store a key first (validation runs here — override the fake).
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _FakeResp({"data": [{"id": "claude-sonnet-4-6"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-x"})
    before = calls["n"]

    # Now disable — must not call the network.
    cfg_env.setattr(models_api.httpx, "get", boom)
    r = client.put("/api/model-config/providers/openai", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["configured_providers"]["openai"]["enabled"] is False
    assert calls["n"] == before  # no extra network call


def test_disable_active_default_refused_409(cfg_env):
    """PUT providers/{key} with enabled=False for the active instance default → 409."""

    def fake_get(*a, **k):
        return _FakeResp({"data": [{"id": "claude-sonnet-4-6"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    # Store a key for anthropic (makes it the default by convention — instance default
    # resolves to anthropic since no base_url is set).
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant"})
    # Anthropic IS the default (default provider after env-seed is anthropic).
    r = client.put("/api/model-config/providers/anthropic", json={"enabled": False})
    assert r.status_code == 409
    assert "default" in r.json()["detail"].lower()


def test_disable_non_default_allowed(cfg_env):
    """PUT providers/{key} with enabled=False for a NON-default provider is allowed."""

    def fake_get(*a, **k):
        return _FakeResp({"data": [{"id": "gpt-4o"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    # Instance default is anthropic; disable openai (a non-default).
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-x"})
    r = client.put("/api/model-config/providers/openai", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["configured_providers"]["openai"]["enabled"] is False


def test_reenable_runs_validation(cfg_env):
    """PUT providers/{key} with enabled=True re-runs validation (network call)."""
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _FakeResp({"data": [{"id": "gpt-4o"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-x"})
    client.put("/api/model-config/providers/openai", json={"enabled": False})
    before = calls["n"]
    r = client.put("/api/model-config/providers/openai", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["configured_providers"]["openai"]["enabled"] is True
    assert calls["n"] == before + 1  # re-validation fired


def test_models_all_excludes_disabled_provider(cfg_env):
    """GET /api/models?all=1 must not include models from disabled providers."""
    client = _client()

    def fake_get(url, **kwargs):
        if "anthropic" in url:
            return _FakeResp({"data": [{"id": "claude-sonnet-4-6"}]})
        return _FakeResp({"data": [{"id": "gpt-4o"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-x"})
    # Disable openai.
    client.put("/api/model-config/providers/openai", json={"enabled": False})

    body = client.get("/api/models?all=1").json()
    provider_keys = {m.get("provider_key") for m in body["models"]}
    assert "openai" not in provider_keys


def test_toggle_busts_models_cache(cfg_env, tmp_path):
    """Toggling a provider's enabled state removes the models cache file so the
    next GET /api/models?all=1 fetches live instead of serving stale data."""
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _FakeResp({"data": [{"id": "gpt-4o"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-x"})
    # Seed the cache via GET /api/models?all=1.
    client.get("/api/models?all=1")
    assert (tmp_path / "models_cache.json").exists()

    # Toggle — cache must be busted.
    client.put("/api/model-config/providers/openai", json={"enabled": False})
    assert not (tmp_path / "models_cache.json").exists()


@pytest.mark.integration
def test_ollama_live_validation():
    """Manual Ollama validation against the REAL local instance at localhost:11434.
    Requires Ollama running; marked integration so it is skipped in CI."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "model_config.json"
        store_mod.CONFIG_FILE = cfg_path
        store = store_mod.ModelConfigStore(path=cfg_path)
        # Ollama is keyless — save only the base_url.
        store.update_provider("ollama", {"base_url": "http://localhost:11434/v1"})
        from backend.api.models import _validate_credentials  # noqa: PLC0415

        status, models, error = _validate_credentials(
            "ollama", "openai_compatible", "http://localhost:11434/v1", ""
        )
        assert status == "valid", f"Expected valid, got {status!r}: {error}"
        assert len(models) > 0, "Expected at least one model from local Ollama"


# ── DELETE provider key (explicit removal — blank PUT keeps the key) ──────────


class _FakeOkResp:
    status_code = 200

    @staticmethod
    def json():
        return {"data": [{"id": "model-x"}]}


def _mock_ok(cfg_env):
    cfg_env.setattr(models_api.httpx, "get", lambda url, **kw: _FakeOkResp())


def test_delete_provider_key_removes_it(cfg_env):
    _mock_ok(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-to-delete"})
    assert client.get("/api/model-config/providers").json()["configured_providers"]["openai"][
        "has_api_key"
    ]

    res = client.delete("/api/model-config/providers/openai/key")
    assert res.status_code == 200
    configured = res.json()["configured_providers"]
    assert "openai" not in configured or not configured["openai"].get("has_api_key")
    # The write-only "blank keeps saved" contract is untouched — there is simply
    # no key left to keep.
    body = client.get("/api/model-config/providers").json()["configured_providers"]
    assert "openai" not in body or not body["openai"].get("has_api_key")


def test_delete_provider_key_unknown_404(cfg_env):
    client = _client()
    assert client.delete("/api/model-config/providers/nope/key").status_code == 404


def test_delete_default_provider_key_refused(cfg_env):
    _mock_ok(cfg_env)
    client = _client()
    client.put("/api/model-config", json={"provider": "anthropic", "model": "claude-sonnet-4-6"})
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-default"})
    res = client.delete("/api/model-config/providers/anthropic/key")
    assert res.status_code == 409
    assert "default" in res.json()["detail"].lower()


def test_delete_preserves_base_url_and_disabled(cfg_env):
    _mock_ok(cfg_env)
    client = _client()
    client.put(
        "/api/model-config/providers/ollama",
        json={"base_url": "http://otherhost:11434/v1"},
    )
    res = client.delete("/api/model-config/providers/ollama/key")
    assert res.status_code == 200
    configured = res.json()["configured_providers"]
    assert configured["ollama"]["base_url"] == "http://otherhost:11434/v1"


# ── DELETE /api/model-config/providers/{key} — the WHOLE entry ───────────────
#
# Distinct from the /key sibling above, which deletes only api_key and deliberately
# preserves base_url/model/enabled. Because a disabled provider always retains
# enabled=False, that preservation meant Settings' "Remove" button could never make a
# disabled row disappear — it removed a key that was already gone and left the entry.


def test_delete_provider_removes_whole_entry(cfg_env):
    _mock_ok(cfg_env)
    client = _client()
    client.put(
        "/api/model-config/providers/openai",
        json={"api_key": "sk-gone", "base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
    )
    assert "openai" in client.get("/api/model-config/providers").json()["configured_providers"]

    res = client.delete("/api/model-config/providers/openai")
    assert res.status_code == 200
    assert "openai" not in res.json()["configured_providers"]
    # And it stays gone on a fresh read — the entry was pruned from disk, not just the response.
    assert "openai" not in client.get("/api/model-config/providers").json()["configured_providers"]


def test_delete_provider_removes_a_disabled_entry(cfg_env):
    """The case the /key endpoint could not: nothing but enabled=False stored.

    This is the exact shape Settings leaves behind when you switch a provider off, and the
    reason Remove appeared to do nothing.
    """
    _mock_ok(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/gemini", json={"enabled": False})
    configured = client.get("/api/model-config/providers").json()["configured_providers"]
    assert configured["gemini"]["enabled"] is False

    # The key endpoint leaves it in place …
    client.delete("/api/model-config/providers/gemini/key")
    assert "gemini" in client.get("/api/model-config/providers").json()["configured_providers"]

    # … the provider endpoint takes it out.
    res = client.delete("/api/model-config/providers/gemini")
    assert res.status_code == 200
    assert "gemini" not in res.json()["configured_providers"]


def test_delete_provider_unknown_404(cfg_env):
    client = _client()
    assert client.delete("/api/model-config/providers/nope").status_code == 404


def test_delete_default_provider_refused(cfg_env):
    """Removing the active default would break every chat turn — same guard as /key."""
    _mock_ok(cfg_env)
    client = _client()
    client.put("/api/model-config", json={"provider": "anthropic", "model": "claude-sonnet-4-6"})
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-default"})

    res = client.delete("/api/model-config/providers/anthropic")
    assert res.status_code == 409
    assert "default" in res.json()["detail"].lower()
    # Refused means UNCHANGED, not partially applied.
    configured = client.get("/api/model-config/providers").json()["configured_providers"]
    assert configured["anthropic"]["has_api_key"] is True


def test_delete_provider_leaves_others_alone(cfg_env):
    _mock_ok(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-a"})
    client.put("/api/model-config/providers/xai", json={"api_key": "sk-b"})

    client.delete("/api/model-config/providers/openai")
    configured = client.get("/api/model-config/providers").json()["configured_providers"]
    assert "openai" not in configured
    assert configured["xai"]["has_api_key"] is True


# ── PUT /api/model-config/task-models (single bulk endpoint) ─────────────────


def _mock_anthropic_models(cfg_env):
    def fake_get(*a, **k):
        return _FakeResp({"data": [{"id": "claude-haiku-4-5"}, {"id": "claude-sonnet-4-6"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)


def test_put_task_models_sets_override(cfg_env):
    """One PUT sets an override and returns it in task_models."""
    _mock_anthropic_models(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})

    r = client.put(
        "/api/model-config/task-models",
        json={"memory": "anthropic:claude-haiku-4-5"},
    )
    assert r.status_code == 200
    assert r.json()["task_models"]["memory"] == "anthropic:claude-haiku-4-5"


def test_put_task_models_bulk_sets_all_in_one_request(cfg_env):
    """ONE request assigns multiple tasks atomically; all echo back."""
    _mock_anthropic_models(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})
    r = client.put(
        "/api/model-config/task-models",
        json={
            "memory": "anthropic:claude-haiku-4-5",
            "intel": "anthropic:claude-haiku-4-5",
            "briefing": "anthropic:claude-sonnet-4-6",
        },
    )
    assert r.status_code == 200
    tm = r.json()["task_models"]
    assert tm["memory"] == "anthropic:claude-haiku-4-5"
    assert tm["intel"] == "anthropic:claude-haiku-4-5"
    assert tm["briefing"] == "anthropic:claude-sonnet-4-6"
    assert "suggestions" not in tm


def test_put_task_models_accepts_news_screen(cfg_env):
    """INT5: news_screen is REST-settable like every other task (own cheap-model pin)."""
    _mock_anthropic_models(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})
    r = client.put(
        "/api/model-config/task-models",
        json={"news_screen": "anthropic:claude-haiku-4-5"},
    )
    assert r.status_code == 200
    assert r.json()["task_models"]["news_screen"] == "anthropic:claude-haiku-4-5"


def test_put_task_models_clear_with_null(cfg_env):
    """null clears an assignment; omitted fields stay untouched."""
    _mock_anthropic_models(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})
    client.put(
        "/api/model-config/task-models",
        json={"memory": "anthropic:claude-haiku-4-5", "intel": "anthropic:claude-sonnet-4-6"},
    )
    r = client.put("/api/model-config/task-models", json={"memory": None})
    assert r.status_code == 200
    tm = r.json()["task_models"]
    assert "memory" not in tm
    assert tm["intel"] == "anthropic:claude-sonnet-4-6"  # untouched


def test_put_task_models_bad_entry_fails_whole_request(cfg_env):
    """Validation is all-or-nothing: one bad entry -> 400 and NOTHING persists."""
    _mock_anthropic_models(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})
    r = client.put(
        "/api/model-config/task-models",
        json={"memory": "anthropic:claude-haiku-4-5", "intel": "plain-model-no-colon"},
    )
    assert r.status_code == 400
    cfg = client.get("/api/model-config").json()
    assert "memory" not in (cfg.get("task_models") or {})


def test_put_task_models_plain_model_id_400(cfg_env):
    """A plain model id (no colon) returns 400 — compound form is required."""
    r = _client().put(
        "/api/model-config/task-models",
        json={"memory": "claude-haiku-4-5"},
    )
    assert r.status_code == 400


def test_put_task_models_unknown_provider_key_400(cfg_env):
    """An override referencing an unknown provider key returns 400."""
    r = _client().put(
        "/api/model-config/task-models",
        json={"intel": "not_a_real_provider:some-model"},
    )
    assert r.status_code == 400


def test_put_task_models_disabled_provider_409(cfg_env):
    """An override pointing at a disabled provider returns 409."""

    def fake_get(*a, **k):
        return _FakeResp({"data": [{"id": "gpt-4o"}]})

    cfg_env.setattr(models_api.httpx, "get", fake_get)
    client = _client()
    client.put("/api/model-config/providers/openai", json={"api_key": "sk-oai"})
    client.put("/api/model-config/providers/openai", json={"enabled": False})

    r = client.put(
        "/api/model-config/task-models",
        json={"suggestions": "openai:gpt-4o-mini"},
    )
    assert r.status_code == 409


def test_get_model_config_includes_task_models(cfg_env):
    """GET /api/model-config returns task_models in its payload."""
    _mock_anthropic_models(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})
    client.put(
        "/api/model-config/task-models",
        json={"briefing": "anthropic:claude-haiku-4-5"},
    )
    body = client.get("/api/model-config").json()
    assert "task_models" in body
    assert body["task_models"]["briefing"] == "anthropic:claude-haiku-4-5"


def test_task_models_multiple_tasks_coexist(cfg_env):
    """Multiple task overrides can be stored independently."""
    _mock_anthropic_models(cfg_env)
    client = _client()
    client.put("/api/model-config/providers/anthropic", json={"api_key": "sk-ant-test"})
    client.put(
        "/api/model-config/task-models",
        json={"memory": "anthropic:claude-haiku-4-5", "intel": "anthropic:claude-sonnet-4-6"},
    )
    body = client.get("/api/model-config").json()
    assert body["task_models"]["memory"] == "anthropic:claude-haiku-4-5"
    assert body["task_models"]["intel"] == "anthropic:claude-sonnet-4-6"


def test_providers_endpoint_reports_each_clis_own_default(cfg_env, tmp_path):
    codex, claude = tmp_path / "codex", tmp_path / "claude"
    codex.mkdir()
    claude.mkdir()
    (codex / "config.toml").write_text('model = "synthetic-sol"\n')
    (claude / "settings.json").write_text('{"model": "opus"}')
    cfg_env.setenv("CODEX_HOME", str(codex))
    cfg_env.setenv("CLAUDE_CONFIG_DIR", str(claude))
    body = _client().get("/api/model-config/providers").json()
    assert body["cli_default_models"] == {"claude_cli": "opus", "codex_cli": "synthetic-sol"}


def test_blank_codex_model_reports_the_clis_real_default(cfg_env, tmp_path):
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "config.toml").write_text('model = "synthetic-sol"\n')
    cfg_env.setenv("CODEX_HOME", str(codex))
    store_mod.ModelConfigStore().update({"provider": "codex_cli", "model": ""})
    body = _client().get("/api/model-config").json()
    assert body["model"] == ""
    assert body["effective_model"] == "synthetic-sol"


def test_explicit_model_is_its_own_effective_model(cfg_env):
    assert _client().get("/api/model-config").json()["effective_model"] == "test-model"


def test_two_custom_endpoints_survive_over_http(cfg_env):
    cfg_env.setattr(models_api, "_validate_credentials", lambda *a, **k: ("valid", ["m1"], ""))
    client = _client()
    for key, url, label in (
        ("custom", "https://one.example/v1", "Work gateway"),
        ("custom_ab12cd34", "https://two.example/v1", "Home server"),
    ):
        r = client.put(
            f"/api/model-config/providers/{key}",
            json={"base_url": url, "api_key": "sk-" + key, "label": label, "enabled": True},
        )
        assert r.status_code == 200, r.text

    got = client.get("/api/model-config/providers").json()["configured_providers"]
    assert got["custom"]["base_url"] == "https://one.example/v1"
    assert got["custom"]["label"] == "Work gateway"
    assert got["custom_ab12cd34"]["base_url"] == "https://two.example/v1"
    assert got["custom_ab12cd34"]["label"] == "Home server"
    assert "sk-custom" not in str(got)

    assert client.put("/api/model-config/providers/custom_BAD!", json={}).status_code == 404
    assert client.delete("/api/model-config/providers/custom").status_code == 200
    after = client.get("/api/model-config/providers").json()["configured_providers"]
    assert "custom" not in after and "custom_ab12cd34" in after
