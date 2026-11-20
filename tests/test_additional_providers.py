"""Tests for the additional AI providers: xAI (Grok), Zhipu (GLM), OpenRouter,
and the generic Custom (OpenAI-compatible) row.

Covers:
  - PROVIDER_CATALOG contains the four new entries with the expected transport,
    base_url, and editable-base-url flags.
  - resolve_for_provider() returns the right transport/base_url for each new key,
    honors a stored GLM China-region base_url override, and resolves a Custom row
    from a user-supplied base_url + key.
  - Pricing rows: grok-* and glm-* map to non-zero list prices; the free GLM flash
    tier is 0; an unknown id still returns 0.
  - OpenRouter cost capture: base_url detection, the usage-accounting request extra,
    attribution headers, and the reported-cost-over-estimate helper.
  - AMS weak-tier defaults include Grok/GLM and degrade for OpenRouter/Custom.
"""

from __future__ import annotations

import json

import pytest

from backend.db.model_config_store import PROVIDER_CATALOG, ModelConfigStore, _provider_entry


@pytest.fixture
def cfg_env(monkeypatch):
    """Isolated model-config store on the per-test DB; env cleared; Anthropic key in env."""
    for key in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    ModelConfigStore().update({"provider": "anthropic", "model": "test-model"})


# ── Catalog presence + shape ──────────────────────────────────────────────────

_EXPECTED = {
    "xai": ("xAI (Grok)", "https://api.x.ai/v1", False),
    "glm": ("Zhipu (GLM)", "https://api.z.ai/api/paas/v4/", True),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", False),
    "litellm": ("LiteLLM Proxy (local)", "http://localhost:4000", True),
    "custom": ("Custom (OpenAI-compatible)", "", True),
}


@pytest.mark.parametrize("key", list(_EXPECTED))
def test_catalog_has_new_provider(key):
    entry = _provider_entry(key)
    assert entry is not None, f"{key} missing from PROVIDER_CATALOG"
    label, base_url, editable = _EXPECTED[key]
    assert entry["label"] == label
    assert entry["transport"] == "openai_compatible"
    assert entry["base_url"] == base_url
    assert entry["editable_base_url"] is editable
    assert entry["needs_api_key"] is True


def test_catalog_keys_are_unique():
    keys = [e["key"] for e in PROVIDER_CATALOG]
    assert len(keys) == len(set(keys))


def test_openai_compatible_preset_base_urls_are_distinct():
    """Non-editable openai_compatible presets must have distinct base_urls so
    _infer_provider_key can map an instance-default back to the right row."""
    seen: dict[str, str] = {}
    for e in PROVIDER_CATALOG:
        if e["transport"] != "openai_compatible" or e["editable_base_url"]:
            continue
        assert e["base_url"], f"{e['key']} preset needs a base_url"
        assert e["base_url"] not in seen, f"duplicate base_url {e['base_url']}"
        seen[e["base_url"]] = e["key"]


# ── Resolution ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["xai", "glm", "openrouter", "litellm"])
def test_resolve_fresh_provider_uses_catalog_base_url(cfg_env, key):
    """A configured-nothing provider resolves to its catalog default base_url,
    openai_compatible transport, and an empty key (no cross-provider leak)."""
    store = ModelConfigStore()
    resolved = store.resolve_for_provider(key)
    assert resolved is not None
    assert resolved["transport"] == "openai_compatible"
    assert resolved["base_url"] == _EXPECTED[key][1]
    assert resolved["api_key"] == ""


def test_resolve_custom_uses_user_base_url_and_key(cfg_env):
    store = ModelConfigStore()
    store.update_provider(
        "custom", {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-deep"}
    )
    resolved = store.resolve_for_provider("custom")
    assert resolved["transport"] == "openai_compatible"
    assert resolved["base_url"] == "https://api.deepseek.com/v1"
    assert resolved["api_key"] == "sk-deep"


def test_resolve_glm_honors_china_region_override(cfg_env):
    """GLM's base_url is editable — a stored China endpoint wins over the default."""
    store = ModelConfigStore()
    china = "https://open.bigmodel.cn/api/paas/v4/"
    store.update_provider("glm", {"base_url": china, "api_key": "glm-key"})
    resolved = store.resolve_for_provider("glm")
    assert resolved["base_url"] == china
    assert resolved["api_key"] == "glm-key"


def test_custom_pin_resolves_via_override(cfg_env):
    """A 'custom:model' override resolves through the custom row's stored creds."""
    store = ModelConfigStore()
    store.update_provider("custom", {"base_url": "https://api.x/v1", "api_key": "k"})
    ov = store.resolve_override("custom:some-model")
    assert ov is not None
    assert ov["provider_key"] == "custom"
    assert ov["transport"] == "openai_compatible"
    assert ov["base_url"] == "https://api.x/v1"
    assert ov["model"] == "some-model"


# ── Pricing ───────────────────────────────────────────────────────────────────


class TestGrokGlmPricing:
    def test_grok45_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        # Grok 4.5: 1M in + 1M out at 2.00 / 6.00 → $8.00
        assert cost_usd_openai("grok-4.5", 1_000_000, 1_000_000) == pytest.approx(8.0)

    def test_grok43_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        assert cost_usd_openai("grok-4.3", 1_000_000, 1_000_000) == pytest.approx(1.25 + 2.50)

    def test_grok420_dated_variant_matches_prefix(self):
        from backend.ai.models.pricing import cost_usd_openai

        # The dated snapshot id resolves via the "grok-4.20" prefix.
        assert cost_usd_openai("grok-4.20-0309-reasoning", 1_000_000, 1_000_000) == pytest.approx(
            1.25 + 2.50
        )

    def test_glm52_flagship_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        assert cost_usd_openai("glm-5.2", 1_000_000, 1_000_000) == pytest.approx(1.40 + 4.40)

    def test_glm5_matches_after_more_specific_glm5x(self):
        from backend.ai.models.pricing import cost_usd_openai

        # "glm-5" must not shadow "glm-5.2"/"glm-5-turbo" — order guards this.
        assert cost_usd_openai("glm-5", 1_000_000, 1_000_000) == pytest.approx(1.00 + 3.20)
        assert cost_usd_openai("glm-5-turbo", 1_000_000, 1_000_000) == pytest.approx(1.20 + 4.00)

    def test_glm46_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        assert cost_usd_openai("glm-4.6", 1_000_000, 1_000_000) == pytest.approx(0.60 + 2.20)

    def test_glm45_airx_matches_before_air(self):
        from backend.ai.models.pricing import cost_usd_openai

        # "glm-4.5-airx" must match its own row, not "glm-4.5-air".
        assert cost_usd_openai("glm-4.5-airx", 1_000_000, 1_000_000) == pytest.approx(1.10 + 4.50)
        assert cost_usd_openai("glm-4.5-air", 1_000_000, 1_000_000) == pytest.approx(0.20 + 1.10)

    def test_glm_flash_is_free(self):
        from backend.ai.models.pricing import cost_usd_openai

        assert cost_usd_openai("glm-4.7-flash", 1_000_000, 1_000_000) == 0.0
        assert cost_usd_openai("glm-4.5-flash", 1_000_000, 1_000_000) == 0.0

    def test_openrouter_model_in_the_price_table_is_priced_from_its_rows(self):
        from backend.ai.models.pricing import cost_usd_openai

        cost = cost_usd_openai(
            "x-ai/grok-4", 100_000, 50_000, base_url="https://openrouter.ai/api/v1"
        )
        assert cost > 0.0

    def test_unknown_openrouter_style_id_zero(self, caplog):
        import logging

        from backend.ai.models.pricing import cost_usd_openai

        # A namespaced OpenRouter id with no row in the price table → 0 (unmeasured).
        with caplog.at_level(logging.WARNING, logger="backend.ai.models.pricing"):
            cost = cost_usd_openai(
                "acme/no-such-model", 100_000, 50_000, base_url="https://openrouter.ai/api/v1"
            )
        assert cost == 0.0
        assert any("unknown model" in r.message for r in caplog.records)


# ── OpenRouter cost capture ───────────────────────────────────────────────────


class TestOpenRouterCostCapture:
    def _provider(self, base_url):
        from backend.ai.models.openai_provider import OpenAIProvider

        return OpenAIProvider(base_url=base_url, api_key="sk-test")

    def test_openrouter_detected(self):
        assert self._provider("https://openrouter.ai/api/v1")._is_openrouter is True

    def test_non_openrouter_not_detected(self):
        assert self._provider("https://api.x.ai/v1")._is_openrouter is False

    def test_usage_request_extra_only_for_openrouter(self):
        assert self._provider("https://openrouter.ai/api/v1")._usage_request_extras() == {
            "extra_body": {"usage": {"include": True}}
        }
        assert self._provider("https://api.x.ai/v1")._usage_request_extras() == {}

    def test_attribution_headers_only_for_openrouter(self):
        headers = self._provider("https://openrouter.ai/api/v1")._default_headers()
        assert headers and "HTTP-Referer" in headers and "X-Title" in headers
        assert self._provider("https://api.openai.com/v1")._default_headers() is None

    def test_reported_cost_parsed(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        class _Usage:
            cost = 0.01234

        assert OpenAIProvider._reported_cost(_Usage()) == pytest.approx(0.01234)

    def test_reported_cost_absent_returns_none(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        class _Usage:
            prompt_tokens = 10

        assert OpenAIProvider._reported_cost(_Usage()) is None
        assert OpenAIProvider._reported_cost(None) is None

    def test_reported_cost_bad_value_returns_none(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        class _Usage:
            cost = "not-a-number"

        assert OpenAIProvider._reported_cost(_Usage()) is None


class TestLiteLLMHeaderCost:
    """A LiteLLM proxy reports true cost in an x-litellm-response-cost header — read it
    so a localhost proxy records real spend instead of the $0 local-endpoint default."""

    def _stream_with_headers(self, headers):
        class _Resp:
            def __init__(self, h):
                self.headers = h

        class _Stream:
            def __init__(self, h):
                self.response = _Resp(h)

        return _Stream(headers)

    def test_reads_header_cost(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        stream = self._stream_with_headers({"x-litellm-response-cost": "0.0123"})
        assert OpenAIProvider._litellm_header_cost(stream) == pytest.approx(0.0123)

    def test_absent_header_returns_none(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        stream = self._stream_with_headers({"content-type": "text/event-stream"})
        assert OpenAIProvider._litellm_header_cost(stream) is None

    def test_stream_without_response_returns_none(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        assert OpenAIProvider._litellm_header_cost(object()) is None

    def test_non_string_header_value_ignored(self):
        # A mocked stream's auto-attrs must not be mistaken for a real header value.
        from unittest.mock import MagicMock

        from backend.ai.models.openai_provider import OpenAIProvider

        assert OpenAIProvider._litellm_header_cost(MagicMock()) is None

    def test_unparseable_header_returns_none(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        stream = self._stream_with_headers({"x-litellm-response-cost": "free"})
        assert OpenAIProvider._litellm_header_cost(stream) is None


class TestCachedTokenExtraction:
    """Cache-hit tokens must be read across provider field conventions so the
    cache-read discount applies for DeepSeek-style providers too, not only OpenAI."""

    def test_standard_openai_nested_field(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        class _Details:
            cached_tokens = 512

        class _Usage:
            prompt_tokens_details = _Details()

        assert OpenAIProvider._extract_cached_tokens(_Usage()) == 512

    def test_deepseek_flat_field(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        class _Usage:
            prompt_tokens_details = None
            prompt_cache_hit_tokens = 1024

        assert OpenAIProvider._extract_cached_tokens(_Usage()) == 1024

    def test_standard_field_wins_when_both_present(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        class _Details:
            cached_tokens = 300

        class _Usage:
            prompt_tokens_details = _Details()
            prompt_cache_hit_tokens = 999

        assert OpenAIProvider._extract_cached_tokens(_Usage()) == 300

    def test_none_and_absent_return_zero(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        class _Usage:
            prompt_tokens = 10

        assert OpenAIProvider._extract_cached_tokens(None) == 0
        assert OpenAIProvider._extract_cached_tokens(_Usage()) == 0

    def test_discount_applied_via_extracted_cache_tokens(self):
        # End-to-end: cost_usd_openai bills cached tokens at THAT MODEL'S OWN
        # cache-read multiple. This assertion used to hardcode 0.50 for every
        # model; grok-4.5 bills cache reads at 0.25 ($0.50 cached vs $2.00 input
        # on docs.x.ai), independently confirmed against provider-reported
        # OpenRouter costs in tests/test_cache_read_discount.py.
        # 1M prompt of which 800k cached: 200k at full rate + 800k at a quarter.
        from backend.ai.models.pricing import cost_usd_openai

        cost = cost_usd_openai("grok-4.5", 1_000_000, 0, cached_tokens=800_000)
        expected = (200_000 * 2.00 + 800_000 * 2.00 * 0.25) / 1_000_000
        assert cost == pytest.approx(expected)


# ── Active-turn model introspection ("which model are you?") ──────────────────


class TestActiveTurnModel:
    """get_model_config must report the model ACTUALLY serving the turn, not just the
    instance default — the fix for "picked grok-4.5 but Lucius says Sonnet"."""

    def _get_model_config(self):
        import asyncio

        from backend.ai.tools import execute_tool

        text, _ = asyncio.run(execute_tool("get_model_config", {}))
        return json.loads(text)

    def test_context_var_roundtrip(self):
        from backend.core.model_context import get_active_model, set_active_model

        assert get_active_model() is None
        try:
            set_active_model({"model": "grok-4.5", "provider_key": "xai"})
            assert get_active_model()["model"] == "grok-4.5"
        finally:
            set_active_model(None)
        assert get_active_model() is None

    def test_active_turn_reported_when_set(self, cfg_env):
        from backend.core.model_context import set_active_model

        try:
            set_active_model(
                {
                    "model": "grok-4.5",
                    "provider_key": "xai",
                    "provider_label": "xAI (Grok)",
                    "transport": "openai_compatible",
                    "base_url": "https://api.x.ai/v1",
                }
            )
            out = self._get_model_config()
        finally:
            set_active_model(None)
        assert out.get("active_turn") == {
            "model": "grok-4.5",
            "provider_key": "xai",
            "provider_label": "xAI (Grok)",
        }
        # The instance default (Anthropic) is still reported separately — the two differ.
        assert out["provider"] == "anthropic"

    def test_active_turn_absent_outside_a_turn(self, cfg_env):
        from backend.core.model_context import set_active_model

        set_active_model(None)
        out = self._get_model_config()
        assert "active_turn" not in out

    def test_stamp_active_model_infers_provider_and_label(self):
        from backend.ai.assistant import _stamp_active_model
        from backend.core.model_context import get_active_model, set_active_model

        try:
            # No provider_key pinned → inferred from (transport, base_url) → xai + label.
            _stamp_active_model("grok-4.5", "", "openai_compatible", "https://api.x.ai/v1")
            active = get_active_model()
            assert active["provider_key"] == "xai"
            assert active["provider_label"] == "xAI (Grok)"
            assert active["model"] == "grok-4.5"
        finally:
            set_active_model(None)
