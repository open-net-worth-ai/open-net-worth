"""TradingAgentsConfigStore — the TradingAgents connector's settings, a thin facade over its
``connectors`` row (id ``trading_agents``; flag ``trading_agents``).

Where each field lives: ``enabled`` is the row's own flag (a pausable connector: off is paused);
the two optional DATA-vendor keys are secrets (``connector_secrets``) — there is no LLM key,
every model call goes through the owner's own AI provider; everything else is plain ``config``
and carries no credential; the install progress (``install_status``, ``installed_version``,
``install_error``) and ``last_run_at`` are app-written ``state``.

Data-only: ``services.trading_agents.settings`` validates what is written here.
"""

from __future__ import annotations

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work

CONNECTOR_ID = "trading_agents"
_NAME = "TradingAgents"
SECRET_KEYS = ("alpha_vantage_api_key", "fred_api_key")

CONFIG_DEFAULTS: dict = {
    "deep_model": None,  # None = the AI model chosen for Lucius
    "quick_model": None,  # None = the model assigned to Intel, else the deep one
    "analysts": ["market", "social", "news", "fundamentals"],
    "max_debate_rounds": 1,
    "max_risk_rounds": 1,
    "max_tool_rounds": 8,
    "max_llm_calls": 150,
    "bridge_concurrency": None,  # None = 4 on API transports, 2 on a CLI
    "per_run_cap_usd": 2.0,
    "monthly_cap_usd": 20.0,
    "context_level": "investments",
    "output_language": "English",
    "sec_edgar_user_agent": "OpenNetWorth TradingAgents",
    "disclaimer_accepted_at": None,
}


class TradingAgentsConfigStore:
    def __init__(self) -> None:
        self._store = ConnectorStore()

    def read(self) -> dict:
        """``{exists, enabled, config, state}``: ``config`` is the defaults under what was saved;
        ``exists`` is False until the connector was first written (it was never set up)."""
        row = self._store.get(CONNECTOR_ID)
        return {
            "exists": row is not None,
            "enabled": bool(row and row["enabled"]),
            "config": {**CONFIG_DEFAULTS, **(row["config"] if row else {})},
            "state": dict(row["state"]) if row else {},
        }

    def secrets(self) -> dict:
        """The data-vendor keys that hold a value (values included — runner use only)."""
        return {k: v for k, v in self._store.secrets(CONNECTOR_ID).items() if v}

    def write(
        self,
        *,
        config: dict | None = None,
        secrets: dict | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Save a validated patch in one transaction. A ``None`` config value resets that key to
        its default; a blank secret keeps the saved one and ``None`` clears it."""
        with serialized_unit_of_work() as conn:
            self._store.ensure(CONNECTOR_ID, CONNECTOR_ID, _NAME, conn=conn)
            if enabled is not None:
                self._store.set_enabled(CONNECTOR_ID, enabled, conn=conn)
            if config:
                self._store.patch_config(CONNECTOR_ID, config, conn=conn)
            if secrets:
                self._store.set_secrets(CONNECTOR_ID, secrets, conn=conn)

    def merge_state(self, changes: dict) -> None:
        with serialized_unit_of_work() as conn:
            self._store.ensure(CONNECTOR_ID, CONNECTOR_ID, _NAME, conn=conn)
            self._store.merge_state(CONNECTOR_ID, changes, conn=conn)
