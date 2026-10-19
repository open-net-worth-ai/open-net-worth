"""
GET    /api/tool-permissions                          — every tool and its value, app-wide
PUT    /api/tool-permissions/{tool}                   — set one tool's value (owner resolved here)
DELETE /api/tool-permissions/{tool}                   — Reset one tool to its seed value

GET    /api/connectors/{id}/permissions               — one connector's levels
PUT    /api/connectors/{id}/permissions/{tool}        — set one tool's value
PUT    /api/connectors/{id}/permissions               — stamp one value onto a group
DELETE /api/connectors/{id}/permissions               — Reset every tool of a connector to its seed

The REST surface over the ONE permission table — the Connectors detail page's ladder
(Connectors redesign §4), Settings › Permissions and the Automation page's tool picker, which are
three views of the same rows (ADR ``2026-08-14-one-permission-store-for-every-tool``). Every tool
has exactly one value, labelled Always allow / Ask / Block (ADR
``2026-10-11-permissions-are-one-value-per-tool``). Thin per the layering rule — validate, call
ONE service, shape the response.

## Two shapes, because they are two different questions

The connector-scoped routes answer "what are THIS connector's tools set to", and the client
already holds that connector's tool list, so the response is a bare map. The app-wide route
answers "what is every tool set to" — the Settings list and the Automation picker — so each row
carries its own owner, the seed value (``default_level``, what Reset restores), a one-line
description, and the join onto the live registry that says whether its tool still resolves. That
join happens HERE rather than in ``services`` because ``services`` sits BELOW ``ai`` in the
dependency rule and cannot import the registry, while ``api -> ai`` is exactly the allowed
direction (the same split ``api/news_sources`` uses to join poller health).

The list is the registry's tools (``core.tool_registry_seam.tool_catalog``) joined onto the
table, plus the external MCP tools that have rows, plus any row whose tool has since been
renamed away: a decision does NOT survive a tool rename (RFC §15.1 — it is keyed on the canonical
name at the time), so a stale row is shown as "no longer available" rather than hidden, and Reset
deletes it. ``web_search`` and ``fetch_page`` have no row of their own — they run the active
search provider's tool — so they are not listed.

``PUT /api/tool-permissions/{tool}`` resolves the OWNER server-side from the tool's
``connector=`` tag, so no client has to know (or guess) which bucket a tool belongs to —
guessing wrong would write a row nothing ever reads. The connector-scoped PUT keeps taking
an explicit id because an MCP server's tools are not in the registry at all.

## What this deliberately does NOT return

A group level. "Custom" is DERIVED, never stored (design handoff §10), so it is computed
where it is displayed: the client already has the connector's tool list and its read/write
grouping from ``GET /api/connectors/{id}``, whose tools each carry their value. Returning a
group aggregate from the server would mint exactly the second, coarser record the design says
must not exist — one that could disagree with the tools underneath it.

``default_level`` ships on every connector response so no client hardcodes the seed, and
``tool_defaults`` names it per tool: a native connector's sends read ``needs_approval`` while its
reads read ``always_allow``; an external MCP tool is always seeded ``needs_approval``.

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
    # What a tool NOT named in ``tool_defaults`` is seeded with.
    default_level: str
    # The seed each of the connector's tools starts at and Reset restores.
    tool_defaults: Dict[str, str] = {}
    # Every tool of the connector that has a row — in practice all of them once seeded.
    permissions: Dict[str, str] = {}


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
    # False when the stored tool name no longer resolves in the registry — a rename retired
    # it, so the decision is already inert. Rendered as a "no longer available" row rather
    # than hidden: a permission the user set and can no longer see is the failure this
    # surface exists to fix. Always True for an MCP-owned tool, which the registry cannot
    # speak for.
    tool_exists: bool = True


class ToolPermissionsResponse(BaseModel):
    permissions: List[ToolPermissionRow]
    total: int


class ResetResponse(BaseModel):
    reset: str
    # The seed value the tool is back at, or ``None`` when its row was deleted (tool retired).
    level: Optional[str] = None


class SetLevelRequest(BaseModel):
    level: Level
    # The tool's OWNER, when the CALLER already knows it (Settings › Permissions always
    # does — it is re-setting a row it is displaying). Omitted, the server resolves it from
    # the tool's ``connector=`` tag. Explicit wins, and it is the only correct answer for a
    # row the registry cannot speak for: an MCP server's tool is not a registry name at
    # all, and two servers may each advertise a ``search``.
    connector_id: Optional[str] = None
    # WHICH conversation this decision was made in — NOT a scope. When it belongs to an
    # Automation run, Always allow / Block go to THAT Automation's own Always allowed / Blocked
    # list instead of the global table, so a tap on a run's card never changes the tool for the
    # rest of the app.
    session_id: Optional[str] = None


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
    from backend.services import connectors as connectors_service

    default_level, tool_defaults = connectors_service.default_levels(connector_id)
    return PermissionsResponse(
        connector_id=connector_id,
        default_level=default_level,
        tool_defaults=tool_defaults,
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
def reset_permissions(connector_id: str) -> PermissionsResponse:
    """Reset every tool of this connector to its seed value.

    Idempotent: resetting a connector that is already at its seeds returns the same map rather
    than 404ing, so a double-tap and a removed-then-re-added connector behave identically.
    """
    reset = permissions_service.reset_connector_permissions(connector_id)
    if reset:
        logger.info("connector permissions reset via API: %s (%d)", connector_id, reset)
    return _response(connector_id)


# ── App-wide: every tool and its value (Settings › Permissions, the Automation picker) ──────


def _first_sentence(text: str) -> str:
    """The tool description's first sentence, capped — enough to tell tools apart in a list."""
    line = " ".join((text or "").split())
    cut = line.find(". ")
    line = line[: cut + 1] if cut != -1 else line
    return line if len(line) <= 160 else line[:157].rstrip() + "…"


def _rows() -> List[ToolPermissionRow]:
    from backend.ai.tools.registry import tool_description
    from backend.core.tool_registry_seam import tool_catalog
    from backend.db.tool_permission_store import SEED

    stored = {
        (str(r.get("connector_id") or ""), str(r.get("tool") or "")): r
        for r in permissions_service.list_decisions()
    }
    rows: List[ToolPermissionRow] = []
    listed: set[tuple[str, str]] = set()
    for owner, tool, _asks in tool_catalog():
        seed = permissions_service.seed_level(tool)
        row = stored.get((owner, tool)) or {}
        level = row.get("level")
        rows.append(
            ToolPermissionRow(
                tool=tool,
                connector_id=owner,
                level=level if level in permissions_service.LEVELS else seed,
                default_level=seed,
                description=_first_sentence(tool_description(tool) or ""),
                updated_at=str(row.get("updated_at") or ""),
            )
        )
        listed.add((owner, tool))
    for (owner, tool), row in stored.items():
        if not owner or not tool or (owner, tool) in listed:
            continue
        if owner.startswith(MCP_CONNECTOR_PREFIX):
            rows.append(
                ToolPermissionRow(
                    tool=tool,
                    connector_id=owner,
                    level=str(row.get("level") or ""),
                    default_level=permissions_service.DEFAULT_LEVEL,
                    updated_at=str(row.get("updated_at") or ""),
                )
            )
        elif row.get("source") != SEED:
            # A decision about a tool that no longer exists. (A leftover SEED row for one is
            # removed by the next sync and is nothing the owner ever set, so it is not shown.)
            rows.append(
                ToolPermissionRow(
                    tool=tool,
                    connector_id=owner,
                    level=str(row.get("level") or ""),
                    updated_at=str(row.get("updated_at") or ""),
                    tool_exists=False,
                )
            )
    core = permissions_service.CORE_CONNECTOR_ID
    return sorted(rows, key=lambda r: (r.connector_id != core, r.connector_id, r.tool))


def _list_response() -> ToolPermissionsResponse:
    rows = _rows()
    return ToolPermissionsResponse(permissions=rows, total=len(rows))


@router.get("/api/tool-permissions", response_model=ToolPermissionsResponse)
def get_all_tool_permissions() -> ToolPermissionsResponse:
    """Every tool and its value, built-in first — including the ones set through a connector's
    own ladder, because there is only one table. Each row carries its seed (``default_level``)
    so a client shows Reset exactly when the value differs from it."""
    return _list_response()


@router.put("/api/tool-permissions/{tool:path}", response_model=ToolPermissionsResponse)
def set_any_tool_permission(tool: str, req: SetLevelRequest) -> ToolPermissionsResponse:
    """Set one tool's value, then return the full list so the caller never needs a second
    round trip.

    The OWNER comes from ``connector_id`` when the caller supplied one and is RESOLVED from
    the registry otherwise (see ``SetLevelRequest.connector_id``). A resolved name is also
    required to BE a registered tool, so a decision is only ever recorded against a name
    the gate will actually look up; a caller-supplied owner is
    taken at face value, exactly as the connector-scoped routes take theirs, because the
    registry is not the authority on an MCP server's tool list.

    ``session_id`` names the CONVERSATION, not a scope. When it belongs to an Automation run,
    ``always_allow`` is routed to that Automation's own Always allowed list, so a tap on a run's
    card never changes the tool anywhere else. A Block is never written from a card: it is set
    deliberately, here (Settings → Permissions), on a connector's page, or on the Automation's
    Tool permissions section.
    """
    tool = (tool or "").strip()
    if not tool:
        raise HTTPException(status_code=422, detail="tool: is required")
    requested = tool
    tool, owner = _resolve_owner(tool, req.connector_id)

    if req.session_id and req.level == "always_allow":
        from backend.services.automations import runs

        if runs.grant_automation_tool(req.session_id, requested, "always"):
            # The open Automation page shows this list; a stale copy would be saved back over
            # this card's change by the next edit there.
            from backend.core.data_events import publish

            publish(["automations"])
            return _list_response()

    try:
        permissions_service.set_tool_permission(owner, tool, req.level)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return _list_response()


@router.delete("/api/tool-permissions/{tool:path}", response_model=ResetResponse)
def reset_any_tool_permission(tool: str, connector_id: Optional[str] = None) -> ResetResponse:
    """Reset one tool to its seed value. A tool whose row outlived it (renamed away) has no seed
    to go back to, so Reset deletes that row — which is why ``connector_id`` is accepted as a
    query parameter and the listing carries it: a stale or MCP-owned row's owner cannot be
    re-derived from a registry that no longer knows the name."""
    tool = (tool or "").strip()
    tool, owner = _resolve_owner(tool, connector_id)
    try:
        level = permissions_service.reset_tool_permission(owner, tool)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return ResetResponse(reset=tool, level=level)


def _resolve_owner(tool: str, supplied: Optional[str]) -> tuple[str, str]:
    """``(canonical_tool, owner)``. A supplied owner is taken verbatim; otherwise the tool
    must be a live registry name, whose ``connector=`` tag (or ``core``) is the owner.

    ``web_search`` and ``fetch_page`` belong to the ACTIVE search connector, so they resolve to
    that provider's own tool whatever owner was supplied — setting a level on either IS setting
    it on the search connector's page, never a ``core`` row of its own that could disagree.

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
        raise HTTPException(status_code=422, detail=f"tool: unknown tool: {tool}")
    owner, tool = permission_key(tool)
    return tool, owner
