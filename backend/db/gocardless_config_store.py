"""GoCardlessConfigStore — config + fetch state for the GoCardless Bank Account Data connector.

A facade over the ``gocardless`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``).
The **secret_id** (the public half of the user-secret pair, like Plaid's ``client_id``) is a
setting, in ``connectors.config``; the **secret_key**, the cached bearer **access_token** and
the **requisitions** (one per linked bank — with a token a requisition id reads that bank's
accounts, so the list is kept as a secret) are in ``connector_secrets``; ``enabled`` is
``connectors.enabled``. App-written state lives in ``connectors.state``: the last fetch status,
the access token's expiry, ``banks`` — each linked bank's name, last known requisition status
(``LN`` = linked), consent expiry and account ids, keyed by its public ``bank_key`` — and
``accounts_meta``, each consented account's name/currency/type, cached because GoCardless
rate-limits ``/details/`` per account per day (as low as 4).

The provider-account → app-account link lives on ``Account.external_ids`` (RFC
``connectors-as-data-sources``); there is no account map and no scheduled poll here.

Secret discipline matches ``PlaidConfigStore``: ``public()`` lists each bank by name and
public key only — never its requisition id — a blank ``secret_key`` on update keeps the saved
one, the operator reads it back only through ``services.connector_secrets``, a model never can.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the protocol
lives in ``services/aggregation/gocardless.py``.
"""

from __future__ import annotations

import hashlib
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
    "access_token": "",  # machine-minted, never revealed
    # Every linked bank, write-only: ``[{requisition_id, replaces}]`` — ``replaces`` is the
    # requisition a renewal takes over once it is linked. Set only through the bank methods.
    "requisitions": [],
    "access_expires_at": None,  # ISO timestamp
    # bank_key → {institution_id, institution_name, country, status, consent_expires_at,
    # linked_at, account_ids}. ``status`` is GoCardless's StatusEnum: CR, GC, UA, GA, SA, LN, EX…
    "banks": {},
    "accounts_meta": {},  # GoCardless account id → {name, currency, mask, account_type}
    "last_synced_at": None,
    "last_status": "never_run",  # never_run | ok | auth_error | rate_limited | connect_error | error
    "last_error": None,
    # A fetch that worked for some banks or accounts but not others: which, and why.
    "last_caution": None,
}

_BANK_FIELDS = (
    "institution_id",
    "institution_name",
    "country",
    "status",
    "consent_expires_at",
    "linked_at",
    "account_ids",
)


def bank_key(requisition_id: str) -> str:
    """The public handle for one linked bank — a hash of its requisition id, so the client can
    name a bank to renew or remove without ever being handed the requisition id itself."""
    return hashlib.sha256(f"gocardless-requisition:{requisition_id}".encode()).hexdigest()[:16]


class GoCardlessConfigStore(AggregatorConfigStore):
    KIND = "gocardless"
    LABEL = "GoCardless"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("secret_id",)
    SECRET_KEYS = ("secret_key", "access_token", "requisitions")
    READ_ONLY_KEYS = ("requisitions", "banks")
    STATE_KEYS = (
        "access_expires_at",
        "banks",
        "accounts_meta",
        "last_synced_at",
        "last_status",
        "last_error",
        "last_caution",
    )

    def _normalized(self, merged: dict) -> dict:
        for key in ("accounts_meta", "banks"):
            if not isinstance(merged.get(key), dict):
                merged[key] = {}
        if not isinstance(merged.get("requisitions"), list):
            merged["requisitions"] = []
        return merged

    # ── reads ────────────────────────────────────────────────────────────────

    def secret_id(self) -> str:
        return (self.get().get("secret_id") or "").strip()

    def secret_key(self) -> str:
        return (self.get().get("secret_key") or "").strip()

    def accounts_meta(self) -> dict:
        return dict(self.get().get("accounts_meta") or {})

    def banks(self, *, conn=None) -> list[dict]:
        """Every linked bank, in link order: its requisition (secret) joined with its state."""
        cfg = self.get(conn=conn)
        state = cfg.get("banks") or {}
        out: list[dict] = []
        for entry in cfg.get("requisitions") or []:
            rid = str((entry or {}).get("requisition_id") or "").strip()
            if not rid:
                continue
            key = bank_key(rid)
            info = state.get(key) or {}
            out.append(
                {
                    "key": key,
                    "requisition_id": rid,
                    "replaces": (entry.get("replaces") or None),
                    **{f: info.get(f) for f in _BANK_FIELDS},
                    "account_ids": list(info.get("account_ids") or []),
                }
            )
        return out

    def bank(self, key: str) -> Optional[dict]:
        return next((b for b in self.banks() if b["key"] == key), None)

    def has_credentials(self) -> bool:
        cfg = self.get()
        return bool((cfg.get("secret_id") or "").strip() and (cfg.get("secret_key") or "").strip())

    def has_link(self) -> bool:
        """At least one bank GoCardless last reported as linked."""
        return any(b["status"] == LINKED for b in self.banks())

    def is_configured(self) -> bool:
        """Credentials AND at least one bank. An unfinished or expired one still counts: the
        fetch then says why it can't read the bank (``auth_error``) instead of
        ``not_configured``."""
        return self.has_credentials() and bool(self.banks())

    # ── app-owned writes ─────────────────────────────────────────────────────

    def save_token(self, access_token: Optional[str], expires_at: Optional[str]) -> None:
        """Cache (or, with ``None``, forget) the bearer and its expiry in one write."""
        with self._unit(None) as c:
            self._ensure(c)
            self._apply(c, {"access_token": access_token, "access_expires_at": expires_at})

    def clear_token(self) -> None:
        """Forget the cached bearer so the next call mints a fresh one."""
        self.save_token(None, None)

    def add_bank(
        self,
        requisition_id: str,
        *,
        institution_id: str,
        institution_name: str,
        country: str,
        consent_expires_at: Optional[str],
        replaces: Optional[str] = None,
    ) -> str:
        """Append a bank whose consent has just started. Returns its ``bank_key``."""
        bank = {
            "key": bank_key(requisition_id),
            "requisition_id": requisition_id,
            "replaces": replaces,
            "institution_id": institution_id,
            "institution_name": institution_name,
            "country": country,
            "status": "CR",
            "consent_expires_at": consent_expires_at,
            "linked_at": None,
            "account_ids": [],
        }
        with self._unit(None) as c:
            self._write(c, [*self.banks(conn=c), bank])
        return bank["key"]

    def update_bank(self, key: str, **fields) -> None:
        """Record what GoCardless reported for one bank (status, account ids, linked_at…)."""
        with self._unit(None) as c:
            banks = self.banks(conn=c)
            for b in banks:
                if b["key"] == key:
                    b.update({k: v for k, v in fields.items() if k in (*_BANK_FIELDS, "replaces")})
            self._write(c, banks)

    def drop_bank(self, key: str) -> None:
        """Forget one bank and the cached details of accounts no other bank still lists."""
        with self._unit(None) as c:
            banks = self.banks(conn=c)
            gone = next((b for b in banks if b["key"] == key), None)
            if gone is None:
                return
            kept = [b for b in banks if b["key"] != key]
            for b in kept:
                if b["replaces"] == gone["requisition_id"]:
                    b["replaces"] = None
            still = {a for b in kept for a in b["account_ids"]}
            meta = self.get(conn=c).get("accounts_meta") or {}
            pruned = {a: m for a, m in meta.items() if a not in set(gone["account_ids"]) - still}
            self._write(c, kept)
            self._record({"accounts_meta": pruned}, conn=c)

    def _write(self, conn, banks: list[dict]) -> None:
        self._ensure(conn)
        self._apply(
            conn,
            {
                "requisitions": [
                    {"requisition_id": b["requisition_id"], "replaces": b["replaces"]}
                    for b in banks
                ]
                or None,
                "banks": {b["key"]: {f: b.get(f) for f in _BANK_FIELDS} for b in banks},
            },
        )

    def cache_account_meta(self, account_id: str, meta: dict) -> None:
        with self._unit(None) as c:
            current = self.get(conn=c).get("accounts_meta") or {}
            self._record({"accounts_meta": {**current, account_id: meta}}, conn=c)

    def record_status(self, status: str, *, error: Optional[str] = None) -> None:
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

    # ── public (non-secret) view for the API ─────────────────────────────────

    def public(self) -> dict:
        cfg = self.get()
        banks = self.banks()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "has_credentials": self.has_credentials(),
            "secret_id": cfg.get("secret_id") or "",
            "has_link": any(b["status"] == LINKED for b in banks),
            # Each linked bank by NAME and public key only — never its requisition id.
            "banks": [
                {
                    "key": b["key"],
                    "institution_id": b["institution_id"] or "",
                    "institution_name": b["institution_name"] or b["institution_id"] or "",
                    "country": b["country"] or "",
                    "status": b["status"] or "",
                    "consent_expires_at": b["consent_expires_at"],
                    "linked_at": b["linked_at"],
                    "accounts": len(b["account_ids"]),
                    "renewing": bool(b["replaces"]),
                }
                for b in banks
            ],
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
            "last_caution": cfg.get("last_caution"),
        }
