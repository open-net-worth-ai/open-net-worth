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

import hashlib

from backend.db.aggregator_config_store import AggregatorConfigStore


def bank_key(item_id: str) -> str:
    """The public handle for one linked bank — a hash of its Plaid ``item_id``, so the client
    can name a bank to remove without ever being handed the item id itself."""
    return hashlib.sha256(f"plaid-item:{item_id}".encode()).hexdigest()[:16]


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
    # LEGACY single-bank pair — read as one entry of ``items`` until the next link rewrites it.
    "access_token": "",
    "item_id": "",
    # Every linked bank (a Plaid Item), write-only: ``[{item_id, access_token, institution_id,
    # institution_name, linked_at}]``. Set only through ``add_item``/``remove_item``.
    "items": [],
    # The Hosted Link session in flight (write-only): Hosted Link never shows the user a public
    # token, so the app reads it back from ``/link/token/get`` with this token.
    "link_token": "",
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error
    "last_error": None,
    # A fetch that worked for some banks but not others (an expired login): which, and why.
    "last_caution": None,
    # Banks whose sign-in Plaid says must be renewed (update mode), by public ``bank_key``.
    "reconnect_needed": [],
    # The bank an in-flight update-mode link is for (``bank_key``), or None for a new bank.
    "pending_link_bank": None,
}


def _items_of(cfg: dict) -> list[dict]:
    """The linked banks in a merged config — the stored list, else the legacy single pair."""
    stored = [
        i
        for i in (cfg.get("items") or [])
        if isinstance(i, dict) and isinstance(i.get("access_token"), str) and i["access_token"]
    ]
    if stored:
        return stored
    token = str(cfg.get("access_token") or "").strip()
    if not token:
        return []
    return [
        {
            "item_id": str(cfg.get("item_id") or "").strip(),
            "access_token": token,
            "institution_id": "",
            "institution_name": "",
            "linked_at": None,
        }
    ]


class PlaidConfigStore(AggregatorConfigStore):
    KIND = "plaid"
    LABEL = "Plaid"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("client_id", "environment")
    SECRET_KEYS = ("secret", "access_token", "item_id", "link_token", "items")
    READ_ONLY_KEYS = ("items",)
    STATE_KEYS = (
        "last_synced_at",
        "last_status",
        "last_error",
        "last_caution",
        "reconnect_needed",
        "pending_link_bank",
    )

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

    def items(self, *, conn=None) -> list[dict]:
        """Every linked bank. A store from before multi-bank holds one bare
        ``access_token``/``item_id`` pair — that reads as a single, unnamed item."""
        return _items_of(self.get(conn=conn))

    def access_token(self) -> str:
        """The FIRST linked bank's access token (``""`` when none) — for a caller that predates
        multi-bank; ``items()`` is the whole list."""
        items = self.items()
        return str(items[0].get("access_token") or "").strip() if items else ""

    def add_item(self, item: dict) -> list[dict]:
        """Link one more bank — never drops another. An entry for the SAME Item is replaced.
        Read and write are ONE locked unit, so a link finishing while another bank is being
        removed can never lose either bank's token. Returns the new list."""
        entry = {**item, "linked_at": item.get("linked_at") or self._now()}
        with self._unit(None) as c:
            kept = [i for i in self.items(conn=c) if i.get("item_id") != item.get("item_id")]
            return self._write_items(c, [*kept, entry])

    def remove_item(self, item_id: str) -> list[dict]:
        """Unlink one bank (by its Plaid ``item_id``), in one locked unit. Returns the new list."""
        with self._unit(None) as c:
            kept = [i for i in self.items(conn=c) if i.get("item_id") != item_id]
            return self._write_items(c, kept)

    def _write_items(self, conn, items: list[dict]) -> list[dict]:
        self._ensure(conn)
        self._connectors.set_secrets(
            self.KIND, {"items": items, "access_token": None, "item_id": None}, conn=conn
        )
        return items

    def link_token(self) -> str:
        return (self.get().get("link_token") or "").strip()

    def item_by_key(self, key: str) -> dict | None:
        """The linked bank behind a public ``bank_key``, or None."""
        return next((i for i in self.items() if bank_key(i.get("item_id") or "") == key), None)

    def set_pending_link_bank(self, key: str | None) -> None:
        """Which bank the in-flight Hosted Link session renews (None = linking a new bank)."""
        self._record({"pending_link_bank": key})

    def pending_link_bank(self) -> str | None:
        return self.get().get("pending_link_bank") or None

    def mark_reconnect(self, *, needed: set[str], ok: set[str]) -> None:
        """Record which banks need their sign-in renewed: add ``needed``, drop ``ok`` (a bank
        that just answered, or was just reconnected). Keys of banks no longer linked drop out."""
        linked = {bank_key(i.get("item_id") or "") for i in self.items()}
        current = set(self.get().get("reconnect_needed") or [])
        new = sorted(((current | needed) - ok) & linked)
        if new != sorted(current):
            self._record({"reconnect_needed": new})

    def clear_link_token(self) -> None:
        """Forget a finished Hosted Link session (``update()`` treats blank as "keep")."""
        with self._unit(None) as c:
            self._ensure(c)
            self._connectors.set_secrets(self.KIND, {"link_token": None}, conn=c)

    def record_status(self, status: str, *, error=None) -> None:
        """A fetch's outcome — and it clears the previous run's caution (a fetch with
        per-bank failures sets a fresh one right after, ``connector_fetch.fetch``)."""
        self._record(
            {
                "last_synced_at": self._now(),
                "last_status": status,
                "last_error": error,
                "last_caution": None,
            }
        )

    def has_secret(self) -> bool:
        """Whether the client_id + secret credential pair is set."""
        return bool(self.client_id()) and bool(self.secret())

    def has_access_token(self) -> bool:
        """Whether at least one bank is linked (a public token was exchanged for an access token)."""
        return bool(self.items())

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
            # Each linked bank by NAME only — never its token or Plaid item id.
            "banks": [
                {
                    "key": bank_key(i.get("item_id") or ""),
                    "institution_name": i.get("institution_name") or "",
                    "linked_at": i.get("linked_at"),
                    "needs_reconnect": bank_key(i.get("item_id") or "")
                    in set(cfg.get("reconnect_needed") or []),
                }
                for i in self.items()
            ],
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
            "last_caution": cfg.get("last_caution"),
        }
