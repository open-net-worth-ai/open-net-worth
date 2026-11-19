"""AggregatorConfigStore — the shared facade behind the nine aggregator settings stores.

``PlaidConfigStore`` … ``IbkrConfigStore`` each keep their public surface (``get`` / ``update`` /
``public`` / ``reset`` / ``record_status`` and the provider's own helpers) but hold no storage of
their own: a provider is ONE ``ConnectorStore`` row whose id is the provider key (RFC
``connectors-table``). Where a field lives is declared per provider, exactly as the migration
placed it:

  * ``enabled``      -> ``connectors.enabled``
  * ``CONFIG_KEYS``  -> ``connectors.config``  (what the user typed — never a secret)
  * ``STATE_KEYS``   -> ``connectors.state``   (what the app wrote: last sync, caches, cash)
  * ``SECRET_KEYS``  -> ``connector_secrets``  (user credentials AND machine-minted tokens)

``get()`` stays the merged dict every internal caller reads: ``DEFAULTS`` under config, state,
secrets and ``enabled``. A provider that was never saved has no row and reads as ``DEFAULTS``;
the row is created disabled on the first write. ``public()`` never carries a secret value.

Every write is ONE ``serialized_unit_of_work`` over both tables, so a field split across them
can never be half-saved and a read-modify-write cannot interleave with another worker's. A
caller already in a unit of work (opened with ``serialized_unit_of_work()``) passes its own
``conn=`` and the write composes into it.

Connectors never read environment variables — there is no env seed.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import Connection

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.db.engine import get_engine


class AggregatorConfigStore:
    # The connector id == kind == the provider key, and its display name (the catalog's label;
    # ``db`` cannot import ``services``, ``tests/test_aggregator_config_facade.py`` pins them equal).
    KIND: str = ""
    LABEL: str = ""
    DEFAULTS: dict = {}
    CONFIG_KEYS: tuple[str, ...] = ()
    SECRET_KEYS: tuple[str, ...] = ()
    STATE_KEYS: tuple[str, ...] = ()
    # Fields ``update()`` refuses (set only through a dedicated method, e.g. OAuth tokens).
    READ_ONLY_KEYS: tuple[str, ...] = ()

    def __init__(self) -> None:
        self._connectors = ConnectorStore()

    # ── reads ─────────────────────────────────────────────────────────────────

    def get(self, *, conn: Optional[Connection] = None) -> dict:
        """The merged config: defaults under config, state, secrets and ``enabled``. Secrets
        are included — db/service callers only; ``public()`` is the client-safe view."""
        if conn is None:
            with get_engine().connect() as c:
                return self._merged(c)
        return self._merged(conn)

    def _merged(self, conn: Connection) -> dict:
        merged = copy.deepcopy(self.DEFAULTS)
        row = self._connectors.get(self.KIND, conn=conn)
        if row is not None:
            merged.update(row["config"])
            merged.update(row["state"])
            merged.update(self._connectors.secrets(self.KIND, conn=conn))
            merged["enabled"] = row["enabled"]
        return self._normalized(merged)

    def _normalized(self, merged: dict) -> dict:
        """Provider-specific repair of a merged dict (an unknown environment, a non-dict map)."""
        return merged

    # ── writes ────────────────────────────────────────────────────────────────

    def update(self, changes: dict, *, conn: Optional[Connection] = None) -> dict:
        """Patch the config. ``None`` change values are dropped ("omitted, leave unchanged"),
        unknown keys are ignored, and a blank secret keeps the saved one. Returns the merged
        config (secrets included — db/service callers only)."""
        clean = self._clean(changes)
        with self._unit(conn) as c:
            self._ensure(c)
            self._apply(c, clean)
            return self.get(conn=c)

    def reset(self) -> None:
        """Remove the connector — the Remove path (``services.connectors.remove_connector``).
        The row and its secrets are deleted, so a removed connector's credentials are erased,
        not hidden behind an omitted field."""
        self._connectors.delete(self.KIND)

    def record_status(self, status: str, *, error: Optional[str] = None) -> None:
        """The one write a fetch call makes: status + "Last fetched" + error. A run that
        succeeded clears the error of the run before it."""
        self._record(
            {
                "last_synced_at": self._now(),
                "last_status": status,
                "last_error": error,
            }
        )

    def clear_reach_failure(self) -> None:
        """Drop a stored "couldn't reach" / unknown failure once something else reached the
        provider with the same credentials (a budget app's sync run). A credentials failure
        stays for the user to fix, and "Last fetched" doesn't move: no fetch ran."""
        with self._unit(None) as c:
            if self.get(conn=c).get("last_status") in ("connect_error", "error"):
                self._record({"last_status": "ok", "last_error": None}, conn=c)

    def is_configured(self) -> bool:
        raise NotImplementedError

    def is_active(self) -> bool:
        """Enabled AND configured — the dormant gate the provider's fetch tool checks."""
        return bool(self.get().get("enabled")) and self.is_configured()

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @contextmanager
    def _unit(self, conn: Optional[Connection]) -> Iterator[Connection]:
        if conn is not None:
            yield conn
            return
        with serialized_unit_of_work() as c:
            yield c

    def _ensure(self, conn: Connection) -> None:
        self._connectors.ensure(self.KIND, self.KIND, self.LABEL, conn=conn)

    def _record(self, state: dict, *, conn: Optional[Connection] = None) -> None:
        """Write app-owned state. ``None`` here is a real value — it removes the key (a run
        always knows its own authoritative outcome), unlike ``update()``'s None-means-unchanged."""
        with self._unit(conn) as c:
            self._ensure(c)
            self._connectors.merge_state(self.KIND, state, conn=c)

    def _clean(self, changes: dict) -> dict:
        writable = set(self.DEFAULTS) - set(self.READ_ONLY_KEYS)
        clean = {k: v for k, v in changes.items() if v is not None and k in writable}
        if "enabled" in clean:
            clean["enabled"] = bool(clean["enabled"])
        for key in self.SECRET_KEYS:
            if key in clean:
                value = str(clean[key] or "").strip()
                if value:
                    clean[key] = value
                else:
                    clean.pop(key)
        self._normalize(clean)
        return clean

    def _normalize(self, clean: dict) -> None:
        """Provider-specific validation of an update, in place."""

    def _apply(self, conn: Connection, fields: dict) -> None:
        """Route already-validated fields to where each lives. ``None`` removes a config or
        state key and clears a secret; a blank string keeps a saved secret."""
        config = {k: v for k, v in fields.items() if k in self.CONFIG_KEYS}
        state = {k: v for k, v in fields.items() if k in self.STATE_KEYS}
        secrets = {k: v for k, v in fields.items() if k in self.SECRET_KEYS}
        if config:
            self._connectors.patch_config(self.KIND, config, conn=conn)
        if state:
            self._connectors.merge_state(self.KIND, state, conn=conn)
        if secrets:
            self._connectors.set_secrets(self.KIND, secrets, conn=conn)
        if "enabled" in fields:
            self._connectors.set_enabled(self.KIND, fields["enabled"], conn=conn)
