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

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional

from backend.db.aggregator_config_store import AggregatorConfigStore

DEFAULTS: dict = {
    "enabled": False,  # master switch
    # Application credentials (write-only secrets — never returned to the frontend).
    "application_id": "",  # the Enable Banking application id (used as the JWT `kid`)
    "private_key": "",  # the application's RSA private key (PEM) — WRITE-ONLY
    # The chosen bank + the consent handle returned by the link/exchange flow.
    "aspsp": "",  # the chosen bank id/name (Enable Banking calls a bank an "ASPSP")
    "country": "",  # the ISO 3166 alpha-2 country of the chosen bank
    # LEGACY single session (one bank). Read as the first bank until the next bank write
    # moves it into ``sessions``; never written otherwise.
    "session_id": "",
    # Every linked bank's session id, WRITE-ONLY: ``[{session_id}]``. A session id reads that
    # bank's accounts, so it is a secret; set only through the bank methods.
    "sessions": [],
    # bank_key → {aspsp, country, valid_until, linked_at, account_ids} — the public half.
    "banks": {},
    # Links started but not yet completed, newest last: ``[{state, aspsp, country,
    # started_at}]``. A bank's return must carry one of these ``state`` values back, and that
    # entry names the bank it linked. Several, so a second Link bank (another bank, or a retry)
    # never strands a consent still open in another tab. Not a credential.
    "pending_links": [],
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error
    "last_error": None,
}


# Pending links: how many are remembered, and for how long a bank's return can complete one.
_PENDING_MAX = 5
_PENDING_TTL = timedelta(hours=2)

_BANK_FIELDS = ("aspsp", "country", "valid_until", "linked_at", "account_ids")


def bank_key(session_id: str) -> str:
    """The public handle for one linked bank — a hash of its session id, so the client can
    name a bank to remove without ever being handed the session id itself."""
    return hashlib.sha256(f"enable-banking-session:{session_id}".encode()).hexdigest()[:16]


class EnableBankingConfigStore(AggregatorConfigStore):
    KIND = "enable_banking"
    LABEL = "Enable Banking"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("application_id", "aspsp", "country")
    SECRET_KEYS = ("private_key", "session_id", "sessions")
    STATE_KEYS = (
        "last_synced_at",
        "last_status",
        "last_error",
        "pending_links",
        "banks",
    )
    READ_ONLY_KEYS = ("sessions", "banks", "pending_links")

    def _normalized(self, merged: dict) -> dict:
        if not isinstance(merged.get("banks"), dict):
            merged["banks"] = {}
        for key in ("sessions", "pending_links"):
            if not isinstance(merged.get(key), list):
                merged[key] = []
        return merged

    # ── status helpers ───────────────────────────────────────────────────────────

    def application_id(self) -> str:
        return (self.get().get("application_id") or "").strip()

    def private_key(self) -> str:
        return self.get().get("private_key") or ""

    def banks(self, *, conn=None) -> list[dict]:
        """Every linked bank, in link order: its session (secret) joined with its state. A
        legacy single ``session_id`` reads as one bank, named by the saved aspsp/country."""
        cfg = self.get(conn=conn)
        state = cfg.get("banks") or {}
        entries = list(cfg.get("sessions") or [])
        legacy = (cfg.get("session_id") or "").strip()
        if not entries and legacy:
            entries = [{"session_id": legacy}]
        out: list[dict] = []
        for entry in entries:
            sid = str((entry or {}).get("session_id") or "").strip()
            if not sid:
                continue
            key = bank_key(sid)
            info = state.get(key) or {}
            if not info and sid == legacy:
                info = {"aspsp": cfg.get("aspsp"), "country": cfg.get("country")}
            out.append(
                {
                    "key": key,
                    "session_id": sid,
                    **{f: info.get(f) for f in _BANK_FIELDS},
                    "account_ids": list(info.get("account_ids") or []),
                }
            )
        return out

    def bank(self, key: str) -> Optional[dict]:
        return next((b for b in self.banks() if b["key"] == key), None)

    def has_credentials(self) -> bool:
        """Whether the application_id + private_key credential pair is set."""
        return bool(self.application_id()) and bool(self.private_key().strip())

    def has_session(self) -> bool:
        return bool(self.banks())

    def is_configured(self) -> bool:
        """Whether the connector is ready to FETCH — credentials AND at least one linked bank
        (each bank's session is the consent handle the accounts/transactions endpoints need)."""
        return self.has_credentials() and self.has_session()

    # ── app-owned bank writes ────────────────────────────────────────────────────

    def add_bank(
        self,
        session_id: str,
        *,
        aspsp: str,
        country: str,
        valid_until: Optional[str],
        linked_at: str,
        account_ids: list[str],
    ) -> tuple[str, list[str]]:
        """Add a bank whose consent just completed. Linking the SAME bank again (same name and
        country) is a renewal: it takes the old one's place. Returns ``(bank_key, the session
        ids it replaced)`` — the caller revokes those at Enable Banking."""
        bank = {
            "key": bank_key(session_id),
            "session_id": session_id,
            "aspsp": aspsp,
            "country": country,
            "valid_until": valid_until,
            "linked_at": linked_at,
            "account_ids": list(account_ids),
        }
        with self._unit(None) as c:
            current = self.banks(conn=c)
            same = [b for b in current if (b["aspsp"], b["country"]) == (aspsp, country)]
            kept = [b for b in current if b not in same and b["session_id"] != session_id]
            self._write(c, [*kept, bank])
        return bank["key"], [b["session_id"] for b in same if b["session_id"] != session_id]

    def drop_bank(self, key: str) -> Optional[str]:
        """Forget one bank. Returns its session id (to revoke), or None if it isn't listed."""
        with self._unit(None) as c:
            banks = self.banks(conn=c)
            gone = next((b for b in banks if b["key"] == key), None)
            if gone is None:
                return None
            self._write(c, [b for b in banks if b["key"] != key])
        return gone["session_id"]

    def add_pending_link(self, state: str, *, aspsp: str, country: str, now: datetime) -> None:
        """Remember a link just started. Keeps the newest few, none older than the window."""
        entry = {"state": state, "aspsp": aspsp, "country": country, "started_at": now.isoformat()}
        with self._unit(None) as c:
            kept = self._live_pending(self.get(conn=c).get("pending_links") or [], now)
            self._record({"pending_links": [*kept, entry][-_PENDING_MAX:]}, conn=c)

    def pending_link(self, state: str, *, now: datetime) -> Optional[dict]:
        """The pending link a bank's return belongs to — matched in constant time — or None."""
        found = None
        for p in self._live_pending(self.get().get("pending_links") or [], now):
            if hmac.compare_digest(str(p.get("state") or ""), state):
                found = p
        return found

    def latest_pending_link(self, *, now: datetime) -> Optional[dict]:
        """The newest live pending link — what a hand-pasted code (no state) completes."""
        live = self._live_pending(self.get().get("pending_links") or [], now)
        return live[-1] if live else None

    def drop_pending_link(self, state: str) -> None:
        with self._unit(None) as c:
            links = self.get(conn=c).get("pending_links") or []
            self._record(
                {"pending_links": [p for p in links if p.get("state") != state] or None}, conn=c
            )

    @staticmethod
    def _live_pending(links: list, now: datetime) -> list[dict]:
        out = []
        for p in links:
            try:
                started = datetime.fromisoformat(str(p.get("started_at")))
            except (TypeError, ValueError, AttributeError):
                continue
            if now - started <= _PENDING_TTL:
                out.append(p)
        return out

    def _write(self, conn, banks: list[dict]) -> None:
        """Persist the bank list; the legacy single session is folded in and cleared."""
        self._ensure(conn)
        self._apply(
            conn,
            {
                "sessions": [{"session_id": b["session_id"]} for b in banks] or None,
                "session_id": None,
                "banks": {b["key"]: {f: b.get(f) for f in _BANK_FIELDS} for b in banks},
            },
        )

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
            # Each linked bank by NAME and public key only — never its session id.
            "banks": [
                {
                    "key": b["key"],
                    "aspsp": b["aspsp"] or "",
                    "country": b["country"] or "",
                    "valid_until": b["valid_until"],
                    "linked_at": b["linked_at"],
                    "accounts": len(b["account_ids"]),
                }
                for b in self.banks()
            ],
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
