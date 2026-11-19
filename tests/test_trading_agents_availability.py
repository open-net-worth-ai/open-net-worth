"""The one seam that decides whether TradingAgents can run — synthetic data only.

``services.trading_agents.availability()`` reads the release flag and the connector in a fixed
order; off, the old app is untouched (no connector card), and every entry point refuses with a
typed error instead of branching on the flag.
"""

from __future__ import annotations

import pytest

from backend.core.errors import (
    FeatureDisabledError,
    ModelUnavailableError,
    NotInstalledError,
    to_http_status,
)
from backend.db.model_config_store import ModelConfigStore
from backend.db.trading_agents_config_store import TradingAgentsConfigStore
from backend.services import connectors as connectors_service
from backend.services import trading_agents
from backend.services.trading_agents import install, settings
from tests.flags import flags


@pytest.fixture(autouse=True)
def isolated_db(db_isolated):
    return db_isolated


def _model(providers=None):
    ModelConfigStore().save(
        {
            "default_model": "anthropic:model-a",
            "providers": providers or {"anthropic": {"api_key": "test-key"}},
        }
    )


def _connector(*, enabled=True, install_status=None, install_error=None, installed=True):
    store = TradingAgentsConfigStore()
    store.write(enabled=enabled)
    state = {
        "install_status": install_status,
        "install_error": install_error,
        "installed_version": install.TA_COMMIT if installed and install_status == "ready" else None,
    }
    store.merge_state({k: v for k, v in state.items() if v})


def _status() -> str:
    return trading_agents.availability().status


@pytest.mark.flag_both("trading_agents")
def test_the_flag_decides_whether_the_connector_exists_at_all():
    ids = {c.id for c in connectors_service.list_connectors()}
    if trading_agents.flag_on():
        assert "trading_agents" in ids
        assert _status() == trading_agents.CONNECTOR_OFF
    else:
        assert "trading_agents" not in ids
        assert _status() == trading_agents.FLAG_OFF


@pytest.mark.flag_both("trading_agents")
def test_every_entry_point_refuses_with_a_typed_error_when_it_cannot_run():
    if trading_agents.flag_on():
        with pytest.raises(FeatureDisabledError) as raised:
            trading_agents.require_ready()
        assert raised.value.context["status"] == trading_agents.CONNECTOR_OFF
        trading_agents.require_flag()
    else:
        with pytest.raises(FeatureDisabledError) as raised:
            trading_agents.require_flag()
        assert raised.value.context["status"] == trading_agents.FLAG_OFF
        with pytest.raises(FeatureDisabledError):
            trading_agents.require_ready()
    assert to_http_status(raised.value) == 409


@flags(trading_agents=True)
def test_a_paused_connector_is_not_the_same_as_one_never_set_up():
    _connector(enabled=False, install_status="ready")
    assert _status() == trading_agents.CONNECTOR_PAUSED


@flags(trading_agents=True)
def test_the_install_state_comes_before_the_model_check():
    _connector(install_status=None)
    assert _status() == trading_agents.NOT_INSTALLED
    _connector(install_status="installing")
    assert _status() == trading_agents.INSTALLING
    with pytest.raises(NotInstalledError):
        trading_agents.require_ready()
    _connector(install_status="failed", install_error="Install timed out.")
    result = trading_agents.availability()
    assert result.status == trading_agents.NOT_INSTALLED
    assert "Install timed out." in result.message
    with pytest.raises(NotInstalledError):
        trading_agents.require_ready()


@flags(trading_agents=True)
@pytest.mark.parametrize("version", [None, "0" * 40])
def test_an_install_from_another_commit_than_the_pin_needs_a_reinstall(version):
    _connector(install_status="ready", installed=False)
    if version:
        TradingAgentsConfigStore().merge_state({"installed_version": version})
    _model()
    result = trading_agents.availability()
    assert result.status == trading_agents.NEEDS_REINSTALL and "Reinstall" in result.message
    with pytest.raises(NotInstalledError):
        trading_agents.require_ready()


@flags(trading_agents=True)
def test_an_installed_connector_with_a_disabled_model_is_model_unavailable():
    _connector(install_status="ready")
    settings.update({"deep_model": "openai:model-b"})
    ModelConfigStore().save(
        {
            "default_model": "anthropic:model-a",
            "providers": {
                "anthropic": {"api_key": "k"},
                "openai": {"api_key": "k", "enabled": False},
            },
        }
    )
    result = trading_agents.availability()
    assert result.status == trading_agents.MODEL_UNAVAILABLE
    with pytest.raises(ModelUnavailableError):
        trading_agents.require_ready()


@flags(trading_agents=True)
def test_installed_enabled_and_a_usable_model_is_ready():
    _connector(install_status="ready")
    _model()
    result = trading_agents.availability()
    assert result.ready and result.status == trading_agents.READY
    trading_agents.require_ready()
