"""TradingAgents settings: validation, and which AI models a run uses — synthetic data only.

The models are resolved against the owner's own AI providers and never rerouted: a provider turned
off in Settings makes the integration report ``model_unavailable`` instead of using another one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.errors import ModelUnavailableError, ValidationError
from backend.db.model_config_store import ModelConfigStore
from backend.db.trading_agents_config_store import (
    CONFIG_DEFAULTS,
    SECRET_KEYS,
    TradingAgentsConfigStore,
)
from backend.services.connector_catalog import CATALOG
from backend.services.trading_agents import settings


def _models(default="anthropic:model-a", providers=None, task_models=None):
    ModelConfigStore().save(
        {
            "default_model": default,
            "providers": providers or {"anthropic": {"api_key": "test-key"}},
            "task_models": task_models or {},
        }
    )


def test_the_facade_and_the_catalog_agree_on_the_kind_s_keys():
    kind = CATALOG["trading_agents"]
    assert set(kind.config_keys) == set(CONFIG_DEFAULTS)
    assert {f.name for f in kind.secret_fields} == set(SECRET_KEYS)


def test_defaults_are_the_specified_ones(db_isolated):
    cfg = settings.load()
    assert cfg.analysts == ("market", "social", "news", "fundamentals")
    assert (cfg.max_debate_rounds, cfg.max_risk_rounds, cfg.max_tool_rounds) == (1, 1, 8)
    assert (cfg.max_llm_calls, cfg.per_run_cap_usd, cfg.monthly_cap_usd) == (150, 2.0, 20.0)
    assert cfg.context_level == "investments"
    assert cfg.deep_model is None and cfg.quick_model is None
    assert cfg.disclaimer_accepted_at is None


def test_a_valid_patch_is_saved_and_none_resets_a_key(db_isolated):
    settings.update({"max_tool_rounds": 12, "analysts": ["news", "market"], "per_run_cap_usd": 1})
    cfg = settings.load()
    assert cfg.max_tool_rounds == 12
    assert cfg.analysts == ("market", "news")  # in the order TradingAgents lists them
    assert cfg.per_run_cap_usd == 1.0
    settings.update({"max_tool_rounds": None})
    assert settings.load().max_tool_rounds == CONFIG_DEFAULTS["max_tool_rounds"]


@pytest.mark.parametrize(
    "changes",
    [
        {"max_debate_rounds": 0},
        {"max_debate_rounds": 4},
        {"max_risk_rounds": True},
        {"max_tool_rounds": 1},
        {"max_tool_rounds": 21},
        {"max_llm_calls": 19},
        {"max_llm_calls": 601},
        {"bridge_concurrency": 9},
        {"per_run_cap_usd": 0.05},
        {"per_run_cap_usd": 51},
        {"monthly_cap_usd": -1},
        {"monthly_cap_usd": 1001},
        {"analysts": []},
        {"analysts": ["market", "crypto"]},
        {"analysts": "market"},
        {"context_level": "everything"},
        {"output_language": ""},
        {"output_language": "x" * 33},
        {"sec_edgar_user_agent": "two\nlines"},
        {"deep_model": "no-provider-prefix"},
        {"quick_model": ":model"},
        {"made_up_key": 1},
        {"disclaimer_accepted_at": "2026-01-01"},  # only the app records it
    ],
)
def test_an_out_of_range_or_unknown_setting_is_refused_and_nothing_is_saved(db_isolated, changes):
    with pytest.raises(ValidationError):
        settings.update({"max_tool_rounds": 9, **changes})
    assert settings.load().max_tool_rounds == CONFIG_DEFAULTS["max_tool_rounds"]


def test_the_monthly_cap_may_be_zero_to_forbid_metered_runs(db_isolated):
    settings.update({"monthly_cap_usd": 0})
    assert settings.load().monthly_cap_usd == 0.0


def test_data_vendor_keys_are_stored_as_secrets_and_never_in_the_config(db_isolated):
    settings.update({}, secrets={"fred_api_key": " fred-test-value "})
    assert settings.data_vendor_env() == {"FRED_API_KEY": "fred-test-value"}
    assert "fred_api_key" not in TradingAgentsConfigStore().read()["config"]
    with pytest.raises(ValidationError):
        settings.update({}, secrets={"openai_api_key": "x"})  # there is no LLM key here


@pytest.mark.parametrize("value", ["a" * 257, "line\nbreak", "nul\0byte", "carriage\rreturn"])
def test_a_data_vendor_key_that_cannot_be_an_environment_value_is_refused(db_isolated, value):
    with pytest.raises(ValidationError):
        settings.update({}, secrets={"fred_api_key": value})
    assert settings.data_vendor_env() == {}


def test_an_unsafe_key_already_saved_never_reaches_the_environment(db_isolated):
    store = TradingAgentsConfigStore()
    store.write(secrets={"fred_api_key": "ok-key", "alpha_vantage_api_key": "bad\nvalue"})
    assert settings.data_vendor_env() == {"FRED_API_KEY": "ok-key"}


def test_only_the_runner_reads_the_data_vendor_keys_as_environment_values():
    root = Path(__file__).resolve().parents[1] / "backend"
    readers = {
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "data_vendor_env" in path.read_text(encoding="utf-8")
    }
    assert readers == {
        "services/trading_agents/settings.py",
        "ai/trading_agents/worker_env.py",
    }


def test_accepting_the_disclaimer_records_when(db_isolated):
    assert settings.load().disclaimer_accepted_at is None
    assert settings.accept_disclaimer().disclaimer_accepted_at


# ── model resolution ─────────────────────────────────────────────────────────────────────


def test_unset_models_follow_the_instance_default_and_the_intel_assignment(db_isolated):
    _models(
        providers={
            "anthropic": {"api_key": "sentinel-key-1"},
            "openai": {"api_key": "sentinel-key-2"},
        },
        task_models={"intel": "openai:model-b"},
    )
    models = settings.resolve_models(settings.load())
    assert (models.deep, models.quick) == ("anthropic:model-a", "openai:model-b")
    assert models.as_dict() == {
        "deep": "anthropic:model-a",
        "quick": "openai:model-b",
        "transports": {"deep": "anthropic", "quick": "openai_compatible"},
    }
    assert "sentinel-key" not in str(models)  # no key in the result


def test_quick_falls_back_to_deep_when_intel_has_no_model(db_isolated):
    _models()
    models = settings.resolve_models(settings.load())
    assert models.quick == models.deep == "anthropic:model-a"


def test_an_explicit_model_wins_over_the_defaults(db_isolated):
    _models(
        providers={"anthropic": {"api_key": "k"}, "openai": {"api_key": "k"}},
        task_models={"intel": "anthropic:model-c"},
    )
    settings.update({"deep_model": "openai:model-d", "quick_model": "openai:model-e"})
    models = settings.resolve_models(settings.load())
    assert (models.deep, models.quick) == ("openai:model-d", "openai:model-e")


def test_a_disabled_provider_is_refused_never_rerouted(db_isolated):
    _models(providers={"anthropic": {"api_key": "k"}, "openai": {"api_key": "k", "enabled": False}})
    settings.update({"quick_model": "openai:model-d"})
    with pytest.raises(ModelUnavailableError) as raised:
        settings.resolve_models(settings.load())
    assert raised.value.context["model_ref"] == "openai:model-d"
    assert "turned off" in raised.value.message


def test_an_unknown_provider_is_refused_not_read_as_a_model_of_the_default(db_isolated):
    _models()
    settings.update({"deep_model": "nonesuch:model-d"})
    with pytest.raises(ModelUnavailableError):
        settings.resolve_models(settings.load())


def test_no_default_model_is_model_unavailable(db_isolated):
    ModelConfigStore().save({"providers": {}})
    with pytest.raises(ModelUnavailableError):
        settings.resolve_models(settings.load())


def test_a_cli_model_resolves_with_its_transport_and_gets_fewer_parallel_calls(db_isolated):
    _models(default="claude_cli:model-a", providers={})
    cfg = settings.load()
    models = settings.resolve_models(cfg)
    assert models.deep_transport == "claude_cli" and models.uses_cli
    assert settings.bridge_concurrency(cfg, models) == settings.CLI_CONCURRENCY == 2
    settings.update({"bridge_concurrency": 6})
    assert settings.bridge_concurrency(settings.load(), models) == 6


@pytest.fixture
def billing(monkeypatch):
    """Answers the CLI-billing question the AI layer normally answers, and puts the seam back."""
    answers: dict = {}
    monkeypatch.setattr(settings, "_cli_billing", lambda key, entry: answers.get(key, False))
    return answers


def test_a_cli_is_plan_included_only_when_its_own_billing_basis_says_so(db_isolated, billing):
    _models(default="claude_cli:model-a", providers={})
    assert not settings.resolve_models(settings.load()).plan_included  # unknown means metered
    billing["claude_cli"] = True
    models = settings.resolve_models(settings.load())
    assert models.deep_plan_included and models.quick_plan_included and models.plan_included


def test_a_cli_with_a_stored_key_is_metered_whatever_its_basis_says(db_isolated, billing):
    billing["claude_cli"] = True
    _models(default="claude_cli:model-a", providers={"claude_cli": {"api_key": "test-key"}})
    models = settings.resolve_models(settings.load())
    assert models.deep_keyed and not models.plan_included


def test_an_api_model_never_asks_the_cli_billing_question(db_isolated, monkeypatch):
    def boom(*_args):
        raise AssertionError("asked")

    monkeypatch.setattr(settings, "_cli_billing", boom)
    _models()
    assert not settings.resolve_models(settings.load()).uses_cli


def test_a_cli_with_no_registered_probe_is_metered(db_isolated, monkeypatch):
    monkeypatch.setattr(settings, "_cli_billing", settings._unknown_billing)
    _models(default="claude_cli:model-a", providers={})
    assert not settings.resolve_models(settings.load()).plan_included


def test_api_models_get_four_parallel_calls_by_default(db_isolated):
    _models()
    cfg = settings.load()
    models = settings.resolve_models(cfg)
    assert not models.uses_cli
    assert settings.bridge_concurrency(cfg, models) == settings.API_CONCURRENCY == 4


def test_a_per_run_context_level_override_is_validated(db_isolated):
    cfg = settings.load()
    assert settings.with_context_level(cfg, None) is cfg
    assert settings.with_context_level(cfg, "ticker").context_level == "ticker"
    with pytest.raises(ValidationError):
        settings.with_context_level(cfg, "all")
