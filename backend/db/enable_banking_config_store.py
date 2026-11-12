"""EnableBankingConfigStore — config + fetch state for the Enable Banking aggregation connector.

A facade over the ``enable_banking`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``).
The **application_id** (the JWT ``kid``) and the chosen bank (**aspsp** + **country**) are
settings, in ``connectors.config``; the application's RSA **private_key** (PEM) and the
post-consent **session_id** (machine-minted, never revealed) are in ``connector_secrets``;
``enabled`` is ``connectors.enabled``; the latest status and last-fetched timestamp are
``connectors.state``. There is no scheduled poll and no provider-account map (RFC
``connectors-as-data-sources``): the provider-account → app-account link lives on
``Account.external_ids``.

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE — the private key + session can read bank balances;
  * ``public()`` exposes only ``has_credentials`` / ``has_session`` — the private key is NEVER
    returned to a client;
  * a blank ``private_key`` on update means "leave the saved one unchanged";
  * the value is NOT unreachable: the logged-in OPERATOR reads it back through
    ``services.connector_secrets`` (``GET /api/connectors/{id}/secret/{field}``), which is the
    ONLY module that returns one. A MODEL never can — ADR
    ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the Enable
Banking protocol + sync orchestration live in ``services/aggregation/enable_banking.py``.
"""

from __future__ import annotations

from backend.db.aggregator_config_store import AggregatorConfigStore

DEFAULTS: dict = {
    "enabled": False,  # master switch
    # Application credentials (write-only secrets — never returned to the frontend).
    "application_id": "",  # the Enable Banking application id (used as the JWT `kid`)
    "private_key": "",  # the application's RSA private key (PEM) — WRITE-ONLY
    # The chosen bank + the consent handle returned by the link/exchange flow.
    "aspsp": "",  # the chosen bank id/name (Enable Banking calls a bank an "ASPSP")
    "country": "",  # the ISO 3166 alpha-2 country of the chosen bank
    "session_id": "",  # the post-consent session id (the per-bank consent handle)
    # The `state` the last /auth sent; the bank's return must carry it back (not a credential).
    "pending_state": "",
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error
    "last_error": None,
}


class EnableBankingConfigStore(AggregatorConfigStore):
    KIND = "enable_banking"
    LABEL = "Enable Banking"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("application_id", "aspsp", "country")
    SECRET_KEYS = ("private_key", "session_id")
    STATE_KEYS = ("last_synced_at", "last_status", "last_error", "pending_state")

    # ── status helpers ───────────────────────────────────────────────────────────

    def application_id(self) -> str:
        return (self.get().get("application_id") or "").strip()

    def private_key(self) -> str:
        return self.get().get("private_key") or ""

    def session_id(self) -> str:
        return (self.get().get("session_id") or "").strip()

    def has_credentials(self) -> bool:
        """Whether the application_id + private_key credential pair is set."""
        return bool(self.application_id()) and bool(self.private_key().strip())

    def has_session(self) -> bool:
        return bool(self.session_id())

    def is_configured(self) -> bool:
        """Whether the connector is ready to FETCH — credentials AND a linked session (the
        per-bank consent handle the accounts/transactions endpoints need)."""
        return self.has_credentials() and self.has_session()

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the secrets stripped → ``has_credentials`` / ``has_session`` flags."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "has_credentials": self.has_credentials(),
            "application_id": cfg.get("application_id") or "",  # the id is not the secret (key is)
            "aspsp": cfg.get("aspsp") or "",
            "country": cfg.get("country") or "",
            "has_session": self.has_session(),
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
