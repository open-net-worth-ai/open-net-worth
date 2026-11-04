"""A scoped MCP endpoint for external trading participants, without operator credentials."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.core.errors import OpenNetWorthError
from backend.core.ratelimit import SlidingWindowLimiter
from backend.core.trading_runtime import TRADING_TOOLS, bind_trading_context
from backend.services.trading_access import authenticate_access, token_digest

router = APIRouter()
MAX_BODY = 131072
request_limiter = SlidingWindowLimiter(max_attempts=120, window_seconds=60)


def _rpc_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@router.post("/api/trading/gateway/{run_id}")
async def trading_gateway(run_id: str, request: Request):
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not authenticate_access(run_id, token):
        return JSONResponse(
            {"detail": "Valid scoped trading access required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )
    key = f"{run_id}:{token_digest(token)}"
    allowed, retry_after = request_limiter.check(key)
    if not allowed:
        return JSONResponse(
            {"detail": "Trading request limit exceeded"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    request_limiter.record(key)
    body = await _bounded_body(request)
    if body is None:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    try:
        rpc = json.loads(body)
    except ValueError:
        return _rpc_error(None, -32700, "Invalid JSON")
    if not isinstance(rpc, dict) or rpc.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Invalid JSON-RPC request")
    request_id, method = rpc.get("id"), rpc.get("method")
    if method == "notifications/initialized":
        return JSONResponse(None, status_code=202)
    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "open-net-worth-trading", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": tool["input_schema"],
                }
                for tool in TOOL_SCHEMAS
                if tool["name"] in TRADING_TOOLS
            ]
        }
    elif method == "tools/call":
        params = rpc.get("params") or {}
        if not isinstance(params, dict) or params.get("name") not in TRADING_TOOLS:
            return _rpc_error(request_id, -32602, "Only scoped trading tools are available")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return _rpc_error(request_id, -32602, "Tool arguments must be an object")
        result = await _call(run_id, token, params["name"], arguments)
    else:
        return _rpc_error(request_id, -32601, "Method not found")
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        headers={"Cache-Control": "no-store"},
    )


async def _bounded_body(request: Request) -> bytes | None:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) < 0 or int(length) > MAX_BODY:
                return None
        except ValueError:
            return None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            return None
        body.extend(chunk)
    return bytes(body)


async def _call(run_id: str, token: str, name: str, arguments: dict) -> dict:
    try:
        with bind_trading_context(run_id, "external", token_digest(token)):
            text, proposed = await execute_tool(name, arguments)
        if proposed is not None:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "Owner approval required"}],
            }
        parsed = json.loads(text)
        return {
            "isError": bool(isinstance(parsed, dict) and parsed.get("error")),
            "content": [{"type": "text", "text": text}],
        }
    except OpenNetWorthError as exc:
        return {"isError": True, "content": [{"type": "text", "text": exc.message}]}
