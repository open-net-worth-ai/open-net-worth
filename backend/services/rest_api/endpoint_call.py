"""One declared endpoint as a tool: the input schema Lucius fills, and the request it becomes.

An endpoint the owner declared (``{tool, method, path, description, params}``) is offered as
its own tool. Its parameters become the tool's arguments; ``resolve`` turns a call's arguments
back into the path, query and body that ``client.send`` sends. The method and the path
TEMPLATE are the endpoint's, fixed — a caller fills the placeholders and nothing else.

A parameter's name is the API's (``filter[status]``, ``page-size``); an argument key must be a
plain identifier, so each gets a safe key and ``resolve`` maps it back.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote

from backend.core.errors import ValidationError

_PLACEHOLDER = re.compile(r"\{([^{}/]+)\}")
_UNSAFE_KEY = re.compile(r"[^A-Za-z0-9_]")
_SCALAR = ["string", "number", "boolean"]
# Free-form arguments every endpoint tool takes besides its declared parameters.
EXTRA_QUERY = "query"
RAW_BODY = "body"


def _fields(endpoint: dict) -> list[dict]:
    """Every argument of the endpoint, in order: ``{key, name, in, required, description}``.
    A ``{placeholder}`` in the path with no declared parameter is one too — it has to be
    filled. Keys are unique; a later duplicate gets a numeric suffix."""
    declared = [p for p in endpoint.get("params") or [] if isinstance(p, dict) and p.get("name")]
    named = {str(p["name"]) for p in declared if p.get("in") == "path"}
    missing = [
        {"name": name, "in": "path", "required": True, "description": ""}
        for name in dict.fromkeys(_PLACEHOLDER.findall(str(endpoint.get("path") or "")))
        if name not in named
    ]
    fields: list[dict] = []
    taken = {EXTRA_QUERY, RAW_BODY}
    for param in [*missing, *declared]:
        base = _UNSAFE_KEY.sub("_", str(param["name"])).strip("_")[:48] or "param"
        key, n = base, 2
        while key in taken:
            key, n = f"{base}_{n}", n + 1
        taken.add(key)
        place = str(param.get("in") or "query")
        fields.append(
            {
                "key": key,
                "name": str(param["name"]),
                "in": place,
                "required": bool(param.get("required")) or place == "path",
                "description": str(param.get("description") or ""),
            }
        )
    return fields


def _takes_raw_body(endpoint: dict, fields: list[dict]) -> bool:
    """A free-form ``body`` is offered, and accepted, only where the owner declared no body
    field and the method can carry one. Declared fields are the whole body otherwise."""
    return endpoint.get("method") != "GET" and not any(f["in"] == "body" for f in fields)


_WHERE = {"path": "In the path", "query": "Query parameter", "body": "Body field"}


def input_schema(endpoint: dict) -> dict:
    """The tool's ``input_schema``: one property per parameter, an optional ``query`` object
    for anything not declared (a page, a cursor), and for a method that takes a body a
    ``body`` object when no body field was declared."""
    fields = _fields(endpoint)
    properties: dict[str, dict] = {}
    for f in fields:
        where = _WHERE.get(f["in"], "Query parameter")
        label = where if f["key"] == f["name"] else f"{where} '{f['name']}'"
        prop: dict[str, Any] = {"description": f"{label}. {f['description']}".strip()}
        if f["in"] != "body":
            prop["type"] = _SCALAR
        properties[f["key"]] = prop
    properties[EXTRA_QUERY] = {
        "type": "object",
        "additionalProperties": True,
        "description": "Any other query-string parameters, e.g. a page or cursor.",
    }
    if _takes_raw_body(endpoint, fields):
        properties[RAW_BODY] = {
            "type": "object",
            "additionalProperties": True,
            "description": "The JSON request body, as an object. Omit for none.",
        }
    return {
        "type": "object",
        "properties": properties,
        "required": [f["key"] for f in fields if f["required"]],
        "additionalProperties": False,
    }


def resolve(endpoint: dict, args: dict) -> tuple[str, dict, Optional[dict]]:
    """``(path, query, body)`` for one call. A path value is percent-encoded whole, so it
    stays ONE segment (``a/b`` cannot add a level; ``..`` is refused downstream by
    ``client.build_url``)."""
    args = args if isinstance(args, dict) else {}
    fields = _fields(endpoint)
    takes_raw_body = _takes_raw_body(endpoint, fields)
    known = {f["key"] for f in fields} | {EXTRA_QUERY} | ({RAW_BODY} if takes_raw_body else set())
    unknown = sorted(set(args) - known)
    if unknown:
        # Dispatch checks argument TYPES only, so an argument this endpoint never declared is
        # refused here — an approval given for "this endpoint, these fields" has to hold.
        raise ValidationError(f"This endpoint takes no argument named {', '.join(unknown)}.")
    path = str(endpoint.get("path") or "")
    extra = args.get(EXTRA_QUERY)
    query: dict = dict(extra) if isinstance(extra, dict) else {}
    body: dict = {}
    for f in fields:
        value = args.get(f["key"])
        if value is None or value == "":
            if f["required"]:
                raise ValidationError(f"'{f['key']}' is required.")
            continue
        if f["in"] == "path":
            path = path.replace("{" + f["name"] + "}", quote(str(value), safe=""))
        elif f["in"] == "body":
            body[f["name"]] = value
        else:
            query[f["name"]] = value
    raw = args.get(RAW_BODY)
    if takes_raw_body and isinstance(raw, dict):
        body = raw
    return path, query, (body or None)
