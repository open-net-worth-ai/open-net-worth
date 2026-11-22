"""Hand-rolled JSON-RPC 2.0 over HTTP MCP endpoint (RFC P3-MCP §2.1, S1).

A thin ``POST /mcp`` route — NO ``mcp`` SDK, zero new dependency (the repo leans
stdlib/zero-dep). It implements the minimal MCP method set:

  * ``initialize``      — handshake + server capabilities (tools + resources).
  * ``tools/list``      — the registry's tool descriptors + presentation annotations.
  * ``tools/call``      — dispatch via ``adapter.call_tool`` → MCP text content.
  * ``resources/list``  — the curated read-only resource catalog.
  * ``resources/read``  — fetch one catalog resource via the same read path → JSON contents.

Authorization: there is none beyond the Bearer gate below — a caller holding the instance
API token IS the operator, and a ``tools/call`` dispatches its tool. The former tiering
(allowlist / grant envelope / propose-instead-of-write fallback) has been removed;
``adapter.call_tool`` still checkpoints + audits a mutating call so it stays undoable and
visible.

JSON-RPC errors are surfaced with the standard codes (parse error -32700,
invalid request -32600, method not found -32601, invalid params -32602, internal
-32603). Notifications (a request with no ``id``) get an empty 202-style body.

Auth (RFC P3-MCP §5): ``/mcp`` is mounted as an ``APIRouter`` on the main app, so
the app-wide ``require_auth`` dependency DOES run for it (unlike ``app.mount``
sub-apps). But MCP must be gated by the **Bearer token specifically** —
cookies / loopback-trust must NOT grant MCP access. So this endpoint enforces the
Bearer explicitly via ``_require_mcp_bearer`` regardless of the app gate: a valid
``Authorization: Bearer <token>`` (verified against ``AuthStore().api_token()``)
is required, with the ``AUTH_DISABLED=true`` dev hatch honored for parity. The
two checks compose: the app gate is the outer ring (any valid credential), the
Bearer check is the inner ring MCP-specific requirement.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from backend.core.config import get_settings
from backend.core.security import verify_api_token
from backend.db.auth_store import AuthStore
from backend.mcp.adapter import (
    ALL_SCOPES,
    McpResourceNotFound,
    call_tool,
    mcp_resource_descriptors,
    mcp_tool_descriptors,
    read_resource,
)

# The inbound MCP caller identity stamped on checkpoints / audit rows / transaction
# provenance: the operator's own API-token client. Every /mcp call is this surface.
MCP_SURFACE = "api_token"

# MCP protocol revisions this server speaks. Tool ANNOTATIONS
# (readOnlyHint/destructiveHint/idempotentHint — what lets a client group our tools
# Read-only vs Write/delete) were introduced in 2025-03-26, so advertising the old
# 2024-11-05 made clients (Claude Desktop) ignore them and lump everything under
# "other tools". We now negotiate: echo the client's requested version when it is one
# we recognize, else fall back to our latest. All our methods (initialize/tools/{list,
# call}/resources/{list,read}) are stable across these revisions; annotations are
# additive/optional, so echoing a newer version is safe.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]  # our latest / default
SERVER_INFO = {"name": "open-net-worth", "version": "1"}


def _negotiate_protocol_version(requested: Any) -> str:
    """Per the MCP spec: respond with the client's requested version if we support it,
    else our latest. Keeps annotation-aware clients on a revision that renders them."""
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return PROTOCOL_VERSION


# JSON-RPC 2.0 standard error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

router = APIRouter(tags=["mcp"])


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


@dataclass(frozen=True)
class McpCaller:
    """Who is calling ``/mcp``: the surface stamped on checkpoints/audit rows, and its scopes."""

    surface: str
    scopes: tuple[str, ...]


_OPERATOR = McpCaller(MCP_SURFACE, ALL_SCOPES)


def _oauth_caller(presented: str) -> Optional[McpCaller]:
    """A live OAuth access token (flag ``mcp_oauth``, and only while the server is available)."""
    from backend.services.mcp_oauth import grants
    from backend.services.mcp_oauth import settings as oauth_settings

    if not oauth_settings.is_available():
        return None
    found = grants.caller_for_access_token(presented, oauth_settings.resource_url())
    if found is None:
        return None
    return McpCaller(f"oauth:{found.grant_id}", found.scopes)


def _challenge() -> str:
    """The 401 challenge. While the OAuth server is available it points hosted apps at the
    protected-resource metadata (RFC 9728); otherwise it is exactly what it always was."""
    from backend.services.mcp_oauth import metadata
    from backend.services.mcp_oauth import settings as oauth_settings

    try:
        if oauth_settings.is_available():
            return metadata.www_authenticate()
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the old challenge
        pass
    return "Bearer"


def _require_mcp_bearer(request: Request) -> tuple[Optional[McpCaller], Optional[JSONResponse]]:
    """Enforce a valid Bearer token for MCP → ``(caller, None)`` or ``(None, 401 response)``.

    MCP-specific gate: cookies / loopback-trust never grant MCP — only the API token does
    (RFC §5), or, with the ``mcp_oauth`` server available, a live OAuth access token for this
    resource. The ``AUTH_DISABLED`` dev hatch is honored for parity.
    """
    settings = get_settings()
    if not settings.auth_enabled:  # AUTH_DISABLED=true → dev escape hatch (parity)
        return _OPERATOR, None
    presented = _bearer_token(request)
    if presented is not None:
        if verify_api_token(presented, AuthStore().api_token()):
            return _OPERATOR, None
        caller = _oauth_caller(presented)
        if caller is not None:
            return caller, None
    return None, JSONResponse(
        status_code=401,
        content={"detail": "authentication required"},
        headers={"WWW-Authenticate": _challenge()},
    )


def _rpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _rpc_result(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _is_error_result(text: str) -> bool:
    """Whether a tool's result string reports its own failure (``ok:false`` / an ``error``
    key) — including the registry's ``{"error": "unknown tool: …"}`` sentinel."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and (parsed.get("ok") is False or bool(parsed.get("error")))


def _call_result(text: str, proposed: Optional[dict]) -> dict:
    """Shape a ``tools/call`` result.

    Most tools (``proposed is None``) → ``{content:[text], isError}``, where ``isError``
    reflects the tool's OWN outcome (a tool that failed, or an unknown name, is a completed
    call surfaced honestly — not a transport error).

    A ``propose_*`` tool returns a non-None proposed-change dict having WRITTEN NOTHING. We
    surface the diff two ways so any MCP client sees it:
      * ``content`` gets an extra ``text`` block (the JSON diff), so a plain text-only client
        renders it; and
      * a top-level ``proposedChange`` field carries the structured dict for a client that
        wants to act on it — by calling the matching ``apply_*`` tool.
    """
    is_error = _is_error_result(text)
    if proposed is None:
        return {"content": [{"type": "text", "text": text}], "isError": is_error}
    diff_text = json.dumps(proposed, ensure_ascii=False)
    return {
        "content": [
            {"type": "text", "text": text},
            {
                "type": "text",
                "text": (
                    "PROPOSED change — nothing was written. Call the matching apply_* "
                    f"tool to commit it:\n{diff_text}"
                ),
            },
        ],
        "proposedChange": proposed,
        "isError": is_error,
    }


async def _handle_tools_call(
    name: str, arguments: dict, req_id: Any, caller: McpCaller = _OPERATOR
) -> dict:
    """Dispatch a ``tools/call`` to its tool.

    A Bearer-authenticated caller is the operator, so there is no second permission layer
    behind the auth gate — the tool runs. ``call_tool`` wraps a mutating call in a checkpoint
    (undoable) and audits it; a ``propose_*`` tool's diff is surfaced to the client.
    """
    from backend.mcp import apps

    if name == apps.ANSWER_TOOL:
        from backend.core.audit_context import clear_audit_scope, set_audit_scope

        token = set_audit_scope("ai", f"mcp:{caller.surface}", note=f"mcp tool:{name}")
        try:
            return _rpc_result(req_id, await apps.answer(arguments, caller.surface, caller.scopes))
        finally:
            clear_audit_scope(token)
    text, proposed = await call_tool(name, arguments, surface=caller.surface, scopes=caller.scopes)
    if name in apps.UI_TOOLS and proposed is None:
        return _rpc_result(req_id, apps.shape(text, caller.surface))
    return _rpc_result(req_id, _call_result(text, proposed))


async def _handle_method(
    method: str, params: dict, req_id: Any, caller: McpCaller = _OPERATOR
) -> dict:
    """Route one well-formed JSON-RPC request to its MCP handler → an RPC envelope."""
    if method == "initialize":
        from backend.mcp import apps

        apps.note_initialize(caller.surface, params)
        return _rpc_result(
            req_id,
            {
                "protocolVersion": _negotiate_protocol_version(params.get("protocolVersion")),
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": SERVER_INFO,
            },
        )

    if method == "ping":
        # Standard MCP utility method — MUST return an empty result, not an error.
        # Clients ping periodically to keep the connection healthy.
        return _rpc_result(req_id, {})

    if method == "tools/list":
        from backend.mcp import apps

        tools = apps.decorate(mcp_tool_descriptors(caller.scopes), caller.scopes, caller.surface)
        return _rpc_result(req_id, {"tools": tools})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _rpc_error(req_id, INVALID_PARAMS, "missing tool name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(req_id, INVALID_PARAMS, "arguments must be an object")
        return await _handle_tools_call(name, arguments, req_id, caller)

    if method == "resources/list":
        from backend.mcp import apps

        resources = [
            *mcp_resource_descriptors(scopes=caller.scopes),
            *apps.resource_descriptors(caller.scopes, caller.surface),
        ]
        return _rpc_result(req_id, {"resources": resources})

    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            return _rpc_error(req_id, INVALID_PARAMS, "missing resource uri")
        try:
            from backend.mcp import apps

            ui = apps.read_ui(caller.scopes, caller.surface) if uri == apps.UI_URI else None
            contents = ui if ui is not None else await read_resource(uri, scopes=caller.scopes)
        except McpResourceNotFound as exc:
            # An unlisted uri — refuse as a proper JSON-RPC error (never a 500 / leak).
            return _rpc_error(req_id, INVALID_PARAMS, str(exc))
        return _rpc_result(req_id, {"contents": contents})

    return _rpc_error(req_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _transport_error(code: int, message: str) -> JSONResponse:
    """A malformed/no-id error returned as HTTP 400 — NOT a 200 JSON-RPC body.

    A JSON-RPC parse/invalid-request error legitimately has ``id: null``, but the MCP
    SDK's message schema requires ``id`` to be a string|number and REJECTS ``id: null``
    when it tries to parse a 2xx body as a message (the "invalid_union" zod error clients
    reported). Returning these at HTTP 400 makes the transport treat it as a transport-level
    error (logged) instead of feeding the body to the message parser — no client crash. The
    body is kept JSON-RPC-shaped for human/debug readability; the status is what matters."""
    return JSONResponse(content=_rpc_error(None, code, message), status_code=400)


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    """The single MCP JSON-RPC entrypoint (Streamable HTTP, request/response JSON)."""
    caller, denied = _require_mcp_bearer(request)
    if denied is not None or caller is None:
        return denied

    try:
        payload = await request.json()
    except Exception:
        return _transport_error(PARSE_ERROR, "parse error")

    # JSON-RPC batching (a JSON array) was REMOVED in MCP 2025-06-18; we only accept a
    # single request object. A non-object body is a transport-level bad request (400, not a
    # 200 id:null body the client would choke on).
    if not isinstance(payload, dict):
        return _transport_error(INVALID_REQUEST, "batch/array requests are not supported")

    req_id = payload.get("id")
    method = payload.get("method")
    # A JSON-RPC notification has no ``id`` and MUST receive no response body — just an
    # HTTP 202 Accepted (the Streamable HTTP contract). A 204 + ``null`` body is malformed
    # (204 = No Content) and some clients parse the ``null`` as a bad message.
    is_notification = "id" not in payload

    if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
        if is_notification:
            return Response(status_code=202)
        # A request with an id but a bad envelope: a 200 JSON-RPC error is fine (id is valid).
        if req_id is not None:
            return JSONResponse(content=_rpc_error(req_id, INVALID_REQUEST, "invalid request"))
        return _transport_error(INVALID_REQUEST, "invalid request")

    params = payload.get("params") or {}
    if not isinstance(params, dict):
        if is_notification:
            return Response(status_code=202)
        return JSONResponse(content=_rpc_error(req_id, INVALID_PARAMS, "params must be an object"))

    if is_notification:
        # Dispatch best-effort (e.g. notifications/initialized is a no-op for us), reply 202.
        try:
            await _handle_method(method, params, req_id, caller)
        except Exception:  # noqa: BLE001 — a notification never gets an error body
            pass
        return Response(status_code=202)

    token = _progress_token(method, params)
    if token is not None and "text/event-stream" in request.headers.get("accept", ""):
        return _streamed(method, params, req_id, caller, token)
    try:
        envelope = await _handle_method(method, params, req_id, caller)
    except Exception as exc:  # noqa: BLE001 — never leak a 500 to an MCP client
        return JSONResponse(content=_rpc_error(req_id, INTERNAL_ERROR, str(exc)))
    return JSONResponse(content=envelope)


def _progress_token(method: str, params: dict) -> Any:
    """A ``tools/call`` whose client asked for progress (``params._meta.progressToken``)."""
    if method != "tools/call":
        return None
    meta = params.get("_meta")
    return meta.get("progressToken") if isinstance(meta, dict) else None


def _sse(message: dict) -> str:
    return f"event: message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"


def _streamed(method: str, params: dict, req_id: Any, caller: McpCaller, token: Any):
    """Answer a long ``tools/call`` as an SSE stream (Streamable HTTP): MCP
    ``notifications/progress`` while the tool reports (``core.mcp_progress``), then the result."""
    from backend.core import mcp_progress

    queue: asyncio.Queue = asyncio.Queue()
    count = 0

    async def report(message: str) -> None:
        nonlocal count
        count += 1
        params_ = {"progressToken": token, "progress": count, "message": message}
        await queue.put({"jsonrpc": "2.0", "method": "notifications/progress", "params": params_})

    async def run() -> None:
        bound = mcp_progress.bind(report)
        try:
            envelope = await _handle_method(method, params, req_id, caller)
        except Exception as exc:  # noqa: BLE001 — never leak a 500 to an MCP client
            envelope = _rpc_error(req_id, INTERNAL_ERROR, str(exc))
        finally:
            mcp_progress.reset(bound)
        await queue.put(envelope)

    async def frames():
        task = asyncio.create_task(run())
        try:
            while True:
                message = await queue.get()
                yield _sse(message)
                if "method" not in message:
                    return
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        frames(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )


def _oauth_gate(request: Request) -> Optional[JSONResponse]:
    """GET/DELETE /mcp skip the app gate while the OAuth server is available (``_deps``), so they
    answer only an authenticated caller then. Otherwise the app gate already ran, as before."""
    from backend.services.mcp_oauth.settings import is_available

    if not is_available():
        return None
    return _require_mcp_bearer(request)[1]


@router.api_route("/mcp", methods=["GET", "HEAD"])
async def mcp_get_stream(request: Request) -> Response:
    """The optional server→client SSE stream (Streamable HTTP). We don't offer one, so per
    spec return 405 Method Not Allowed (clients fall back to request/response cleanly). An
    explicit handler is needed so GET /mcp doesn't fall through to the SPA static mount (404).
    HEAD is listed too: a FastAPI GET route doesn't answer it, so ``curl -I /mcp`` fell through
    to the SPA and got its HTML (200) instead of the 401 challenge a setup check looks for."""
    denied = _oauth_gate(request)
    if denied is not None:
        return denied
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
async def mcp_delete_session(request: Request) -> Response:
    """Session termination (Streamable HTTP). We hold no per-session state, so accept it as a
    no-op (200) rather than 404 via the static mount."""
    denied = _oauth_gate(request)
    if denied is not None:
        return denied
    return Response(status_code=200)
