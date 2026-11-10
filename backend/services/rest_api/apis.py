"""The APIs of the generic REST connector — validation and the two projections.

Each API is its own connector (``rest:<slug>``). It is what the OWNER declared at setup: a base URL, how to authenticate, optional fixed
headers, an endpoint list (typed by hand or imported from an OpenAPI document) and free-text
notes. Lucius never writes any of it.

Two projections, never mixed:

  * ``operator_view`` — the owner's own settings screen: the base URL as typed, header NAMES,
    ``has_value`` for the credential. Never a credential VALUE.
  * ``model_view``    — what Lucius reads to form a request: the host only (a base URL is a
    credential — ``models.mcp_client.redact_endpoint``), the auth TYPE, endpoints and notes.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

from backend.core import rest_tools
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.untrusted import sanitize_untrusted
from backend.db.rest_config_store import RestConfigStore
from backend.models.mcp_client import redact_endpoint
from backend.services import tool_permissions

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
AUTH_TYPES = ("none", "bearer", "basic", "header", "query")
PARAM_PLACES = ("path", "query", "body")
# Bounds on what ONE API can declare — each endpoint is a tool of its own. They exist for a
# hostile or runaway OpenAPI document, far above any hand-written list, and are refused, never
# silently trimmed, on save.
MAX_ENDPOINTS = 500
MAX_PARAMS = 100
# Past this many endpoints in one answer, Lucius gets them as "METHOD /path" lines and
# narrows with ``search`` to read the parameters.
COMPACT_OVER = 100

_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
# Headers the client sets itself, or that would re-route the request.
_RESERVED_HEADERS = frozenset({"host", "content-length", "authorization", "transfer-encoding"})


def _text(value: Any, limit: int) -> str:
    return sanitize_untrusted(str(value or "").strip(), max_len=limit)


def _one_line(value: Any, what: str) -> str:
    """A credential part as typed, refused if it holds a control character: it goes into a
    header or a URL, and one that http.client rejects is quoted back in its error."""
    text = str(value or "").strip()
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ValidationError(f"The {what} cannot contain line breaks or control characters.")
    return text


# ── validation ────────────────────────────────────────────────────────────────────────────


def clean_base_url(value: Any) -> str:
    """The base URL every request of this API is pinned under. http(s), a host, no query or
    fragment (they would be cut off by the path that is appended) and no ``user:pass@``
    (a credential belongs in the auth fields, where it is never echoed)."""
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValidationError("Base URL must start with http:// or https:// and name a host.")
    if parts.username or parts.password:
        raise ValidationError("Put the username and password in Authentication, not in the URL.")
    if parts.query or parts.fragment:
        raise ValidationError("Base URL cannot contain ? or # — put the key in Authentication.")
    try:
        parts.port
    except ValueError:
        raise ValidationError("Base URL has an invalid port.")
    return url.rstrip("/")


def _clean_auth(raw: Any, saved: Optional[dict]) -> dict:
    """``{type, name, username, value}``. A blank ``value`` keeps the saved one (write-only
    secret) as long as the type did not change."""
    raw = raw if isinstance(raw, dict) else {}
    saved = saved or {}
    kind = str(raw.get("type") or "none").strip().lower()
    if kind not in AUTH_TYPES:
        raise ValidationError(f"Unknown authentication type '{kind}'.")
    if kind == "none":
        return {"type": "none"}
    value = _one_line(raw.get("value"), "key")
    if not value and saved.get("type") == kind:
        value = str(saved.get("value") or "")
    if not value:
        raise ValidationError("Authentication needs a key, token or password.")
    auth = {"type": kind, "value": value}
    if kind == "basic":
        auth["username"] = _one_line(raw.get("username"), "username")
        if not auth["username"]:
            raise ValidationError("Basic authentication needs a username.")
    if kind in ("header", "query"):
        name = _one_line(raw.get("name"), "name")
        if not name or (kind == "header" and not _HEADER_NAME.match(name)):
            raise ValidationError("Authentication needs the header or parameter name.")
        if kind == "header" and name.lower() in _RESERVED_HEADERS - {"authorization"}:
            raise ValidationError(f"'{name}' cannot be used as the key header.")
        auth["name"] = name
    return auth


def _clean_headers(raw: Any, saved: Optional[dict]) -> dict:
    """Fixed headers sent with every request (an API version, say). A blank value keeps the
    saved value of the same name."""
    if raw is None:
        return dict(saved or {})
    if not isinstance(raw, dict):
        raise ValidationError("Headers must be name/value pairs.")
    headers: dict[str, str] = {}
    for key, val in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        if not _HEADER_NAME.match(name) or name.lower() in _RESERVED_HEADERS:
            raise ValidationError(f"'{name}' is not a header this connector can send.")
        value = str(val or "").strip() or str((saved or {}).get(name) or "")
        if not value or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValidationError(f"Header '{name}' needs a single-line value.")
        headers[name] = value
    return headers


def _clean_params(raw: Any) -> list[dict]:
    params: list[dict] = []
    if isinstance(raw, list) and len(raw) > MAX_PARAMS:
        raise ValidationError(f"An endpoint can list at most {MAX_PARAMS} parameters.")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 120)
        if not name:
            continue
        place = str(item.get("in") or "query").strip().lower()
        params.append(
            {
                "name": name,
                "in": place if place in PARAM_PLACES else "query",
                "required": bool(item.get("required")),
                "description": _text(item.get("description"), 300),
            }
        )
    return params


def _endpoint_id(item: dict, method: str, path: str, taken: set[str]) -> str:
    """This endpoint's tool id: the one it already has, else one made from its OpenAPI
    ``operationId`` or its method and path — valid, and unique in this API. An endpoint that is
    edited keeps its id, so the tool Lucius and an Automation know it by does not move."""
    given = str(item.get("tool") or "").strip()
    base = (
        given
        if rest_tools.is_endpoint_id(given)
        else rest_tools.endpoint_id_from(method, path, str(item.get("operation_id") or ""))
    )
    if not rest_tools.is_endpoint_id(base):
        base = f"{method.lower()}_endpoint"
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"_{n}"
        candidate = base[: rest_tools.MAX_ENDPOINT_ID - len(suffix)].rstrip("_") + suffix
        n += 1
    taken.add(candidate)
    return candidate


def _chosen_level(item: dict, path: str) -> Optional[str]:
    """The permission the owner picked on this endpoint's row of the form, if they picked one.
    Absent means "leave it as it is": an untouched endpoint keeps its default, whatever the
    permission engine makes that."""
    level = item.get("permission")
    if level in (None, ""):
        return None
    if level not in tool_permissions.LEVELS:
        raise ValidationError(f"Unknown permission '{level}' on {path}.")
    return str(level)


def clean_endpoints(raw: Any, chosen: Optional[dict[str, str]] = None) -> list[dict]:
    """The endpoint list as stored: a tool id, method, path, a description and its
    parameters. An entry with no path is dropped; an unknown method is refused (a typo should
    not vanish). ``chosen`` collects, by tool id, the permission picked on an endpoint's row:
    it is the owner's decision about the tool, never part of what the API holds."""
    endpoints: list[dict] = []
    taken: set[str] = set()
    if isinstance(raw, list) and len(raw) > MAX_ENDPOINTS:
        raise ValidationError(
            f"An API can list at most {MAX_ENDPOINTS:,} endpoints. Remove the ones you don't need."
        )
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        path = _text(item.get("path"), 500)
        if not path:
            continue
        method = str(item.get("method") or "GET").strip().upper()
        if method not in METHODS:
            raise ValidationError(f"Unknown method '{method}' on {path}.")
        path = path if path.startswith("/") else f"/{path}"
        level = _chosen_level(item, path)
        tool = _endpoint_id(item, method, path, taken)
        endpoints.append(
            {
                "tool": tool,
                "method": method,
                "path": path,
                "description": _text(item.get("description"), 500),
                "params": _clean_params(item.get("params")),
            }
        )
        if level is not None and chosen is not None:
            chosen[tool] = level
    return endpoints


# ── reads ─────────────────────────────────────────────────────────────────────────────────


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _usable(endpoint: Any) -> bool:
    return (
        isinstance(endpoint, dict)
        and rest_tools.is_endpoint_id(str(endpoint.get("tool") or ""))
        and str(endpoint.get("method") or "") in METHODS
        and str(endpoint.get("path") or "").startswith("/")
    )


def _stored_endpoints(raw: Any) -> list[dict]:
    """A stored endpoint list made safe to read. A row can also arrive through Move, where what
    it holds was written by another instance and never passed ``clean_endpoints``: an endpoint
    that is not a whole one, or repeats an id (two tools cannot share a name), is left out —
    never guessed at — and the text and bounds are applied again."""
    endpoints: list[dict] = []
    seen: set[str] = set()
    for item in (raw if isinstance(raw, list) else [])[:MAX_ENDPOINTS]:
        if not _usable(item) or item["tool"] in seen:
            continue
        path = _text(item["path"], 500)
        if not path.startswith("/"):
            continue
        seen.add(item["tool"])
        params = item.get("params")
        endpoints.append(
            {
                "tool": item["tool"],
                "method": item["method"],
                "path": path,
                "description": _text(item.get("description"), 500),
                "params": _clean_params(params[:MAX_PARAMS] if isinstance(params, list) else []),
            }
        )
    return endpoints


def _normalised(slug: str, stored: dict) -> dict:
    """A stored API with its shapes made safe to read (see ``_stored_endpoints``)."""
    return {
        "id": slug,
        "name": str(stored.get("name") or slug),
        "enabled": bool(stored.get("enabled")),
        "base_url": str(stored.get("base_url") or ""),
        "auth": _dict(stored.get("auth")) or {"type": "none"},
        "headers": {str(k): str(v) for k, v in _dict(stored.get("headers")).items()},
        "endpoints": _stored_endpoints(stored.get("endpoints")),
        "notes": str(stored.get("notes") or ""),
    }


def load(slug: str) -> dict:
    """One API as stored — credentials included; service callers only (``client``, the
    tools). ``NotFoundError`` when there is none."""
    stored = RestConfigStore().get(slug) if rest_tools.is_slug(slug) else None
    if stored is None:
        raise NotFoundError(f"No REST API with id '{slug}'.")
    return _normalised(slug, stored)


def load_all() -> list[dict]:
    """Every API as stored, oldest first, in two reads however many there are — what tool
    registration walks on every offer."""
    return [_normalised(stored["slug"], stored) for stored in RestConfigStore().all()]


def find_endpoint(api: dict, tool: str) -> dict:
    hit = next((e for e in api["endpoints"] if e["tool"] == tool), None)
    if hit is None:
        raise NotFoundError(
            f"'{api['name']}' no longer has the endpoint '{tool}'. Its list_endpoints tool "
            "shows what it has now."
        )
    return hit


def operator_view(api: dict) -> dict:
    auth = api["auth"]
    return {
        "id": api["id"],
        "connector_id": rest_tools.connector_id(api["id"]),
        "name": api["name"],
        "enabled": api["enabled"],
        "base_url": api["base_url"],
        "auth": {
            "type": auth.get("type") or "none",
            "name": auth.get("name") or "",
            "username": auth.get("username") or "",
            "has_value": bool(auth.get("value")),
        },
        "header_names": sorted(api["headers"]),
        "endpoints": _with_levels(api),
        "notes": api["notes"],
    }


def _with_levels(api: dict) -> list[dict]:
    """Each endpoint with the level its tool is at now, as the connector's own page shows it
    (the same read: ``connectors.get_connector``), so the form and that page cannot disagree."""
    from backend.services import connectors as connectors_service

    try:
        view = connectors_service.get_connector(rest_tools.connector_id(api["id"]))
    except NotFoundError:
        return api["endpoints"]
    levels = {t.name: t.level for t in view.tools}
    return [
        {**e, "level": levels.get(rest_tools.tool_name(api["id"], e["tool"]))}
        for e in api["endpoints"]
    ]


def _matches(endpoint: dict, words: list[str]) -> bool:
    text = f"{endpoint.get('method')} {endpoint.get('path')} {endpoint.get('description')}"
    return all(w in f"{text} {endpoint.get('tool')}".lower() for w in words)


def model_view(api: dict, *, search: str = "") -> dict:
    """What Lucius may read about one API. ``search`` keeps the endpoints whose method, path
    or description contains every word of it."""
    words = [w for w in search.lower().split() if w]
    endpoints = [e for e in api["endpoints"] if _matches(e, words)] if words else api["endpoints"]
    view: dict = {
        "name": api["name"],
        "host": redact_endpoint("http", url=api["base_url"]),
        "auth": api["auth"].get("type") or "none",
        "notes": api["notes"],
        "endpoint_count": len(api["endpoints"]),
    }
    if len(endpoints) > COMPACT_OVER:
        view["endpoints"] = [f"{e['tool']}: {e['method']} {e['path']}" for e in endpoints]
        view["endpoints_note"] = (
            f"{len(endpoints)} endpoints, shown as tool id, method and path only. Pass "
            "`search` to narrow to the ones you need and see their parameters."
        )
    else:
        view["endpoints"] = endpoints
    return view


def get_operator_api(slug: str) -> dict:
    return operator_view(load(slug))


# ── writes ────────────────────────────────────────────────────────────────────────────────


def _name_taken(name: str, *, besides: Optional[str]) -> bool:
    return any(
        row["slug"] != besides and str(row["name"]).lower() == name.lower()
        for row in RestConfigStore().rows()
    )


def _new_slug(payload: dict, name: str) -> str:
    """The id of an API being created: the one the owner typed, else the name's. It is the
    owner's only chance to choose it, so a typed id is checked, never tidied into another."""
    typed = str(payload.get("id") or "").strip()
    if typed:
        if not rest_tools.is_slug(typed):
            raise ValidationError(
                "The ID takes lowercase letters, digits and single underscores, "
                f"up to {rest_tools.MAX_SLUG} characters."
            )
        return typed
    slug = rest_tools.slug_from_name(name)
    if not rest_tools.is_slug(slug):
        raise ValidationError("The name needs at least one letter or digit (a to z, 0 to 9).")
    return slug


def save_api(payload: dict, slug: Optional[str] = None) -> dict:
    """Create an API (``slug`` None — a new connector, switched on) or replace one. Returns
    its operator view.

    On an update a blank credential or header value keeps the saved one, so the form never has
    to be handed a secret to round-trip it. The id never changes: it names the connector's
    tools, and those names are stored in Automations. A payload ``id`` is read only on create."""
    saved = load(slug) if slug is not None else None
    name = _text(payload.get("name"), 80)
    if not name:
        raise ValidationError("Give the API a name.")
    new_slug = slug or _new_slug(payload, name)
    if _name_taken(name, besides=slug):
        raise ConflictError(f"There is already a REST API called '{name}'.")
    chosen: dict[str, str] = {}
    fields = {
        "base_url": clean_base_url(payload.get("base_url")),
        "auth": _clean_auth(payload.get("auth"), saved["auth"] if saved else None),
        "headers": _clean_headers(payload.get("headers"), saved["headers"] if saved else None),
        "endpoints": clean_endpoints(payload.get("endpoints"), chosen),
        "notes": sanitize_untrusted(str(payload.get("notes") or "").strip(), max_len=20_000),
    }
    store = RestConfigStore()
    if saved is None:
        if store.get(new_slug) is not None:
            raise ConflictError(f"There is already a REST API with the id '{new_slug}'.")
        store.create(new_slug, name, fields)
    else:
        store.update(new_slug, name, fields)
    _withdraw_stale_grants(new_slug, saved, fields)
    _apply_chosen_levels(new_slug, chosen)
    return get_operator_api(new_slug)


def _apply_chosen_levels(slug: str, chosen: dict[str, str]) -> None:
    """Record the permissions the owner picked in the form, AFTER stale grants are withdrawn:
    a level chosen in the same save that changed an endpoint is a decision about the endpoint
    as it now is. This plane is the owner's alone (no tool reaches it), exactly like the
    connector page's own permission switches, which would accept the same choice a click later."""
    connector = rest_tools.connector_id(slug)
    for tool, level in chosen.items():
        tool_permissions.set_tool_permission(connector, rest_tools.tool_name(slug, tool), level)  # type: ignore[arg-type]


def _withdraw_stale_grants(slug: str, saved: Optional[dict], fields: dict) -> None:
    """Take back an Always allow whose subject just changed. A permission row is keyed by a
    tool's NAME, and an endpoint keeps its name through an edit — so without this a grant made
    for ``GET /accounts`` would carry on, unattended, for the ``DELETE`` it was edited into.
    You are trusting the endpoint, not the label (the rule MCP's edit already follows).

      * a new API, or a new base URL: every Always allow on the connector goes — the grants
        were for somewhere else (or for an earlier API that had this id);
      * an endpoint whose method or path changed: its own Always allow goes;
      * an endpoint that was removed: its row goes whatever it held, so a later endpoint
        that is given the same id starts from the default.

    A Blocked or Needs approval row is never weakened by an edit."""
    connector = rest_tools.connector_id(slug)
    if saved is None or saved["base_url"] != fields["base_url"]:
        tool_permissions.revoke_allowed_tools(connector)
        if saved is None:
            return
    now = {e["tool"]: (e["method"], e["path"]) for e in fields["endpoints"]}
    for endpoint in saved["endpoints"]:
        tool = rest_tools.tool_name(slug, endpoint["tool"])
        current = now.get(endpoint["tool"])
        if current is None:
            tool_permissions.clear_tool_permission(connector, tool)
        elif current != (endpoint["method"], endpoint["path"]):
            tool_permissions.revoke_allowed_tool(connector, tool)
