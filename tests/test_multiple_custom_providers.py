"""Several OpenAI-compatible endpoints can be kept at once: each extra one is a
``custom_<id>`` key that resolves like the generic ``custom`` row, so saving a second
never overwrites the first."""

from __future__ import annotations

import pytest

from backend.db.model_config_store import ModelConfigStore, _provider_entry, is_custom_key


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    s = ModelConfigStore()
    s.update({"provider": "anthropic", "model": "test-model"})
    return s


def test_extra_custom_keys_resolve_like_custom():
    entry = _provider_entry("custom_ab12cd34")
    assert entry is not None
    assert entry["key"] == "custom_ab12cd34"
    assert entry["transport"] == "openai_compatible"
    assert entry["editable_base_url"] is True


@pytest.mark.parametrize(
    "key", ["custom_", "custom_UPPER", "customx", "custom_a:b", "custom_" + "a" * 25]
)
def test_malformed_custom_keys_stay_unknown(key):
    assert not is_custom_key(key)
    assert _provider_entry(key) is None


def test_two_custom_endpoints_coexist(store):
    store.update_provider(
        "custom", {"base_url": "https://one.example/v1", "api_key": "k1", "label": "Work gateway"}
    )
    store.update_provider(
        "custom_ab12cd34",
        {"base_url": "https://two.example/v1", "api_key": "k2", "label": "Home server"},
    )

    public = store.providers_public()
    assert public["custom"]["base_url"] == "https://one.example/v1"
    assert public["custom"]["label"] == "Work gateway"
    assert public["custom_ab12cd34"]["base_url"] == "https://two.example/v1"
    assert public["custom_ab12cd34"]["label"] == "Home server"

    assert store.resolve_for_provider("custom")["api_key"] == "k1"
    second = store.resolve_for_provider("custom_ab12cd34")
    assert (second["base_url"], second["api_key"]) == ("https://two.example/v1", "k2")
    assert {"custom", "custom_ab12cd34"} <= set(store.configured_provider_keys())
    assert store.display_label("custom_ab12cd34") == "Home server"


def test_label_is_ignored_for_a_preset_and_removing_one_keeps_the_other(store):
    store.update_provider("openrouter", {"api_key": "k", "label": "Renamed"})
    assert "label" not in store.providers_public()["openrouter"]

    store.update_provider("custom", {"base_url": "https://one.example/v1", "api_key": "k1"})
    store.update_provider(
        "custom_ab12cd34", {"base_url": "https://two.example/v1", "api_key": "k2"}
    )
    store.remove_provider("custom")
    assert "custom" not in store.providers_public()
    assert store.resolve_for_provider("custom_ab12cd34")["api_key"] == "k2"


def test_a_url_alone_finds_the_custom_endpoint_that_owns_it(store):
    from backend.db.model_config_store import _infer_provider_key, provider_label_for

    store.update_provider("custom", {"base_url": "https://one.example/v1", "label": "Work gateway"})
    store.update_provider(
        "custom_ab12cd34", {"base_url": "https://two.example/v1/", "label": "Home server"}
    )

    # Cost attribution, vision refs and the model list only know (transport, base_url).
    assert _infer_provider_key("openai_compatible", "https://two.example/v1") == "custom_ab12cd34"
    assert _infer_provider_key("openai_compatible", "https://one.example/v1") == "custom"
    assert _infer_provider_key("openai_compatible", "https://nobody.example/v1") == "custom"
    assert provider_label_for("openai_compatible", "https://two.example/v1") == "Home server"
    assert provider_label_for("openai_compatible", "https://one.example/v1") == "Work gateway"
    assert provider_label_for("openai_compatible", "https://nobody.example/v1") == (
        "Custom (OpenAI-compatible)"
    )


def test_cost_provider_for_a_pinned_custom_ref_uses_its_name(store):
    from backend.services.cost import provider_of_model

    store.update_provider(
        "custom_ab12cd34", {"base_url": "https://two.example/v1", "label": "Home"}
    )
    assert provider_of_model("custom_ab12cd34:some-model") == "Home"
