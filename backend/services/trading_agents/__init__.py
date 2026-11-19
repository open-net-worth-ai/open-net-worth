"""TradingAgents — multi-agent stock research as a plugin of Lucius (flag ``trading_agents``).

``availability()`` is the ONE seam that reads the release flag and the connector: the REST API,
Lucius's tools (``unavailable=``) and the runner all ask it, and ``require_ready()`` turns its
answer into the typed error a caller raises. Routers never branch on the flag.

This package is pure logic (settings, context, the run choke); the runner and the model bridge
live in ``ai/trading_agents`` because they call AI providers.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.errors import (
    FeatureDisabledError,
    ModelUnavailableError,
    NotInstalledError,
)
from backend.db.trading_agents_config_store import TradingAgentsConfigStore
from backend.services.trading_agents import settings

FLAG = "trading_agents"

FLAG_OFF = "flag_off"
CONNECTOR_OFF = "connector_off"
CONNECTOR_PAUSED = "connector_paused"
NOT_INSTALLED = "not_installed"
NEEDS_REINSTALL = "needs_reinstall"
INSTALLING = "installing"
MODEL_UNAVAILABLE = "model_unavailable"
READY = "ready"


@dataclass(frozen=True)
class Availability:
    status: str
    message: str

    @property
    def ready(self) -> bool:
        return self.status == READY


def flag_on() -> bool:
    from backend.services.feature_flags import enabled

    return enabled(FLAG)


def availability() -> Availability:
    """Whether a run can start, and if not, the first thing in the way (in this order): the
    release flag, the connector never set up, paused, not installed (or failed), installing,
    installed from a commit other than the pinned one, an AI model that cannot serve, else ready."""
    if not flag_on():
        return Availability(FLAG_OFF, "TradingAgents is not turned on.")
    saved = TradingAgentsConfigStore().read()
    if not saved["exists"]:
        return Availability(CONNECTOR_OFF, "Turn on the TradingAgents connector to use it.")
    if not saved["enabled"]:
        return Availability(CONNECTOR_PAUSED, "The TradingAgents connector is paused.")
    state = saved["state"]
    install = state.get("install_status")
    if install == "installing":
        return Availability(INSTALLING, "TradingAgents is still installing.")
    if install != "ready":
        detail = state.get("install_error") or "Install it from its connector page."
        return Availability(NOT_INSTALLED, f"TradingAgents is not installed. {detail}".strip())
    if state.get("installed_version") != _pinned_commit():
        return Availability(
            NEEDS_REINSTALL,
            "TradingAgents was installed from a different version than this app expects. "
            "Reinstall it from its connector page.",
        )
    try:
        settings.resolve_models(settings.load())
    except ModelUnavailableError as exc:
        return Availability(MODEL_UNAVAILABLE, exc.message)
    return Availability(READY, "Ready.")


def _pinned_commit() -> str:
    from backend.services.trading_agents import install

    return install.TA_COMMIT


def require_flag() -> None:
    if not flag_on():
        raise FeatureDisabledError("TradingAgents is not turned on.", status=FLAG_OFF)


def require_ready() -> None:
    """Raise the typed error for whatever keeps a run from starting; return when ready."""
    result = availability()
    if result.ready:
        return
    context = {"status": result.status}
    if result.status in (NOT_INSTALLED, INSTALLING, NEEDS_REINSTALL):
        raise NotInstalledError(result.message, **context)
    if result.status == MODEL_UNAVAILABLE:
        raise ModelUnavailableError(result.message, **context)
    raise FeatureDisabledError(result.message, **context)
