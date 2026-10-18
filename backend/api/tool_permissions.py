"""
GET    /api/tool-permissions                          — every stored decision, app-wide
PUT    /api/tool-permissions/{tool}                   — set one tool's level (owner resolved here)
DELETE /api/tool-permissions/{tool}                   — forget one tool's decision

GET    /api/connectors/{id}/permissions               — one connector's stored levels + the default
PUT    /api/connectors/{id}/permissions/{tool}        — set one tool's level
PUT    /api/connectors/{id}/permissions               — stamp one level onto a group
DELETE /api/connectors/{id}/permissions               — forget every level for a connector

The REST surface over the ONE permission table — the Connectors detail page's ladder
(Connectors redesign §4) AND Settings › Permissions (approval model RFC 2026-07-20 §15.4),
which are two views of the same rows since the 2026-08-14 merge folded ``tool_grants`` in
here (ADR ``2026-08-14-one-permission-store-for-every-tool``). Thin per the layering rule
— validate, call ONE service, shape the response.

## Two shapes, because they are two different questions

The connector-scoped routes answer "what has the user decided about THIS connector", and
the client already holds that connector's tool list, so the response is a bare map. The
app-wide route answers "what has the user decided, anywhere" — the revocation surface —
so each row carries its own owner and the join onto the live registry that says whether
its tool still resolves. That join happens HERE rather than in ``services`` because
``services`` sits BELOW ``ai`` in the dependency rule and cannot import the registry,
while ``api -> ai`` is exactly the allowed direction (the same split ``api/news_sources``
uses to join poller health).

The join earns its keep: a decision does NOT survive a tool rename (RFC §15.1 — it is
keyed on the canonical name at the time), so after a rename the old key stops resolving
and the user is silently asked again. Rendering the stale row as "no longer available"
turns an invisible revocation into a visible one, which is the whole reason the tab exists.

``PUT /api/tool-permissions/{tool}`` resolves the OWNER server-side from the tool's
``connector=`` tag, so no client has to know (or guess) which bucket a tool belongs to —
guessing wrong would write a row nothing ever reads. The connector-scoped PUT keeps taking
an explicit id because an MCP server's tools are not in the registry at all.

## What this deliberately does NOT return

A group level. "Custom" is DERIVED, never stored (design handoff §10), so it is computed
where it is displayed: the client already has the connector's tool list and its read/write
grouping from ``GET /api/connectors/{id}``, and merging that with ``permissions`` +
``default_level`` from here is the whole computation. Returning a group aggregate from the
server would mint exactly the second, coarser record the design says must not exist —
one that could disagree with the tools underneath it.

``default_level`` ships on every response so no client hardcodes what an absent tool means.
A tool missing from ``permissions`` is at ``default_level``; that is the ONLY way to read
the map.

## Route order is load-bearing

``api/connectors_api.py`` registers ``GET/PATCH /api/connectors/{connector_id:path}``, and
``:path`` matches slashes — so ``/api/connectors/plaid/permissions`` would be swallowed by
it as ``connector_id="plaid/permissions"``. ``backend/main.py`` therefore includes THIS
router BEFORE that one (Starlette matches in registration order, first full match wins).
``tests/test_tool_permissions_routes.py::test_permissions_route_wins_over_the_connector_path_route``
pins it, so a reordering of the include block fails loudly instead of silently 404ing the
whole permission UI.
"""

from __future__ import annotations

import logging
from typing import Annotated, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.models.mcp_client import MCP_CONNECTOR_PREFIX
from backend.services import tool_permissions as permissions_service

logger = logging.getLogger(__name__)
router = APIRouter()

# Typed at the edge so a stale client's fourth level is a 422 here, never a silently
# stored value the gate would not recognise. "custom" is deliberately absent: it is a
# group DISPLAY state, not a level anything can be set to.
Level = Literal["always_allow", "needs_approval", "blocked"]

# Bounds on a group stamp. The list is whatever the page was displaying, so the real ceiling
# is a chatty MCP server's tool count — 500 is far past any of those while still making an
# unbounded body (which lands as one transaction of that many upserts) a 422 instead of a
# stall. The per-name cap is the same idea one level down: a tool name is an identifier.
MAX_GROUP_TOOLS = 500
MAX_TOOL_NAME_CHARS = 200

ToolName = Annotated[str, StringConstraints(max_length=MAX_TOOL_NAME_CHARS)]


class PermissionsResponse(BaseModel):
    connector_id: str
    # What an ABSENT tool means. Shipped on every response so the client never hardcodes it.
    default_level: str = permissions_service.DEFAULT_LEVEL
    # Only tools with a STORED decision. Everything else is at ``default_level``.
    permissions: Dict[str, str] = {}


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


class SetLevelRequest(BaseModel):
    level: Level
    # The tool's OWNER, when the CALLER already knows it (Settings › Permissions always
    # does — it is re-setting a row it is displaying). Omitted, the server resolves it from
    # the tool's ``connector=`` tag. Explicit wins, and it is the only correct answer for a
    # row the registry cannot speak for: an MCP server's tool is not a registry name at
    # all, and two servers may each advertise a ``search``.
    connector_id: Optional[str] = None
    # WHICH conversation this decision was made in — NOT a scope. When it belongs to an
    # Automation run the permission is routed to that Automation's own ``allowed`` list
    # instead of the global table, so an Automation cannot inherit a permission granted in
    # an unrelated chat.
    session_id: Optional[str] = None
    # The card this decision was tapped on. Only read for a ``fetch_page`` card in an
    # Automation run, where *Always* allows that card's website rather than the whole tool.
    tool_use_id: Optional[str] = None


class ToolLevelRequest(BaseModel):
    level: Level


class GroupLevelRequest(BaseModel):
    """A group stamp: one level onto every tool named.

    ``tools`` is sent by the CLIENT rather than derived server-side from the group name,
    deliberately — the group's membership is whatever the page is currently displaying
    (an MCP server's tool list is the server's to advertise and can change between two
    reads), so stamping exactly what the user saw is the honest behaviour. An empty list
    is a no-op, not an error: a connector with no write tools has an empty write group.

    Bounded — see ``MAX_GROUP_TOOLS``. An uncapped list is one request that becomes an
    arbitrarily long write transaction.
    """

    tools: List[ToolName] = Field(default_factory=list, max_length=MAX_GROUP_TOOLS)
    level: Level


def _response(connector_id: str) -> PermissionsResponse:
    return PermissionsResponse(
        connector_id=connector_id,
        default_level=permissions_service.DEFAULT_LEVEL,
        permissions=dict(permissions_service.get_connector_permissions(connector_id)),
    )


@router.get("/api/connectors/{connector_id}/permissions", response_model=PermissionsResponse)
def get_permissions(connector_id: str) -> PermissionsResponse:
    """Every stored level for one connector.

    Never 404s on an unknown connector: "no decisions recorded" and "no such connector"
    are the same empty map, and the ladder is legitimately read for a connector that is
    not persisted yet (the add-an-MCP-server flow permissions what step 2 discovered).
    """
    return _response(connector_id)


@router.put(
    "/api/connectors/{connector_id}/permissions/{tool_name}", response_model=PermissionsResponse
)
def set_tool_permission(
    connector_id: str, tool_name: str, req: ToolLevelRequest
) -> PermissionsResponse:
    """Set ONE tool's level, then return the connector's full map so the caller never
    needs a second round trip to re-render the ladder."""
    try:
        permissions_service.set_tool_permission(connector_id, tool_name, req.level)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return _response(connector_id)


@router.put("/api/connectors/{connector_id}/permissions", response_model=PermissionsResponse)
def set_group_permission(connector_id: str, req: GroupLevelRequest) -> PermissionsResponse:
    """Stamp one level onto every tool named — the group control.

    Deliberately LOSSY: a per-tool override inside the group is overwritten. That is the
    control's purpose (§4 — "make this whole group behave"), and it is how a Custom group
    is flattened back to uniform in one action.
    """
    try:
        permissions_service.set_group_permission(connector_id, req.tools, req.level)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return _response(connector_id)


@router.delete("/api/connectors/{connector_id}/permissions", response_model=PermissionsResponse)
def clear_permissions(connector_id: str) -> PermissionsResponse:
    """Forget every stored decision for this connector — its tools return to
    ``default_level`` (UNSET, not a stored ``needs_approval``).

    Idempotent: clearing a connector with nothing stored returns the same empty map rather
    than 404ing, so a double-tap and a removed-then-re-added connector behave identically.
    """
    cleared = permissions_service.clear_connector_permissions(connector_id)
    if cleared:
        logger.info("connector permissions cleared via API: %s (%d)", connector_id, cleared)
    return _response(connector_id)


# ── App-wide: the revocation surface (Settings › Permissions) ────────────────────────


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
        exists = tool in live if registry_owned and tool != permissions_service.YOLO_TOOL else True
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


def _decisions_response() -> DecisionsResponse:
    rows = _decisions()
    return DecisionsResponse(
        permissions=rows, total=len(rows), yolo=permissions_service.yolo_enabled()
    )


@router.get("/api/tool-permissions", response_model=DecisionsResponse)
def get_all_tool_permissions() -> DecisionsResponse:
    """Every stored decision in the app, newest first — including the ones made through a
    connector's own ladder, because after the merge there is only one place they live."""
    return _decisions_response()


@router.put("/api/tool-permissions/{tool:path}", response_model=DecisionsResponse)
def set_any_tool_permission(tool: str, req: SetLevelRequest) -> DecisionsResponse:
    """Set one tool's level, then return the full list so the caller never needs a second
    round trip.

    ``{tool:path}`` rather than ``{tool}`` so the reserved ``*`` yolo row is settable and
    revocable through this same route — arming and disarming the single most powerful
    permission here must not be a second code path in a design whose whole claim is that
    there is exactly one.

    The OWNER comes from ``connector_id`` when the caller supplied one and is RESOLVED from
    the registry otherwise (see ``SetLevelRequest.connector_id``). A resolved name is also
    required to BE a registered tool, so a decision is only ever recorded against a name
    the gate will actually look up; a caller-supplied owner is
    taken at face value, exactly as the connector-scoped routes take theirs, because the
    registry is not the authority on an MCP server's tool list.

    ``session_id`` names the CONVERSATION, not a scope. When it belongs to an Automation
    run an ``always_allow`` is routed to that Automation's own ``allowed`` list, so an
    Automation can never inherit a permission granted in an unrelated chat.
    """
    tool = (tool or "").strip()
    if not tool:
        raise HTTPException(status_code=422, detail="tool: is required")
    tool, owner = _resolve_owner(tool, req.connector_id)

    if req.session_id and req.level == "always_allow":
        from backend.services.automations.runs import grant_automation_tool

        if grant_automation_tool(req.session_id, tool, "always", tool_use_id=req.tool_use_id):
            return _decisions_response()

    try:
        permissions_service.set_tool_permission(owner, tool, req.level)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return _decisions_response()


@router.delete("/api/tool-permissions/{tool:path}")
def clear_any_tool_permission(tool: str, connector_id: Optional[str] = None) -> dict:
    """Forget one tool's decision — it returns to its DEFAULT. 404 when nothing was stored.

    Clearing a STALE row (a tool since renamed away) is deliberately allowed: the row is
    real even though the tool is not, and leaving the user unable to clear it would be the
    same "no revocation surface" complaint this surface exists to answer. That is exactly
    why ``connector_id`` is accepted as a query parameter and why the listing carries it —
    a stale or MCP-owned row's owner cannot be re-derived from a registry that no longer
    knows the name, so the client hands back the one it was shown.
    """
    tool = (tool or "").strip()
    tool, owner = _resolve_owner(tool, connector_id)
    if not permissions_service.clear_tool_permission(owner, tool):
        raise HTTPException(status_code=404, detail=f"No permission decision for tool: {tool}")
    return {"cleared": tool}


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
        raise HTTPException(status_code=422, detail=f"tool: unknown tool: {tool}")
    return tool, permission_owner(tool)
