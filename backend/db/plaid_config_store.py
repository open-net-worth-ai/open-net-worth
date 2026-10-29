"""PlaidConfigStore — config + fetch state for the Plaid bank-aggregation connector.

A facade over the ``plaid`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``). The
**client_id** and the chosen **environment** (``sandbox`` | ``production``) are settings, in
``connectors.config``; the **secret** and the **access_token** (a bearer credential minted by
the public-token exchange) plus the linked Item's **item_id** are in ``connector_secrets`` (the
last two machine-minted, never revealed); the master ``enabled`` flag is ``connectors.enabled``;
the latest status and last-fetched timestamp are ``connectors.state``. There is no scheduled
poll and no provider-account map (RFC ``connectors-as-data-sources``): fetching uses
``/transactions/get`` with an explicit date window every call, and the provider-account →
app-account link lives on ``Account.external_ids``.

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE — the secret + access token are bearer credentials,
    anyone with them can read the user's bank balances;
  * ``public()`` exposes only ``has_secret`` / ``has_access_token`` (and non-secret flags);
  * a blank ``secret`` / ``access_token`` on update means "leave the saved one unchanged";
  * the value is NOT unreachable: the logged-in OPERATOR reads it back through
    ``services.connector_secrets`` (``GET /api/connectors/{id}/secret/{field}``), which is the
    ONLY module that returns one. A MODEL never can — ADR
    ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the Plaid
protocol + the sync orchestration live in ``services/aggregation/plaid.py``.
"""

from __future__ import annotations

from backend.db.aggregator_config_store import AggregatorConfigStore

# Plaid's per-environment API host. ``sandbox`` is free + uses test banks; ``production`` is
# the live host. (Plaid retired its standalone ``development`` host in 2024 — production is
# the only live tier — so we offer just the two.)
PLAID_HOSTS: dict = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}

DEFAULTS: dict = {
    "enabled": False,  # master switch
    "client_id": "",  # Plaid client_id (treated as part of the credential pair)
    "secret": "",  # WRITE-ONLY — never returned to the frontend
    "environment": "sandbox",  # sandbox | production
    # The bearer access token minted by the public-token exchange (write-only) + the Item id.
    "access_token": "",
    "item_id": "",
    # The Hosted Link session in flight (write-only): Hosted Link never shows the user a public
    # token, so the app reads it back from ``/link/token/get`` with this token.
    "link_token": "",
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error
    "last_error": None,
}


class PlaidConfigStore(AggregatorConfigStore):
    KIND = "plaid"
    LABEL = "Plaid"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("client_id", "environment")
    SECRET_KEYS = ("secret", "access_token", "item_id", "link_token")
    STATE_KEYS = ("last_synced_at", "last_status", "last_error")

    def _normalized(self, merged: dict) -> dict:
        if merged.get("environment") not in PLAID_HOSTS:
            merged["environment"] = DEFAULTS["environment"]
        return merged

    def _normalize(self, clean: dict) -> None:
        if "environment" in clean:
            env = str(clean["environment"] or "").strip().lower()
            if env in PLAID_HOSTS:
                clean["environment"] = env
            else:
                clean.pop("environment")

    # ── status helpers ───────────────────────────────────────────────────────────

    def client_id(self) -> str:
        return (self.get().get("client_id") or "").strip()

    def secret(self) -> str:
        return (self.get().get("secret") or "").strip()

    def environment(self) -> str:
        env = (self.get().get("environment") or "").strip().lower()
        return env if env in PLAID_HOSTS else DEFAULTS["environment"]

    def api_base(self) -> str:
        """The Plaid API host for the configured environment."""
        return PLAID_HOSTS[self.environment()]

    def access_token(self) -> str:
        return (self.get().get("access_token") or "").strip()

    def link_token(self) -> str:
        return (self.get().get("link_token") or "").strip()

    def clear_link_token(self) -> None:
        """Forget a finished Hosted Link session (``update()`` treats blank as "keep")."""
        with self._unit(None) as c:
            self._ensure(c)
            self._connectors.set_secrets(self.KIND, {"link_token": None}, conn=c)

    def has_secret(self) -> bool:
        """Whether the client_id + secret credential pair is set."""
        return bool(self.client_id()) and bool(self.secret())

    def has_access_token(self) -> bool:
        """Whether an Item has been linked (a public token was exchanged for an access token)."""
        return bool(self.access_token())

    def is_configured(self) -> bool:
        """Whether the connector is ready to FETCH — credentials AND a linked Item (the
        access token ``/transactions/get`` needs)."""
        return self.has_secret() and self.has_access_token()

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the secrets stripped → ``has_secret`` / ``has_access_token`` flags."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "environment": self.environment(),
            "has_secret": self.has_secret(),
            "client_id": cfg.get("client_id") or "",  # the id is not secret (the secret is)
            "has_access_token": self.has_access_token(),
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
