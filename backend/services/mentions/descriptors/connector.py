"""Descriptor for the ``connector`` mentionable type using the GENERIC provider.

Continues the generic-provider pattern established in new_types.py / equity_lots.py
(generic search + generic resolve via rows + id_field + project; no override).

DATA SOURCE — SECURITY (critical): ``rows`` reads through the SERVICE
``services/connectors.list_connectors()``, which SYNTHESIZES + SANITIZES the
``ConnectorView`` list (every secret surfaces only as a ``has_*`` boolean inside
``secrets_public`` — never a value).  It MUST NOT import any raw connector config
store (PlaidConfigStore, IbkrConfigStore, EmailConfigStore, …):
those are listed in ``core.mention_guard.MENTION_FORBIDDEN_STORES`` and
``_collect.register()`` fails closed on any descriptor callable that references one.
Only non-secret ``ConnectorView`` fields (id / name / kind / enabled / status) are projected —
no token/secret/credential/api_key field is even named (the ``_SECRET_KEY_RE`` denylist would refuse such a key anyway).

Order continues after equity_lots.py's 19–22 (income/lot/grant/vest), so connector
takes 23 — appended at the tail of the registry sort.
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.types import SearchQuery


def _connector_rows(query: SearchQuery) -> list[dict]:
    """Non-secret projection of the synthesized ConnectorView list.

    Reads ONLY the sanitizing service (``list_connectors``) — never a raw connector
    config store — and emits a flat dict of non-secret scalars per connector.
    """
    from backend.services.connectors import list_connectors

    return [
        {
            "id": v.id,
            "name": v.name,
            "kind": v.kind,
            "enabled": v.enabled,
            "status": v.status,
            "last_status": str((v.config or {}).get("last_status") or ""),
        }
        for v in list_connectors()
    ]


_ERROR_STATUSES = frozenset({"error", "connect_error", "auth_error", "failed"})


def _connector_sublabel(row: dict) -> str:
    """The Connectors page's own words for a connector's state — the server twin of
    ``statusOf`` in ``frontend/src/components/tabs/connectors/adapter.ts``, so a picker row
    says "Connected" where the card does, never the raw ``active`` / ``not_configured``. No
    trailing on/off: "Connected" already means on, "Not connected"/"Disconnected" off."""
    status = str(row.get("status") or "")
    last = str(row.get("last_status") or "")
    if last == "reconnect_needed":
        return "Sign-in needed"
    if status in _ERROR_STATUSES or last in _ERROR_STATUSES:
        return "Auth error" if "auth_error" in (status, last) else "Error"
    if row.get("enabled"):
        return "Connected"
    configured = str(row.get("id") or "").startswith("mcp:") or status != "not_configured"
    return "Disconnected" if configured else "Not connected"


register(
    EntityDescriptor(
        type="connector",
        label="Connector",
        icon="⇄",  # distinct monochrome glyph — not used by any other type
        order=23,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_connector_rows,
        id_field="id",
        label_field="name",
        search_fields=("name", "kind"),
        sublabel_fn=_connector_sublabel,
        fields={
            "name": FieldSpec("Name"),
            "kind": FieldSpec("Provider"),
            "enabled": FieldSpec("Enabled"),
            "status": FieldSpec("Status", "enum"),
        },
    )
)
