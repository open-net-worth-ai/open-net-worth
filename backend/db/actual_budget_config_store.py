"""ActualBudgetConfigStore — config + state for the Actual Budget connector.

A facade over the ``actual_budget`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``).
All four of the server address, password, token and encryption password are secrets in
``connector_secrets``: an address can carry credentials (``https://user:pw@host``), and an
endpoint is one. ``connectors.config`` holds only the chosen budget's sync id; its name and
currency are ``connectors.state``. Nothing here reads the environment.

``public()`` reports that each secret is set and shows the server's host with any userinfo
stripped, never a value. Behind the gateway (a hosted instance) the connector is unavailable and
refuses every write: its client opens its own connection, which a hosted instance must not aim
at its own network.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from backend.core import config
from backend.core.errors import ConflictError, ValidationError
from backend.db.aggregator_config_store import AggregatorConfigStore

DEFAULTS: dict = {
    "enabled": False,
    "server_url": "",  # WRITE-ONLY secret — the address can carry credentials
    "password": "",  # WRITE-ONLY secret
    "token": "",  # WRITE-ONLY secret, for servers that do not take a password
    "encryption_password": "",  # WRITE-ONLY secret, for an end-to-end encrypted budget
    "budget_id": None,  # the budget's sync id
    "budget_name": None,
    "budget_currency": None,
    "last_synced_at": None,
    "last_status": "never_run",
    "last_error": None,
}

SECRET_NAMES = ("server_url", "password", "token", "encryption_password")


def _parts(url: str):
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — raises on a non-numeric or out-of-range port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts


def display_host(url: str) -> str | None:
    """``host[:port]`` of a server address, userinfo, path and query dropped."""
    parts = _parts(url)
    if parts is None:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{host}:{parts.port}" if parts.port else host


def plain_http_remote(url: str) -> bool:
    """Plain http to anything but this computer. Allowed, and said wherever it matters."""
    parts = _parts(url)
    if parts is None or parts.scheme != "http":
        return False
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


AUTH_ERROR = "auth_error"


class ActualBudgetConfigStore(AggregatorConfigStore):
    KIND = "actual_budget"
    LABEL = "Actual Budget"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("budget_id",)
    SECRET_KEYS = SECRET_NAMES
    STATE_KEYS = (
        "budget_name",
        "budget_currency",
        "last_synced_at",
        "last_status",
        "last_error",
    )

    def available(self) -> bool:
        return not config.gateway_active()

    def require_available(self) -> None:
        if not self.available():
            raise ConflictError("Actual isn't available on a hosted instance.")

    def _normalize(self, clean: dict) -> None:
        """Refuse any write behind the gateway, and an address that is not http(s) with a host.
        ``budget_id`` reads a blank as "clear the pick" (it is not a secret), which also drops
        the cached name and currency. A new password or token replaces the other: a saved
        password would otherwise win over a token typed to replace it."""
        self.require_available()
        if "token" in clean and "password" not in clean:
            clean["password"] = None
        elif "password" in clean and "token" not in clean:
            clean["token"] = None
        if "server_url" in clean and _parts(clean["server_url"]) is None:
            raise ValidationError(
                "The server address must start with http:// or https:// and name a host."
            )
        if "budget_id" in clean:
            new_id = str(clean["budget_id"]).strip() or None
            clean["budget_id"] = new_id
            if new_id is None and "budget_name" not in clean:
                clean["budget_name"] = None
                clean["budget_currency"] = None

    def note_sign_in(self, refused: bool) -> None:
        """What a read learned about the sign-in, as the status word alone: never the server's
        text, which can hold the address. A good read clears only this status."""
        if refused:
            self._record({"last_status": AUTH_ERROR})
        elif self.get().get("last_status") == AUTH_ERROR:
            self._record({"last_status": None})

    # ── reads ────────────────────────────────────────────────────────────────────

    def credentials(self) -> dict:
        """The four secrets, for server-side use only (never an API response or a model)."""
        cfg = self.get()
        return {name: (cfg.get(name) or "").strip() for name in SECRET_NAMES}

    def budget_id(self) -> str | None:
        return self.get().get("budget_id") or None

    def is_configured(self) -> bool:
        """An address and one way to sign in (a password or a token). The budget is picked at
        runtime, so it is not part of "set up"."""
        secret = self.credentials()
        return bool(secret["server_url"] and (secret["password"] or secret["token"]))

    def public(self) -> dict:
        cfg = self.get()
        secret = self.credentials()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "available": self.available(),
            "has_password": bool(secret["password"]),
            "has_token": bool(secret["token"]),
            "has_encryption_password": bool(secret["encryption_password"]),
            "has_server_url": bool(secret["server_url"]),
            "server_host": display_host(secret["server_url"]),
            "server_plain_http": plain_http_remote(secret["server_url"]),
            "budget_id": cfg.get("budget_id"),
            "budget_name": cfg.get("budget_name"),
            "budget_currency": cfg.get("budget_currency"),
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
