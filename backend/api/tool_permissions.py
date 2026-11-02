"""
GET    /api/tool-permissions                          — the app-wide list of tool values
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
its tool still resolves. That join happens in ``ai.permission_api`` (``ai.permission_api_v2``
under ``permissions_v2``; ``ai.permissions.permission_api()`` picks, so this router never
branches) rather than in ``services``, which sits BELOW ``ai`` in the dependency rule and
cannot import the registry.

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

from typing import Annotated, Any, Callable, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from backend.ai.permissions import permission_api
from backend.core.errors import OpenNetWorthError, to_http_status

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


class SetLevelRequest(BaseModel):
    level: Level
    # The tool's OWNER, when the CALLER already knows it (Settings › Permissions always
    # does — it is re-setting a row it is displaying). Omitted, the server resolves it from
    # the tool's ``connector=`` tag. Explicit wins, and it is the only correct answer for a
    # row the registry cannot speak for: an MCP server's tool is not a registry name at
    # all, and two servers may each advertise a ``search``.
    connector_id: Optional[str] = None
    # WHICH conversation this decision was made in — NOT a scope. When it belongs to an
    # Automation run, Always allow goes to THAT Automation's own list instead of the global
    # table, so a card tap in one cannot change the tool for every other caller.
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


def _answer(call: Callable[[Any], dict]) -> dict:
    """Run one ``permission_api()`` call and map a domain error to its HTTP status."""
    try:
        return call(permission_api())
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/connectors/{connector_id}/permissions")
def get_permissions(connector_id: str) -> dict:
    """Every stored level for one connector.

    Never 404s on an unknown connector: "no decisions recorded" and "no such connector"
    are the same empty map, and the ladder is legitimately read for a connector that is
    not persisted yet (the add-an-MCP-server flow permissions what step 2 discovered).
    """
    return _answer(lambda api: api.connector(connector_id))


@router.put("/api/connectors/{connector_id}/permissions/{tool_name}")
def set_tool_permission(connector_id: str, tool_name: str, req: ToolLevelRequest) -> dict:
    """Set ONE tool's level, then return the connector's full map so the caller never
    needs a second round trip to re-render the ladder."""
    return _answer(lambda api: api.set_connector_tool(connector_id, tool_name, req.level))


@router.put("/api/connectors/{connector_id}/permissions")
def set_group_permission(connector_id: str, req: GroupLevelRequest) -> dict:
    """Stamp one level onto every tool named — the group control.

    Deliberately LOSSY: a per-tool override inside the group is overwritten. That is the
    control's purpose (§4 — "make this whole group behave"), and it is how a Custom group
    is flattened back to uniform in one action.
    """
    return _answer(lambda api: api.set_connector_group(connector_id, req.tools, req.level))


@router.delete("/api/connectors/{connector_id}/permissions")
def clear_permissions(connector_id: str) -> dict:
    """Forget every stored decision for this connector — its tools return to their default.

    Idempotent: clearing a connector with nothing stored returns the same empty map rather
    than 404ing, so a double-tap and a removed-then-re-added connector behave identically.
    """
    return _answer(lambda api: api.clear_connector(connector_id))


# ── App-wide: Settings › Permissions, the Automation picker ──────────────────────────


@router.get("/api/tool-permissions")
def get_all_tool_permissions() -> dict:
    """The app-wide list: the stored decisions, or (``permissions_v2``) every tool and its
    value — including the ones made through a connector's own ladder, because there is only one
    table."""
    return _answer(lambda api: api.list_tools())


@router.put("/api/tool-permissions/{tool:path}")
def set_any_tool_permission(tool: str, req: SetLevelRequest) -> dict:
    """Set one tool's level, then return the full list so the caller never needs a second
    round trip.

    ``{tool:path}`` rather than ``{tool}`` so the reserved ``*`` yolo row is settable and
    revocable through this same route.

    The OWNER comes from ``connector_id`` when the caller supplied one and is RESOLVED from
    the registry otherwise (see ``SetLevelRequest.connector_id``). A resolved name is also
    required to BE a registered tool, so a decision is only ever recorded against a name
    the gate will actually look up; a caller-supplied owner is taken at face value, exactly
    as the connector-scoped routes take theirs, because the registry is not the authority on
    an MCP server's tool list.

    ``session_id`` names the CONVERSATION, not a scope; what it changes is in
    ``ai.permission_api`` / ``ai.permission_api_v2``.
    """
    return _answer(lambda api: api.set_tool(tool, req.level, req.connector_id, req.session_id))


@router.delete("/api/tool-permissions/{tool:path}")
def clear_any_tool_permission(tool: str, connector_id: Optional[str] = None) -> dict:
    """Forget one tool's decision — it returns to its DEFAULT (Reset, under ``permissions_v2``).

    Clearing a STALE row (a tool since renamed away) is deliberately allowed: the row is
    real even though the tool is not, and leaving the user unable to clear it would be the
    same "no revocation surface" complaint this surface exists to answer. That is exactly
    why ``connector_id`` is accepted as a query parameter and why the listing carries it —
    a stale or MCP-owned row's owner cannot be re-derived from a registry that no longer
    knows the name, so the client hands back the one it was shown.
    """
    return _answer(lambda api: api.reset_tool(tool, connector_id))
