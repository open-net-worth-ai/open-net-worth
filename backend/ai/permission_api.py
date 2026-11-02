"""What the permission REST API does with the flag off — main's behaviour, moved out of the router.

``api/tool_permissions.py`` is a thin router: it validates the request and calls the object
``ai.permissions.permission_api()`` returns, which is this class or its ``permissions_v2`` twin
(``ai.permission_api_v2``). The logic below is what the router held inline, unchanged: a stored
decision per tool, the app-wide list of DECISIONS (plus the YOLO flag), Reset as "forget the
decision". It lives in ``ai`` rather than ``services`` because the list joins the live tool
registry, which ``services`` cannot import.

Errors are the domain's own (``core.errors``); the router maps them to HTTP.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from backend.core.errors import NotFoundError, ValidationError
from backend.models.mcp_client import MCP_CONNECTOR_PREFIX
from backend.services import tool_permissions as permissions_service

logger = logging.getLogger(__name__)


class PermissionsResponse(BaseModel):
    connector_id: str
    # What an ABSENT tool means. Shipped on every response so the client never hardcodes it.
    default_level: str = permissions_service.DEFAULT_LEVEL
    # Only tools with a STORED decision. Everything else is at ``default_level``.
    permissions: dict[str, str] = {}


class DecisionRow(BaseModel):
    """One stored decision, app-wide — the Settings › Permissions row."""

    tool: str
    # The tool's OWNER: a connector id, ``mcp:<server>``, or ``core`` for an unscoped tool.
    connector_id: str
    level: str
    updated_at: str = ""
    # False when the stored tool name no longer resolves in the registry — a rename retired
    # it, so the decision is already inert. Rendered as a "no longer available" row rather
    # than hidden: a permission the user set and can no longer see is the failure this
    # surface exists to fix. Always True for the reserved ``*`` yolo row and for any
    # MCP-owned tool, neither of which is a registry name.
    tool_exists: bool = True


class DecisionsResponse(BaseModel):
    permissions: List[DecisionRow]
    total: int
    # Whether the blanket "stop asking me" row is armed. Reported from the STORED row
    # rather than computed, so this surface can never disagree with the gate it describes
    # (E3: an env-var implementation once reported OFF while confirmations were skipped).
    yolo: bool = False


class PermissionApi:
    def connector(self, connector_id: str) -> dict:
        """Every stored level for one connector. Never 404s on an unknown connector: "no
        decisions recorded" and "no such connector" are the same empty map."""
        return self._connector_response(connector_id)

    def set_connector_tool(self, connector_id: str, tool_name: str, level: str) -> dict:
        permissions_service.set_tool_permission(connector_id, tool_name, level)
        return self._connector_response(connector_id)

    def set_connector_group(self, connector_id: str, tools: list[str], level: str) -> dict:
        permissions_service.set_group_permission(connector_id, tools, level)
        return self._connector_response(connector_id)

    def clear_connector(self, connector_id: str) -> dict:
        """Forget every stored decision for this connector — its tools return to
        ``default_level`` (UNSET, not a stored ``needs_approval``). Idempotent."""
        cleared = permissions_service.clear_connector_permissions(connector_id)
        if cleared:
            logger.info("connector permissions cleared via API: %s (%d)", connector_id, cleared)
        return self._connector_response(connector_id)

    def list_tools(self) -> dict:
        """Every stored decision in the app, newest first — including the ones made through a
        connector's own ladder, because after the merge there is only one place they live."""
        return self._decisions_response()

    def set_tool(
        self, tool: str, level: str, connector_id: Optional[str], session_id: Optional[str]
    ) -> dict:
        """Set one tool's level, then return the full list so the caller never needs a second
        round trip.

        The OWNER comes from ``connector_id`` when the caller supplied one and is RESOLVED from
        the registry otherwise. ``session_id`` names the CONVERSATION, not a scope. When it
        belongs to an Automation run an ``always_allow`` is routed to that Automation's own
        ``allowed`` list, so an Automation can never inherit a permission granted in an
        unrelated chat.
        """
        tool = (tool or "").strip()
        if not tool:
            raise ValidationError("tool: is required")
        tool, owner = self._resolve_owner(tool, connector_id)

        if session_id and level == "always_allow":
            from backend.services.automations.runs import grant_automation_tool

            if grant_automation_tool(session_id, tool, "always"):
                return self._decisions_response()

        permissions_service.set_tool_permission(owner, tool, level)
        return self._decisions_response()

    def reset_tool(self, tool: str, connector_id: Optional[str]) -> dict:
        """Forget one tool's decision — it returns to its DEFAULT. 404 when nothing was stored.

        Clearing a STALE row (a tool since renamed away) is deliberately allowed, which is why
        ``connector_id`` is accepted: a stale or MCP-owned row's owner cannot be re-derived from a
        registry that no longer knows the name, so the client hands back the one it was shown.
        """
        tool = (tool or "").strip()
        tool, owner = self._resolve_owner(tool, connector_id)
        if not permissions_service.clear_tool_permission(owner, tool):
            raise NotFoundError(f"No permission decision for tool: {tool}")
        return {"cleared": tool}

    @staticmethod
    def _connector_response(connector_id: str) -> dict:
        return PermissionsResponse(
            connector_id=connector_id,
            default_level=permissions_service.DEFAULT_LEVEL,
            permissions=dict(permissions_service.get_connector_permissions(connector_id)),
        ).model_dump()

    @staticmethod
    def _decisions() -> List[DecisionRow]:
        from backend.ai.tools.registry import registered_tool_names

        live = set(registered_tool_names())
        rows: List[DecisionRow] = []
        for row in permissions_service.list_decisions():
            tool = str(row.get("tool") or "")
            owner = str(row.get("connector_id") or "")
            if not tool or not owner:
                continue
            # A name only has to resolve when the REGISTRY is what owns it. An MCP tool is
            # advertised by its own server and a `*` yolo row is not a tool at all, so neither
            # can be judged against the registry — calling either one stale would be a lie the
            # user cannot act on.
            registry_owned = not owner.startswith(MCP_CONNECTOR_PREFIX)
            exists = (
                tool in live if registry_owned and tool != permissions_service.YOLO_TOOL else True
            )
            rows.append(
                DecisionRow(
                    tool=tool,
                    connector_id=owner,
                    level=str(row.get("level") or ""),
                    updated_at=str(row.get("updated_at") or ""),
                    tool_exists=exists,
                )
            )
        return rows

    @classmethod
    def _decisions_response(cls) -> dict:
        rows = cls._decisions()
        return DecisionsResponse(
            permissions=rows, total=len(rows), yolo=permissions_service.yolo_enabled()
        ).model_dump()

    @staticmethod
    def _resolve_owner(tool: str, supplied: Optional[str]) -> tuple[str, str]:
        """``(canonical_tool, owner)``. A supplied owner is taken verbatim; otherwise the tool
        must be a live registry name, whose ``connector=`` tag (or ``core``) is the owner.

        422s an unknown name only when nothing was supplied — with an explicit owner there is
        nothing to look up and no way to be wrong about it.
        """
        from backend.ai.tools._connector_gate import permission_owner
        from backend.ai.tools.registry import registered_tool_names

        owner = (supplied or "").strip()
        if tool == permissions_service.YOLO_TOOL:
            return tool, owner or permissions_service.CORE_CONNECTOR_ID
        if owner:
            return tool, owner
        if tool not in set(registered_tool_names()):
            raise ValidationError(f"tool: unknown tool: {tool}")
        return tool, permission_owner(tool)
