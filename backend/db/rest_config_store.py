"""RestConfigStore — the generic REST connector's APIs, one ``connectors`` row each.

An API the owner adds is the connector ``rest:<slug>`` (kind ``rest``). Everything that
describes it lives in ``connector_secrets``: the base URL (a credential — it can carry a key
in its path), the auth, fixed headers, and also the endpoint list and notes, which are text
the owner pasted and may hold one too. ``connectors.config`` and ``state`` stay empty, which
is what keeps that table safe in a sanitised backup.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — validation,
the operator/model projections and the HTTP call live in ``services/rest_api/``.
"""

from __future__ import annotations

from typing import Optional

from backend.core import rest_tools
from backend.db.connector_store import ConnectorStore, serialized_unit_of_work

SECRET_KEYS = ("base_url", "auth", "headers", "endpoints", "notes")


class RestConfigStore:
    def __init__(self) -> None:
        self._connectors = ConnectorStore()

    def rows(self) -> list[dict]:
        """``[{slug, name, enabled}]`` for every REST connector, oldest first. No secrets:
        this is the lean read behind tool registration."""
        out = []
        for row in self._connectors.list(rest_tools.KIND):
            slug = rest_tools.slug_of(row["id"])
            if slug:
                out.append({"slug": slug, "name": row["name"], "enabled": bool(row["enabled"])})
        return out

    def all(self) -> list[dict]:
        """Every API as ``get`` returns one, oldest first, in two reads."""
        rows = self._connectors.list(rest_tools.KIND)
        if not rows:
            return []
        secrets = self._connectors.secrets_by_kind(rest_tools.KIND)
        out = []
        for row in rows:
            slug = rest_tools.slug_of(row["id"])
            if slug:
                held = secrets.get(row["id"]) or {}
                out.append(
                    {
                        "slug": slug,
                        "name": row["name"],
                        "enabled": bool(row["enabled"]),
                        **{k: held[k] for k in SECRET_KEYS if k in held},
                    }
                )
        return out

    def get(self, slug: str) -> Optional[dict]:
        """One API as stored — ``{slug, name, enabled, **secrets}`` (credentials included;
        db/service callers only) — or ``None``."""
        connector = rest_tools.connector_id(slug)
        row = self._connectors.get(connector)
        if row is None or row["kind"] != rest_tools.KIND:
            return None
        secrets = self._connectors.secrets(connector)
        return {
            "slug": slug,
            "name": row["name"],
            "enabled": bool(row["enabled"]),
            **{k: secrets[k] for k in SECRET_KEYS if k in secrets},
        }

    def create(self, slug: str, name: str, fields: dict) -> None:
        """Add an API, switched on. ``ConflictError`` when the id is taken."""
        connector = rest_tools.connector_id(slug)
        with serialized_unit_of_work() as conn:
            self._connectors.create(connector, rest_tools.KIND, name, enabled=True, conn=conn)
            self._connectors.replace_secrets(connector, self._secrets(fields), conn=conn)

    def update(self, slug: str, name: str, fields: dict) -> None:
        """Replace an API's name and everything it holds, in one unit. Its on/off switch and
        its id are untouched."""
        connector = rest_tools.connector_id(slug)
        with serialized_unit_of_work() as conn:
            self._connectors.set_name(connector, name, conn=conn)
            self._connectors.replace_secrets(connector, self._secrets(fields), conn=conn)

    @staticmethod
    def _secrets(fields: dict) -> dict:
        return {k: fields[k] for k in SECRET_KEYS if k in fields}
