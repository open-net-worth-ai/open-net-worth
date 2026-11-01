"""GoCardlessConfigStore — config + fetch state for the GoCardless Bank Account Data connector.

A facade over the ``gocardless`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``).
The **secret_id** (the public half of the user-secret pair, like Plaid's ``client_id``) and the
chosen bank (**institution_id** + **country**) are settings, in ``connectors.config``; the
**secret_key**, the cached bearer **access_token** and the **requisition_id** (the per-bank
consent handle — with a token it reads the bank's accounts, so it is kept as a secret) are in
``connector_secrets``; ``enabled`` is ``connectors.enabled``. App-written state lives in
``connectors.state``: the last fetch status, the access token's expiry, the requisition's last
known status (``LN`` = linked) and ``accounts_meta`` — each consented account's name/currency/
type, cached because GoCardless rate-limits ``/details/`` per account per day (as low as 4).

The provider-account → app-account link lives on ``Account.external_ids`` (RFC
``connectors-as-data-sources``); there is no account map and no scheduled poll here.

Secret discipline matches ``EnableBankingConfigStore``: ``public()`` returns only
``has_credentials`` / ``has_link`` flags, a blank ``secret_key`` on update keeps the saved one,
the operator reads it back only through ``services.connector_secrets``, a model never can.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the protocol
lives in ``services/aggregation/gocardless.py``.
"""

from __future__ import annotations

from typing import Optional

from backend.db.aggregator_config_store import AggregatorConfigStore

# GoCardless's requisition status for "the user finished consent; accounts are readable".
LINKED = "LN"
# Requisition statuses that never become LN again — only a new link recovers them.
ENDED = {"EX": "expired", "RJ": "refused", "SU": "suspended", "ER": "failed"}

DEFAULTS: dict = {
    "enabled": False,
    "secret_id": "",
    "secret_key": "",  # WRITE-ONLY — never returned to the frontend
    "institution_id": "",
    "country": "",
    "requisition_id": "",  # machine-minted, never revealed
    "access_token": "",  # machine-minted, never revealed
    "access_expires_at": None,  # ISO timestamp
    "requisition_status": "",  # GoCardless StatusEnum: CR, GC, UA, GA, SA, LN, EX, RJ, …
    "consent_expires_at": None,  # ISO timestamp, approximate (from the agreement's length)
    "accounts_meta": {},  # GoCardless account id → {name, currency, mask, account_type}
    "last_synced_at": None,
    "last_status": "never_run",  # never_run | ok | auth_error | rate_limited | connect_error | error
    "last_error": None,
}


class GoCardlessConfigStore(AggregatorConfigStore):
    KIND = "gocardless"
    LABEL = "GoCardless"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("secret_id", "institution_id", "country")
    SECRET_KEYS = ("secret_key", "access_token", "requisition_id")
    STATE_KEYS = (
        "access_expires_at",
        "requisition_status",
        "consent_expires_at",
        "accounts_meta",
        "last_synced_at",
        "last_status",
        "last_error",
    )

    def _normalized(self, merged: dict) -> dict:
        if not isinstance(merged.get("accounts_meta"), dict):
            merged["accounts_meta"] = {}
        return merged

    # ── reads ────────────────────────────────────────────────────────────────

    def secret_id(self) -> str:
        return (self.get().get("secret_id") or "").strip()

    def secret_key(self) -> str:
        return (self.get().get("secret_key") or "").strip()

    def requisition_id(self) -> str:
        return (self.get().get("requisition_id") or "").strip()

    def accounts_meta(self) -> dict:
        return dict(self.get().get("accounts_meta") or {})

    def has_credentials(self) -> bool:
        cfg = self.get()
        return bool((cfg.get("secret_id") or "").strip() and (cfg.get("secret_key") or "").strip())

    def has_link(self) -> bool:
        """A requisition exists and GoCardless last reported it as linked."""
        cfg = self.get()
        return bool(cfg.get("requisition_id")) and cfg.get("requisition_status") == LINKED

    def is_configured(self) -> bool:
        """Credentials AND a requisition. An unfinished or expired one still counts: the fetch
        then says why it can't read the bank (``auth_error``) instead of ``not_configured``."""
        return self.has_credentials() and bool(self.requisition_id())

    # ── app-owned writes ─────────────────────────────────────────────────────

    def save_token(self, access_token: Optional[str], expires_at: Optional[str]) -> None:
        """Cache (or, with ``None``, forget) the bearer and its expiry in one write."""
        with self._unit(None) as c:
            self._ensure(c)
            self._apply(c, {"access_token": access_token, "access_expires_at": expires_at})

    def clear_token(self) -> None:
        """Forget the cached bearer so the next call mints a fresh one."""
        self.save_token(None, None)

    def start_link(
        self,
        institution_id: str,
        country: str,
        requisition_id: str,
        consent_expires_at: Optional[str],
    ) -> None:
        """A new requisition replaces the old one: new bank, fresh status, no cached accounts."""
        with self._unit(None) as c:
            self._ensure(c)
            self._apply(
                c,
                {
                    "institution_id": institution_id,
                    "country": country,
                    "requisition_id": requisition_id,
                    "requisition_status": "CR",
                    "consent_expires_at": consent_expires_at,
                    "accounts_meta": {},
                },
            )

    def record_requisition_status(self, status: str) -> None:
        self._record({"requisition_status": status})

    def cache_account_meta(self, account_id: str, meta: dict) -> None:
        with self._unit(None) as c:
            current = self.get(conn=c).get("accounts_meta") or {}
            self._record({"accounts_meta": {**current, account_id: meta}}, conn=c)

    # ── public (non-secret) view for the API ─────────────────────────────────

    def public(self) -> dict:
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "has_credentials": self.has_credentials(),
            "secret_id": cfg.get("secret_id") or "",
            "institution_id": cfg.get("institution_id") or "",
            "country": cfg.get("country") or "",
            "has_requisition": bool(cfg.get("requisition_id")),
            "requisition_status": cfg.get("requisition_status") or "",
            "has_link": self.has_link(),
            "consent_expires_at": cfg.get("consent_expires_at"),
            "linked_accounts": len(cfg.get("accounts_meta") or {}),
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
