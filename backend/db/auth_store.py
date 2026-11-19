"""AuthStore — instance auth secrets (RFC P3-AUTH §3).

A single dict (NOT a list) holding the in-app-changeable auth state:
``password_hash``, ``session_signing_key``, ``api_token``, ``session_ttl``,
``trust_loopback``, and ``auth_disabled``.

Seeding/materialisation order (first access): for each secret use the stored
value if present, else seed from the corresponding env, else generate.
Concretely:

- ``password_hash``      — ``AUTH_PASSWORD_HASH`` as-is (preferred) > hashed
  ``AUTH_PASSWORD`` plaintext > ``""`` (no password configured yet).
- ``session_signing_key`` — ``AUTH_SESSION_KEY`` env > generated (ALWAYS present).
- ``api_token``          — ``AUTH_API_TOKEN`` env > generated.
- ``session_ttl``        — ``AUTH_SESSION_TTL`` env > 2592000 (30 days).
- ``trust_loopback``     — ``AUTH_TRUST_LOOPBACK`` env > ``true``.
- ``auth_disabled``      — ``AUTH_DISABLED`` env > ``false``.

Materialised secrets are persisted back immediately so the store is
authoritative afterwards (the in-app change-password works without ``.env``
edits, mirroring ``ModelConfigStore``/``EmailConfigStore``).

WAVE 5D2a (JSON->DB): replaces the hand-rolled ``auth.json`` file I/O with a row
in the ``secret_config`` table, namespace ``"auth"`` — checkpoint-EXCLUDED
(``ai.checkpoint._policy._EXCLUDED_TABLES``, via the whole ``secret_config``
table, which already carried that exclusion since Wave 5D1), SECRET and
NON-RESTORABLE (``core.sensitive`` — likewise via ``secret_config``'s existing
``SECRET_TABLES``/``NON_RESTORABLE_TABLES`` membership; no new entry needed).
THE highest-stakes row this migration has moved: the password hash, the
session-signing HMAC key, and the API bearer token, read on every
authenticated request — a single tiny row, well within per-request budget.

Diff is MINIMAL, mirroring every Wave 5D1 connector store: only ``__init__``/
``_read_raw``/``_write`` change; every materialisation/derived-view/mutator
method is byte-identical to before. The corrupt-bytes quarantine ceremony
(``_raise_corrupt``) is DROPPED — a DB read either succeeds or the database
itself is broken, which ``db/migrate.ensure_migrated()``'s fail-closed boot
gate owns instead (no separate "corrupt JSON" failure mode to carry forward).
Chmod 600 is likewise dropped — ``onw.db`` itself is ``-rw-------``.
"""

from __future__ import annotations

from typing import Optional

from backend.core.config import get_settings
from backend.core.security import generate_token, hash_password
from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import secret_config


def _revoke_connected_apps(reason: str) -> None:
    """Every app connected through the MCP OAuth server was approved with the OLD password, so a
    change, reset or removal revokes them all — first, so a failure here fails the password write
    rather than leaving a grant the owner meant to cut off (ADR
    2026-11-16-hosted-ai-apps-connect-to-mcp-through-the-apps-own-oauth-server)."""
    from datetime import datetime, timezone

    from backend.db.mcp_oauth_store import McpOAuthStore

    McpOAuthStore().revoke_all_if_present(reason, datetime.now(timezone.utc).isoformat())


class AuthStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("auth", table=secret_config, engine=engine)

    # ── read / materialise ───────────────────────────────────────────────────

    def _read_raw(self) -> dict:
        return super().get()

    def get(self) -> dict:
        """Return the auth state, materialising + persisting any missing secrets.

        ``password_hash`` may stay ``""`` (no password configured yet — the
        first-run dormant state); the signing key and api token are always
        materialised so cookie signing + Bearer auth have a key to work with.
        """
        data = self._read_raw()
        settings = get_settings()
        dirty = False

        # Password hash: store value > pre-hashed env > hashed plaintext env > "".
        pw_hash = data.get("password_hash")
        if not isinstance(pw_hash, str) or not pw_hash:
            if settings.auth_password_hash:
                pw_hash = settings.auth_password_hash
                dirty = True
            elif settings.auth_password:
                pw_hash = hash_password(settings.auth_password)
                dirty = True
            else:
                pw_hash = ""

        # Session signing key: store > env > generated (always present).
        signing_key = data.get("session_signing_key")
        if not isinstance(signing_key, str) or not signing_key:
            signing_key = settings.auth_session_key or generate_token()
            dirty = True

        # API token: store > env > generated.
        api_token = data.get("api_token")
        if not isinstance(api_token, str) or not api_token:
            api_token = settings.auth_api_token or generate_token()
            dirty = True

        # Session TTL: store > env > default (30 days).
        session_ttl = data.get("session_ttl")
        if not isinstance(session_ttl, int) or session_ttl <= 0:
            session_ttl = settings.auth_session_ttl
            dirty = True

        # Trust loopback: store > env > True.
        trust_loopback = data.get("trust_loopback")
        if not isinstance(trust_loopback, bool):
            trust_loopback = settings.auth_trust_loopback
            dirty = True

        # Auth disabled: store > env > False.
        auth_disabled = data.get("auth_disabled")
        if not isinstance(auth_disabled, bool):
            auth_disabled = settings.auth_disabled
            dirty = True

        materialised = {
            "password_hash": pw_hash,
            "session_signing_key": signing_key,
            "api_token": api_token,
            "session_ttl": session_ttl,
            "trust_loopback": trust_loopback,
            "auth_disabled": auth_disabled,
        }
        if dirty:
            self._write(materialised)
        return materialised

    # ── write ──────────────────────────────────────────────────────────────────

    def _write(self, data: dict) -> None:
        self.save(data)

    def set_password_hash(self, password_hash: str) -> dict:
        """Persist a new password hash, preserving the signing key + api token."""
        _revoke_connected_apps("password_changed")
        data = self.get()
        data["password_hash"] = password_hash
        self._write(data)
        return data

    def replace_password(self, password_hash: str) -> None:
        """Set a new password AND rotate the session signing key in one serialized write.

        Rotating the key is what signs every other browser out, so the two must land
        together — and serialized at the database, because the ``onw reset-password`` CLI
        writes this row from a process that shares no in-process lock with a running server.
        """
        self.get()  # materialise any missing secret first, so the row below is complete
        _revoke_connected_apps("password_reset" if password_hash else "password_removed")
        with self._use_serialized_namespace_conn(None) as conn:
            data = super().get(conn=conn)
            data["password_hash"] = password_hash
            data["session_signing_key"] = generate_token()
            self.save(data, conn=conn)

    def remove_password(self) -> None:
        """Clear the password (the instance is "not configured" again) and sign everyone out."""
        self.replace_password("")

    def rotate_api_token(self) -> str:
        """Generate + persist a fresh API token; return it."""
        data = self.get()
        data["api_token"] = generate_token()
        self._write(data)
        return data["api_token"]

    # ── derived views ────────────────────────────────────────────────────────

    def password_hash(self) -> str:
        return self.get()["password_hash"]

    def session_key_bytes(self) -> bytes:
        return self.get()["session_signing_key"].encode("utf-8")

    def api_token(self) -> str:
        return self.get()["api_token"]

    def session_ttl(self) -> int:
        return self.get()["session_ttl"]

    def trust_loopback(self) -> bool:
        return self.get()["trust_loopback"]

    def auth_disabled(self) -> bool:
        return self.get()["auth_disabled"]

    def update_auth_settings(
        self,
        *,
        session_ttl: int | None = None,
        trust_loopback: bool | None = None,
        auth_disabled: bool | None = None,
    ) -> dict:
        data = self.get()
        if session_ttl is not None:
            data["session_ttl"] = session_ttl
        if trust_loopback is not None:
            data["trust_loopback"] = trust_loopback
        if auth_disabled is not None:
            data["auth_disabled"] = auth_disabled
        self._write(data)
        return {
            "session_ttl": data["session_ttl"],
            "trust_loopback": data["trust_loopback"],
            "auth_disabled": data["auth_disabled"],
        }

    def is_password_set(self) -> bool:
        """Whether a password has been configured (the in-app login is usable)."""
        return bool(self.password_hash())

    def is_configured(self) -> bool:
        """Whether the instance has ANY credential that gates remote traffic.

        A password OR a (non-generated) configured API token counts. The
        always-generated first-run token does NOT make an instance "configured" —
        that token is the lockout-safety escape, logged once at boot.
        """
        settings = get_settings()
        return bool(self.password_hash()) or bool(settings.auth_api_token)
