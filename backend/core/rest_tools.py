"""Names of the generic REST connector's connectors and tools — one place, stdlib only.

Each REST API the owner adds is its own connector, ``rest:<slug>``, and brings its own tools,
``rest__<slug>__<tool>``: one per endpoint the owner declared (``<tool>`` is that endpoint's
stable id) plus three generic ones (``GENERIC``) for reading the list and for a path that was
not declared. They are ordinary registry tools (registered while the connector exists,
``ai.tools.rest_api.sync_rest_tools``), so a permission level, a confirm card and an
Automation's lists all work per connector AND per endpoint with no special case.

A leaf because three layers need the same answer: ``services`` names an endpoint and lists a
connector's tools, ``ai`` registers them, and ``db`` keys the rows.

ADR ``2026-11-10-generic-rest-connector-is-pinned-to-the-owners-host``.
"""

from __future__ import annotations

import re
from typing import Optional

KIND = "rest"
# The catalog's not-yet-configured "REST API" entry: the card Browse & add opens to ADD one.
LAUNCHER_ID = "rest"
CONNECTOR_PREFIX = "rest:"
TOOL_PREFIX = "rest__"

LIST_ENDPOINTS = "list_endpoints"
GET_DATA = "get_data"
SEND_REQUEST = "send_request"
GENERIC = (LIST_ENDPOINTS, GET_DATA, SEND_REQUEST)

# A tool name is at most 64 characters at every provider: ``rest__`` + slug + ``__`` + tool.
MAX_SLUG = 20
_MIN_WORD_CUT = 4
MAX_ENDPOINT_ID = 36
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SLUG = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slug_from_name(name: str) -> str:
    """The id a new API gets from its name. Fixed for the connector's life: its tool names
    are stored in Automations and transcripts, so a rename must not move them.

    A name too long for the id loses whole words from the end, never half of one: "Swagger
    Petstore - OpenAPI 3.0" cut at 20 characters read ``swagger_petstore_ope`` (owner,
    2026-11-11). A word is cut only when dropping it would leave almost nothing (under four
    characters): "A Supercalifragilistic…" is not the API ``a``."""
    slug = _NOT_SLUG.sub("_", (name or "").lower()).strip("_")
    if len(slug) <= MAX_SLUG:
        return slug
    cut = slug[: MAX_SLUG + 1].rfind("_")
    return slug[:cut] if cut >= _MIN_WORD_CUT else slug[:MAX_SLUG].strip("_")


def is_slug(value: str) -> bool:
    return bool(value) and len(value) <= MAX_SLUG and bool(_SLUG.match(value))


def connector_id(slug: str) -> str:
    return f"{CONNECTOR_PREFIX}{slug}"


def slug_of(connector: str) -> Optional[str]:
    """The slug of a ``rest:<slug>`` connector id, else ``None`` (the launcher included)."""
    if not connector.startswith(CONNECTOR_PREFIX):
        return None
    slug = connector[len(CONNECTOR_PREFIX) :]
    return slug if is_slug(slug) else None


def is_endpoint_id(value: str) -> bool:
    """A usable endpoint id: slug-shaped, short enough for a tool name, and not one of the
    generic tools' own names."""
    return (
        bool(value)
        and len(value) <= MAX_ENDPOINT_ID
        and bool(_SLUG.match(value))
        and value not in GENERIC
    )


def endpoint_id_from(method: str, path: str, operation_id: str = "") -> str:
    """The id a newly declared endpoint gets: its OpenAPI ``operationId`` when it has one
    (``listTransactions`` -> ``list_transactions``), else its method and path
    (``GET /accounts/{id}/transactions`` -> ``get_accounts_id_transactions``). May be empty or
    taken — the caller makes it valid and unique."""
    raw = _CAMEL.sub("_", operation_id or "") or f"{method} {path}"
    return _NOT_SLUG.sub("_", raw.lower()).strip("_")[:MAX_ENDPOINT_ID].strip("_")


def tool_name(slug: str, tool: str) -> str:
    return f"{TOOL_PREFIX}{slug}__{tool}"


def split_tool(name: str) -> Optional[tuple[str, str]]:
    """``(slug, tool)`` for a REST connector tool name — a generic tool or an endpoint's —
    else ``None``. Neither part can hold ``__``, so the split is unambiguous."""
    if not name.startswith(TOOL_PREFIX):
        return None
    slug, sep, tool = name[len(TOOL_PREFIX) :].partition("__")
    known = tool in GENERIC or is_endpoint_id(tool)
    return (slug, tool) if sep and known and is_slug(slug) else None


def is_rest_tool(name: str) -> bool:
    return split_tool(name) is not None
