"""IbkrConfigStore — config + poll state for the IBKR Flex Web Service connector.

A facade over the ``ibkr`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``). The Flex
**token** (a read-only credential that can only QUERY — cannot trade) is in
``connector_secrets``; the **flex_query_id** (the saved query number from IBKR Account
Management) and the Flex Web Service **base_url** are settings, in ``connectors.config``; the
master ``enabled`` flag (READ sync) is ``connectors.enabled``; the latest status and
last-synced timestamp are ``connectors.state``. The provider-account → app-account link lives
on ``Account.external_ids``.

This connector is READ-ONLY. The ``trading_enabled`` kill switch was REMOVED 2026-07-20 —
it gated a capability that never existed (no order tool, no order wire call, and the Flex
token cannot place orders even in principle). See
``docs/adr/2026-07-20-remove-ibkr-dead-trading-flag.md``. Do NOT re-add it.

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE — a restore must never resurrect a superseded or
    revoked token;
  * ``public()`` exposes only ``has_token`` (+ non-secret status/flags);
  * a blank ``flex_token`` on update means "leave the saved value unchanged" (write-only);
  * the value is NOT unreachable: the logged-in OPERATOR reads it back through
    ``services.connector_secrets`` (``GET /api/connectors/{id}/secret/{field}``), which is the
    ONLY module that returns one. A MODEL never can — ADR
    ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the Flex wire
protocol + the position/trade mapping live in ``services/aggregation/ibkr.py``.
"""

from __future__ import annotations

from backend.db.aggregator_config_store import AggregatorConfigStore

DEFAULTS: dict = {
    "enabled": False,  # master switch for READ fetch
    "flex_token": "",  # WRITE-ONLY — never returned to the frontend
    "flex_query_id": "",  # the saved Flex query number from IBKR Account Management
    "base_url": "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService",
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | rate_limited | connect_error | error
    "last_error": None,
}


class IbkrConfigStore(AggregatorConfigStore):
    KIND = "ibkr"
    LABEL = "IBKR Flex"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("flex_query_id", "base_url")
    SECRET_KEYS = ("flex_token",)
    STATE_KEYS = ("last_synced_at", "last_status", "last_error")

    def _normalize(self, clean: dict) -> None:
        if "flex_query_id" in clean:
            clean["flex_query_id"] = str(clean["flex_query_id"]).strip()

    # ── helpers ──────────────────────────────────────────────────────────────────

    def flex_token(self) -> str:
        return (self.get().get("flex_token") or "").strip()

    def flex_query_id(self) -> str:
        return (self.get().get("flex_query_id") or "").strip()

    def is_configured(self) -> bool:
        """Whether both flex_token AND flex_query_id are set."""
        return bool(self.flex_token()) and bool(self.flex_query_id())

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the token stripped → a ``has_token`` flag instead. Surfaces the
        non-secret status and the ``enabled`` read switch."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "has_token": bool(self.flex_token()),
            "flex_query_id": cfg.get("flex_query_id") or "",  # not a secret, ok to show
            "enabled": cfg["enabled"],
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
