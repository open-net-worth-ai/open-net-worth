"""Connector CONFIGURATION reads — the permission ladder and the external-MCP server list.

The sibling of ``ai/tools/connectors.py``, which is about OPERATING a connector (status,
sync, enable). This module is about INSPECTING how a connector is set up: what Lucius is
allowed to do with each of its tools, and which external MCP servers exist. Split rather
than appended because that file is already at the ~400-line guideline and the two answer
different questions.

## Read-only, deliberately and permanently

Both tools here are reads, and the two obvious write twins are **structurally excluded** —
this is a design boundary, not a backlog:

**No tool may WRITE a permission.** No ``set_tool_permission``, no ``set_group_permission``,
and no permission argument smuggled into another tool. A model that can raise its own tools
to ``allow`` converts ONE approved confirm card into a permanent bypass of every future
confirm card, which is precisely the ladder the redesign exists to build. Permission writes
are REST/UI only (``api/connector_permissions.py``), exactly as the sandbox's kill switches
are REST-only. (That last property is real but has no named guard enforcing it: it holds
because no tool under ``ai/`` reaches ``AppConfigStore.set_flag``, not because any check
rejects the attempt. Earlier text here credited a ``_AI_FORBIDDEN_UPDATE_KEYS`` constant that
does not exist anywhere in the codebase — corrected 2026-08-28. Naming a guard that isn't
there is how a reviewer ends up skipping the check that would have found its absence, which
is exactly how three privacy sweeps missed a national ID sitting in a docstring that
mislabelled it.) Reading the ladder is safe and useful —
Lucius can explain why a call needs approval and point the user at the control — so the read
half ships and the write half never does.

**No tool may write CREDENTIALS or add a server.** Setup stays human-only, everywhere (RFC
``.claude/rfcs/2026-08-10-connectors-architecture.md`` §B4: "Lucius can guide, enable, and
operate a connector once it exists — setup itself stays human-only"). ``list_mcp_servers``
therefore projects the store's ``public()`` view and re-allowlists on top of it, so a token
cannot reach the model even if a future field is added to that view.

## Two rules for everything a SERVER chose

An external MCP server picks its own tool names and descriptions, its own ``serverInfo``
provenance, and (via the body of an HTTP error) a slice of ``last_error``. All of it is
third-party text this module puts in front of the model, so all of it obeys two rules:

1. **"Secret-stripped" is not the same as "safe to show a model."** The store's ``public()``
   withholds the token and the ``env`` VALUES, but ``endpoint`` is the whole connection
   string — and a credential in a stdio server's argv or an http server's query string is
   the documented quick start for several real MCP servers. So the endpoint is NOT
   projected; ``endpoint_hint`` (``models.mcp_client.redact_endpoint``) is, and it carries
   a host or a command basename and nothing else.
2. **Every server-chosen string is sanitized and capped** (``_clean``, over
   ``core.untrusted.sanitize_untrusted``), as every other untrusted-text channel does (``ai/tools/misc.py``,
   ``web_search_providers.py``, ``screen.py``, ``email_ingest.py``). Unsanitized, a hostile
   server can put ``«user_msg:hex»…«/user_msg:hex»`` in a tool description — the ONE fence in
   this codebase that marks content as the OPERATOR speaking — and forge an instruction into
   the context through a plain read. Sanitized, NOT fenced: these are JSON tool results,
   where JSON escaping is the delimiter and a textual fence is the wrong layer (ADR
   ``2026-07-19-fence-only-flat-prompts-not-json-tool-results``).
"""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import Field

from backend.ai.permissions import engine
from backend.ai.tools.connectors import DIRECTORY_NOTE
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import did_you_mean
from backend.core.untrusted import sanitize_untrusted

# One page of tools / servers. Big enough that a realistic connector answers in one call
# (the largest first-party bundle is 3 tools; a chatty MCP server is the only thing that
# pages), small enough that a 200-tool server cannot flood a turn.
_TOOL_PAGE = 50
_SERVER_PAGE = 50

# Caps on the server-chosen strings below. A tool DESCRIPTION is the one field with real
# prose in it, so it gets the roomy cap; a name / publisher / version is a LABEL, and a
# server answering with a megabyte of it is not naming itself. ``last_error`` embeds up to
# 200 chars of an attacker-chosen HTTP error body inside our own wrapper, so it is capped
# just above that. Nothing here relies on the transport's own caps — ``_clean_info_field``
# bounds ``serverInfo`` at 200 but a tool description was never bounded at all, and a page
# of 50 unbounded descriptions is how one server floods a turn.
_MAX_DESCRIPTION_CHARS = 500
_MAX_LABEL_CHARS = 200
_MAX_ERROR_CHARS = 300


def _clean(value: Any, limit: int = _MAX_LABEL_CHARS) -> Any:
    """Sanitize + cap one server-chosen string; pass any non-string through untouched.

    ``None`` must stay ``None`` (an absent publisher is a different fact from an empty one —
    the provenance panel's whole point), so this is deliberately not ``str(value or "")``.
    """
    if not isinstance(value, str):
        return value
    return sanitize_untrusted(value, max_len=limit)


def _page_bounds(input_data: dict, default_limit: int) -> tuple[int, int]:
    """``(limit, offset)`` from the call, clamped to sane values. A limit of 0 or less is
    treated as the default rather than as "everything" — an unbounded read is the thing the
    tool-result convention exists to prevent."""
    raw_limit = input_data.get("limit")
    raw_offset = input_data.get("offset")
    try:
        limit = int(raw_limit) if raw_limit is not None else default_limit
    except (TypeError, ValueError):
        limit = default_limit
    try:
        offset = max(0, int(raw_offset)) if raw_offset is not None else 0
    except (TypeError, ValueError):
        offset = 0
    return (limit if limit > 0 else default_limit), offset


# ── 1. the permission ladder ────────────────────────────────────────────────────────────


class GetConnectorToolsInput(ToolInput):
    """Schema-of-record for ``get_connector_tools`` (see ``schema_from_model``)."""

    connector_id: str = Field(
        description=(
            "The connector to read, e.g. 'email', 'telegram', 'plaid', 'ibkr', "
            "'web_search:tavily', or 'mcp:<server_id>' for an external MCP server "
            "(list_mcp_servers gives you those ids)."
        )
    )
    group: Optional[Literal["read", "write"]] = Field(
        default=None,
        description=(
            "Optional: show only one group. 'read' = tools that only read, 'write' = tools "
            "with side effects. Omit for both. Filtering here is applied BEFORE the page "
            "limit, so it is how you narrow a large server rather than paging through it."
        ),
    )
    limit: Optional[int] = Field(
        default=None, description=f"Max tools to return. Default {_TOOL_PAGE}."
    )
    offset: Optional[int] = Field(default=None, description="Skip this many tools. Default 0.")


def _group_block(
    tools: list, key: str, limit: int, offset: int, defaults: Optional[dict[str, str]] = None
) -> dict:
    """One group's block: the DERIVED aggregate level over the WHOLE group, plus a page.

    The aggregate is computed over every tool in the group BEFORE the page is cut — a level
    derived from a partial page would report 'allow' for a group holding a blocked tool the
    page did not reach, which is the one direction this summary must never be wrong in.

    ``name``/``description`` are sanitized and capped for EVERY family rather than only for
    ``source == "mcp"``: an external server chose both of those strings, and branching on
    the source would make the safety of this loop depend on a field one of the two families
    could get wrong. Sanitizing our own native descriptions costs nothing (they contain no
    control characters and no guillemets, so the call is a no-op on them).
    """
    from backend.services import tool_permissions

    total = len(tools)
    page = tools[offset : offset + limit]
    return {
        "group": key,
        # Uniform group -> that level; disagreeing -> "custom" (a DISPLAY state — there is
        # no such stored level, and nothing can be set to it).
        "level": tool_permissions.derive_group_level([t.level for t in tools]),
        "tools": [
            {
                "name": _clean(t.name),
                "level": t.level,
                "description": _clean(t.description, _MAX_DESCRIPTION_CHARS),
                "source": t.source,
                **({"default": defaults.get(t.name)} if defaults else {}),
            }
            for t in page
        ],
        "showing": len(page),
        "offset": offset,
        "total": total,
        "has_more": offset + len(page) < total,
    }


@tool(
    "get_connector_tools",
    description=(
        "What can a connector do, and what needs approval? Lists EVERY tool it contributes — "
        "the complete set, not search hits — so use it when the user asks what a server or "
        "connector can do, or before saying it has no tool for something. To find a tool for a "
        "task, find_tools(query=…) is cheaper. "
        "grouped into read-only vs. write tools, each with its current level — 'always_allow' (runs "
        "without asking, unattended included), 'needs_approval' (raises a confirm card the "
        "user taps first; the DEFAULT for anything undecided), or 'blocked' (refused "
        "outright). Each group also reports its aggregate level, which is 'custom' when the "
        "tools inside it disagree. Every level here is ENFORCED: it is what decides whether "
        "one of this connector's calls fires, on every surface. Use this to explain why a "
        "call needed approval or was refused, and to tell the user exactly which control to "
        "change. YOU CANNOT CHANGE A LEVEL — there is deliberately no tool for that; the "
        "user sets levels themselves in Connectors, opening that connector and using the "
        "permission control. Never returns a credential."
    ),
    input_schema=schema_of(GetConnectorToolsInput, tool_name="get_connector_tools"),
)
async def get_connector_tools(input_data: dict):
    from backend.core.errors import NotFoundError
    from backend.services import connectors as connectors_service

    connector_id = str(input_data.get("connector_id") or "").strip()
    if not connector_id:
        return json.dumps({"error": "connector_id is required"}, ensure_ascii=False), None

    try:
        view = connectors_service.get_connector(connector_id)
    except NotFoundError:
        # The full id list rides along rather than a "call X for the list" pointer: there
        # is no tool that lists connectors, and naming this one would send the model in a
        # circle. The set is bounded by the install's own connectors, so it is cheap.
        known = [v.id for v in connectors_service.list_connectors()]
        close = did_you_mean(connector_id, known)
        head = f"unknown connector: {connector_id}."
        if close:
            head += f" Closest existing: {', '.join(repr(c) for c in close)}."
        return json.dumps({"error": head, "known_connector_ids": known}, ensure_ascii=False), None

    limit, offset = _page_bounds(input_data, _TOOL_PAGE)
    wanted = input_data.get("group")
    default_level, tool_defaults = engine().connector_defaults(view)
    blocks = []
    for key, is_read in (("read", True), ("write", False)):
        if wanted and wanted != key:
            continue
        blocks.append(
            _group_block(
                [t for t in view.tools if t.read_only is is_read],
                key,
                limit,
                offset,
                tool_defaults,
            )
        )

    total = sum(b["total"] for b in blocks)
    showing = sum(b["showing"] for b in blocks)
    payload = {
        "connector_id": view.id,
        "connector": _clean(view.name) or view.id,
        "enabled": view.enabled,
        "status": view.status,
        # Shipped every time so nothing hardcodes what an undecided tool means.
        "default_level": default_level,
        "groups": blocks,
        "showing": showing,
        "total": total,
        "has_more": any(b["has_more"] for b in blocks),
    }
    notes: list[str] = []
    if not total:
        notes.append(
            f"'{view.id}' contributes no tools"
            + (f" in the '{wanted}' group" if wanted else "")
            + ". A transport-only connector (Telegram, webhook) delivers messages rather "
            "than exposing tools, and an external MCP server lists nothing until its "
            "connection has been tested at least once."
        )
    elif payload["has_more"]:
        notes.append(
            f"Showing {showing} of {total} tools. Narrow with group='read'/'write', or page "
            f"with offset={offset + max(b['showing'] for b in blocks)}."
        )
    if notes:
        payload["note"] = " ".join(notes)
    return json.dumps(payload, ensure_ascii=False), None


# ── 2. the external-MCP server list ─────────────────────────────────────────────────────

# The ONLY keys copied out of ``McpClientStore.public()``. ``public()`` already strips the
# token and the ``env`` VALUES, so this is a second, independent gate rather than the first
# one: a field added to that view later cannot reach the model without being named here.
# ``env_keys``, ``auth_header`` and the raw ``discovered_tools`` descriptors are deliberately
# absent — a key NAME is still a fact about the user's secrets, and the tool list belongs to
# ``get_connector_tools`` where it comes with its levels.
#
# ``endpoint``/``url``/``command``/``args`` are absent for a HARDER reason than tidiness: the
# endpoint is where the credential lives on a large share of real MCP servers. ``npx -y
# @modelcontextprotocol/server-postgres postgresql://admin:pw@db/finance`` and
# ``https://vendor/sse?api_key=…`` are both the vendor's own documented quick start, and
# ``POST /api/mcp-clients/import`` ingests exactly those config blocks verbatim. Handing that
# string to the model puts a working credential one ``send_email`` away. ``endpoint_hint``
# below (host / command basename) is what replaces it.
_SERVER_FIELDS = ("id", "name", "transport", "enabled", "last_status", "last_error")


class ListMcpServersInput(ToolInput):
    """Schema-of-record for ``list_mcp_servers`` (see ``schema_from_model``)."""

    enabled_only: Optional[bool] = Field(
        default=None,
        description=(
            "True to list only enabled servers. Omit for all of them, including ones the "
            "user added but has not switched on."
        ),
    )
    limit: Optional[int] = Field(
        default=None, description=f"Max servers to return. Default {_SERVER_PAGE}."
    )
    offset: Optional[int] = Field(default=None, description="Skip this many servers. Default 0.")


def _public_server(pub: dict) -> dict:
    """One server, projected from the store's secret-stripped ``public()`` view.

    Provenance is grouped under its own key and labelled as a CLAIM on purpose: every field
    in it is what the server said about itself during the handshake, never our finding.
    ``verification`` is the one exception and is set only by the curated-catalog path — a
    server can never talk its own badge up, which is why it is a three-state field rather
    than a boolean ('unknown' = we have no idea who this is; 'unverified' = it came from our
    catalog and is NOT the vendor's official server).

    Every string the SERVER chose is sanitized + capped on the way through (module docstring
    rule 2). ``id`` and ``transport`` are exempt because neither is free text — an id is
    restricted to ``[a-z0-9-]`` by ``McpServerConfig`` and transport is a two-value Literal.
    """
    from backend.models.mcp_client import mcp_connector_id, redact_endpoint

    out: dict = {k: pub.get(k) for k in _SERVER_FIELDS}
    out["name"] = _clean(out.get("name"))
    # A LOCAL ('stdio') server's ``last_error`` can trace back to a spawned child's own
    # stderr — the store scrubs that at write time (``McpCallError.safe_summary``,
    # ``client_manager._discover``), but this is the belt for that suspenders: a stdio
    # server's diagnostic detail is withheld from the model entirely, the same way its
    # ``endpoint`` is (see the module docstring, rule 1) rather than trusted to have been
    # cleaned by every past and future writer of this field.
    out["last_error"] = (
        None if pub.get("transport") == "stdio" else _clean(out.get("last_error"), _MAX_ERROR_CHARS)
    )
    # The id the permission ladder is keyed by — hand it over so the model never has to
    # know how to spell the namespace itself.
    out["connector_id"] = mcp_connector_id(str(pub.get("id") or ""))
    # REDACTED, never the endpoint itself — see ``_SERVER_FIELDS`` and ``redact_endpoint``.
    out["endpoint_hint"] = redact_endpoint(
        str(pub.get("transport") or "http"),
        str(pub.get("url") or ""),
        str(pub.get("command") or ""),
    )
    out["has_token"] = bool(pub.get("has_token"))
    out["tool_count"] = len(pub.get("discovered_tools") or [])
    out["provenance"] = {
        "publisher": _clean(pub.get("publisher")),
        "verification": pub.get("verification"),
        "self_reported_name": _clean(pub.get("server_info_name")),
        "self_reported_version": _clean(pub.get("server_info_version")),
        "homepage_url": _clean(pub.get("homepage_url")),
        "repo_url": _clean(pub.get("repo_url")),
    }
    return out


@tool(
    "list_mcp_servers",
    description=(
        "List the external Model Context Protocol (MCP) servers configured on this instance "
        "— third-party tool servers the user connected. Each entry gives its id and "
        "'connector_id' (pass that to get_connector_tools to see what you may call), "
        "name, transport ('http' remote or 'stdio' local), an 'endpoint_hint', whether it is "
        "enabled, the last connection status, how many tools it advertised, and its "
        "provenance — publisher plus a 'verification' of 'verified' / 'unverified' / "
        "'unknown'. Provenance is the SERVER'S OWN CLAIM about itself, not a finding of "
        "ours: treat 'unknown' as unknown, never as safe. 'endpoint_hint' is DELIBERATELY "
        "PARTIAL — the host for a remote server, the command name for a local one — because "
        "a full endpoint routinely contains a password or an API key; the whole endpoint is "
        "shown to the user in Connectors and is not available to you. 'last_error' is given "
        "for a remote ('http') server but WITHHELD (null) for a local ('stdio') one — a "
        "local server's diagnostic detail can trace back to a locally spawned process's own "
        "output, which this tool treats as untrusted like everything else here; point the "
        "user at Connectors for it. Never returns tokens, credentials, endpoints, or the "
        "names of environment variables. Servers are added and configured by the user in "
        "the UI (Connectors), never by you — there is no tool that adds one."
    ),
    input_schema=schema_of(ListMcpServersInput, tool_name="list_mcp_servers"),
)
async def list_mcp_servers(input_data: dict):
    from backend.db.mcp_client_store import McpClientStore

    servers = McpClientStore().public()
    if input_data.get("enabled_only"):
        servers = [s for s in servers if s.get("enabled")]

    limit, offset = _page_bounds(input_data, _SERVER_PAGE)
    total = len(servers)
    page = [_public_server(s) for s in servers[offset : offset + limit]]
    payload = {
        "servers": page,
        "showing": len(page),
        "offset": offset,
        "total": total,
        "has_more": offset + len(page) < total,
    }
    if not total:
        payload["note"] = (
            "No external MCP servers are configured. The user adds one in Connectors → "
            "Browse & add; you cannot add one for them."
        )
    elif payload["has_more"]:
        nxt = offset + len(page)
        payload["note"] = f"Showing {len(page)} of {total} servers. Page with offset={nxt}."
    payload["more_in_directory"] = DIRECTORY_NOTE
    return json.dumps(payload, ensure_ascii=False), None
