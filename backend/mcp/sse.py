"""Streamable HTTP response body parsing — plain JSON or SSE-framed, same result either way.

Pure protocol code: no network, no store access, no I/O. A Streamable HTTP MCP server may
answer a POST with ``Content-Type: application/json`` (a bare JSON-RPC envelope) or
``text/event-stream`` (one or more ``event: message`` / ``data: {...}`` frames), per the MCP
transport spec — verified live 2026-08-14 against two public servers (``mcp.deepwiki.com``,
``mcp.context7.com``), both of which 406 a client that doesn't offer BOTH in ``Accept`` and
answer SSE-framed once it does. ``backend.mcp.client`` is the one caller.
"""

from __future__ import annotations

import json
from typing import Any

# Every HTTP request to an MCP server or its sign-in service names us. Cloudflare's "browser
# integrity" rule (error 1010) refuses urllib's default ``Python-urllib/x.y`` outright — Zerodha's
# mcp.kite.trade and Upstox's mcp.upstox.com both did, 2026-10-31 — while an honest product UA
# passes. Lives here because it is the one module both ``client`` and ``oauth`` already import.
USER_AGENT = "OpenNetWorth/1.0 (+https://github.com/open-net-worth-ai/open-net-worth)"


def parse_rpc_body(raw: bytes, content_type: str, request_id: int) -> Any:
    """Parse an ``_rpc`` response body — plain JSON or SSE-framed — into the same JSON value.

    Detected by ``Content-Type`` first; a body that fails a bare ``json.loads`` is retried as
    SSE regardless of what ``Content-Type`` claimed, since a mislabelling server should not turn
    a parseable reply into a hard failure. Raises ``ValueError``/``TypeError`` — same as
    ``json.loads`` — on a body that is neither, so the caller keeps one except clause for
    "could not parse this at all".
    """
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type.lower():
        return _select_sse_response(_parse_sse_events(text), request_id)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        events = _parse_sse_events(text)
        if not events:
            raise
        return _select_sse_response(events, request_id)


def _parse_sse_events(text: str) -> list[Any]:
    """Every ``data:`` payload of an SSE-framed body that parses as JSON, in order.

    Frames are separated by a blank line; a multi-line ``data:`` block is joined with ``\\n``
    per the SSE spec. A frame with no ``data:`` line (e.g. a bare ``: keep-alive`` comment) or
    a ``data:`` payload that isn't valid JSON is dropped rather than raised — the caller decides
    what an empty result means.
    """
    events: list[Any] = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip(" ")
            for line in frame.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            events.append(json.loads("\n".join(data_lines)))
        except (ValueError, TypeError):
            continue
    return events


def _select_sse_response(events: list[Any], request_id: int) -> Any:
    """The event answering THIS request, out of however many an SSE reply carried.

    A server may interleave progress notifications (no ``id``) with the final JSON-RPC
    response (``id`` echoing the request) on one POST's event stream; matching by ``id`` picks
    that response out from between them. Falls back to the last parseable event when nothing
    matches, so a server that omits ``id`` on its one response frame still works. Raises
    ``ValueError`` when nothing parsed at all, joining the "not parseable" contract
    ``parse_rpc_body``'s caller already expects from a bad plain-JSON body.
    """
    if not events:
        raise ValueError("no parseable SSE data frame")
    matched = [e for e in events if isinstance(e, dict) and e.get("id") == request_id]
    return matched[-1] if matched else events[-1]
