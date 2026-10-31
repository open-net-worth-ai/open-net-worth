"""What the permission REST API does under ``permissions_v2`` (ADR
``2026-10-11-permissions-are-one-value-per-tool``): the flag-on twin of ``ai.permission_api``.

Every tool has exactly one value, labelled Always allow / Ask / Block. The app-wide list is the
registry's tools (``_connector_gate.permission_catalog``) joined onto the table, plus the external
MCP tools the servers last reported, plus any stored row whose tool has since been renamed away: a
decision does NOT survive a tool rename (RFC §15.1 — it is keyed on the canonical name at the
time), so a stale row is shown as "no longer available" rather than hidden, and Reset deletes it.
The retired YOLO row is one of those, which is how the owner takes it back.

Nothing is seeded: a tool with no row reads its seed (``services.tool_permissions_v2``), and each
row carries that seed (``default_level``) so a client shows Reset exactly when the value differs
from it. ``web_search`` and ``fetch_page`` have no value of their own — they run the active search
provider's tool — so they are not listed.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from backend.core.errors import ConflictError, ValidationError
from backend.models.mcp_client import MCP_CONNECTOR_PREFIX, mcp_connector_id
from backend.services import tool_permissions_v2 as permissions_service

logger = logging.getLogger(__name__)


class PermissionsResponse(BaseModel):
    connector_id: str
    # What a tool NOT named in ``tool_defaults`` is seeded with.
    default_level: str
    # The seed each of the connector's tools starts at and Reset restores.
    tool_defaults: dict[str, str] = {}
    # Every tool of the connector the owner has set.
    permissions: dict[str, str] = {}


class ToolPermissionRow(BaseModel):
    """One tool and its value, app-wide — a Settings › Permissions row, an Automation picker entry."""

    tool: str
    # The tool's OWNER: a connector id, ``mcp:<server>``, or ``core`` for an unscoped tool.
    connector_id: str
    level: str
    # The seed: what the tool starts at and what Reset restores. ``""`` for a row whose tool no
    # longer exists, which has nothing to go back to (Reset deletes it).
    default_level: str = ""
    # The tool's own first sentence, for the picker and for search. Empty for an MCP tool.
    description: str = ""
    updated_at: str = ""
    # False when the stored tool name no longer resolves in the registry. Always True for an
    # MCP-owned tool, which the registry cannot speak for.
    tool_exists: bool = True


class ToolPermissionsResponse(BaseModel):
    permissions: List[ToolPermissionRow]
    total: int


class ResetResponse(BaseModel):
    reset: str
    # The seed value the tool is back at, or ``None`` when its row was deleted (tool retired).
    level: Optional[str] = None


def _first_sentence(text: str) -> str:
    """The tool description's first sentence, capped — enough to tell tools apart in a list."""
    line = " ".join((text or "").split())
    cut = line.find(". ")
    line = line[: cut + 1] if cut != -1 else line
    return line if len(line) <= 160 else line[:157].rstrip() + "…"


class PermissionApiV2:
    def connector(self, connector_id: str) -> dict:
        return self._connector_response(connector_id)

    def set_connector_tool(self, connector_id: str, tool_name: str, level: str) -> dict:
        permissions_service.set_tool_permission(connector_id, tool_name, level)
        return self._connector_response(connector_id)

    def set_connector_group(self, connector_id: str, tools: list[str], level: str) -> dict:
        permissions_service.set_group_permission(connector_id, tools, level)
        return self._connector_response(connector_id)

    def clear_connector(self, connector_id: str) -> dict:
        """Reset every tool of this connector to its seed value. Idempotent: resetting a
        connector that is already at its seeds returns the same map rather than 404ing."""
        reset = permissions_service.reset_connector_permissions(connector_id)
        if reset:
            logger.info("connector permissions reset via API: %s (%d)", connector_id, reset)
        return self._connector_response(connector_id)

    def list_tools(self) -> dict:
        """Every tool and its value, built-in first — including the ones set through a
        connector's own ladder, because there is only one table."""
        return self._list_response()

    def set_tool(
        self, tool: str, level: str, connector_id: Optional[str], session_id: Optional[str]
    ) -> dict:
        """Set one tool's value, then return the full list so the caller never needs a second
        round trip.

        ``session_id`` names the CONVERSATION, not a scope. When it belongs to an Automation run,
        ``always_allow`` goes to THAT Automation's own Always allowed list instead of the global
        table, so a tap on a run's card never changes the tool for the rest of the app. If that
        Automation (or the run) no longer exists the request is refused with a conflict and
        nothing is written: it is never widened into a global decision. A chat conversation (no
        Automation behind it) still writes the global row. A Block is never written from a card:
        it is set deliberately (Settings → Permissions, a connector's page, or the Automation's
        Tool permissions section).
        """
        tool = (tool or "").strip()
        if not tool:
            raise ValidationError("tool: is required")
        requested = tool
        tool, owner = self._resolve_owner(tool, connector_id)

        if session_id and level == "always_allow":
            from backend.services.automations import runs

            if runs.grant_automation_tool(session_id, requested, "always"):
                # The open Automation page shows this list; a stale copy would be saved back over
                # this card's change by the next edit there.
                from backend.core.data_events import publish

                publish(["automations"])
                return self._list_response()
            if runs.is_automation_session(session_id):
                # The card outlived its Automation (or its run). Falling through would turn
                # "allow for this Automation" into "allow everywhere" without anyone choosing it.
                raise ConflictError("This Automation no longer exists, so nothing was changed.")

        permissions_service.set_tool_permission(owner, tool, level)
        return self._list_response()

    def reset_tool(self, tool: str, connector_id: Optional[str]) -> dict:
        """Reset one tool to its seed value. A tool whose row outlived it (renamed away) has no
        seed to go back to, so Reset deletes that row — which is why ``connector_id`` is accepted
        and the listing carries it: a stale or MCP-owned row's owner cannot be re-derived from a
        registry that no longer knows the name."""
        tool = (tool or "").strip()
        tool, owner = self._resolve_owner(tool, connector_id)
        level = permissions_service.reset_tool_permission(owner, tool)
        return ResetResponse(reset=tool, level=level).model_dump()

    @staticmethod
    def _connector_response(connector_id: str) -> dict:
        from backend.services import connectors as connectors_service

        default_level, tool_defaults = connectors_service.default_levels(connector_id)
        return PermissionsResponse(
            connector_id=connector_id,
            default_level=default_level,
            tool_defaults=tool_defaults,
            permissions=dict(permissions_service.get_connector_permissions(connector_id)),
        ).model_dump()

    @staticmethod
    def _discovered_mcp_tools() -> list[tuple[str, str]]:
        """``(mcp:<id>, tool)`` for what the external MCP servers last reported — tools nobody
        has set yet, which have no row to be listed from."""
        from backend.db.mcp_client_store import McpClientStore

        found: list[tuple[str, str]] = []
        for server in McpClientStore().all():
            for tool in server.discovered_tools or []:
                name = tool.get("name") if isinstance(tool, dict) else None
                if name:
                    found.append((mcp_connector_id(server.id), name))
        return found

    @classmethod
    def _rows(cls) -> List[ToolPermissionRow]:
        from backend.ai.tools._connector_gate import permission_catalog
        from backend.ai.tools.registry import tool_description

        stored = {
            (str(r.get("connector_id") or ""), str(r.get("tool") or "")): r
            for r in permissions_service.list_decisions()
        }
        yolo, auto_run = permissions_service.stop_asking_switches()
        rows: List[ToolPermissionRow] = []
        listed: set[tuple[str, str]] = set()
        for owner, tool, _asks in permission_catalog():
            row = stored.get((owner, tool)) or {}
            level = row.get("level")
            rows.append(
                ToolPermissionRow(
                    tool=tool,
                    connector_id=owner,
                    level=level
                    if level in permissions_service.LEVELS
                    else permissions_service.unrowed_level(tool, yolo=yolo, auto_run=auto_run),
                    default_level=permissions_service.seed_level(tool),
                    description=_first_sentence(tool_description(tool) or ""),
                    updated_at=str(row.get("updated_at") or ""),
                )
            )
            listed.add((owner, tool))
        for key in cls._discovered_mcp_tools():
            if key in listed:
                continue
            row = stored.get(key) or {}
            rows.append(
                ToolPermissionRow(
                    tool=key[1],
                    connector_id=key[0],
                    level=str(row.get("level") or permissions_service.DEFAULT_LEVEL),
                    default_level=permissions_service.DEFAULT_LEVEL,
                    updated_at=str(row.get("updated_at") or ""),
                )
            )
            listed.add(key)
        for (owner, tool), row in stored.items():
            if not owner or not tool or (owner, tool) in listed:
                continue
            is_mcp = owner.startswith(MCP_CONNECTOR_PREFIX)
            rows.append(
                ToolPermissionRow(
                    tool=tool,
                    connector_id=owner,
                    level=str(row.get("level") or ""),
                    default_level=permissions_service.DEFAULT_LEVEL if is_mcp else "",
                    updated_at=str(row.get("updated_at") or ""),
                    tool_exists=is_mcp,
                )
            )
        core = permissions_service.CORE_CONNECTOR_ID
        return sorted(rows, key=lambda r: (r.connector_id != core, r.connector_id, r.tool))

    @classmethod
    def _list_response(cls) -> dict:
        rows = cls._rows()
        return ToolPermissionsResponse(permissions=rows, total=len(rows)).model_dump()

    @staticmethod
    def _resolve_owner(tool: str, supplied: Optional[str]) -> tuple[str, str]:
        """``(canonical_tool, owner)``. A supplied owner is taken verbatim; otherwise the tool
        must be a live registry name, whose ``connector=`` tag (or ``core``) is the owner.

        ``web_search`` and ``fetch_page`` belong to the ACTIVE search connector, so they resolve
        to that provider's own tool whatever owner was supplied — setting a level on either IS
        setting it on the search connector's page, never a ``core`` row of its own that could
        disagree.

        422s an unknown name only when nothing was supplied — with an explicit owner there is
        nothing to look up and no way to be wrong about it.
        """
        from backend.ai.tools._connector_gate import permission_key
        from backend.ai.tools.registry import registered_tool_names
        from backend.ai.tools.web_search_providers import active_provider_tool

        owner = (supplied or "").strip()
        if active_provider_tool(tool) is not None:
            owner, tool = permission_key(tool)
            return tool, owner
        if owner:
            return tool, owner
        if tool not in set(registered_tool_names()):
            raise ValidationError(f"tool: unknown tool: {tool}")
        owner, tool = permission_key(tool)
        return tool, owner
