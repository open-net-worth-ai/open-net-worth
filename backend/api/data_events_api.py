"""GET /api/events — the app-wide ``data_changed`` stream (see ``core.data_events``).

One long-lived SSE per open app. Each event is ``{"type": "data_changed", "domains": [...],
"seq": n}``, the same shape a chat turn streams, so the client feeds both into one bus. A
``: ping`` comment every ``_PING_S`` keeps proxies from idling the connection out.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend.core import data_events

router = APIRouter()

_TICK_S = 1.0
_PING_S = 20.0


async def _event_stream(request: Request):
    with data_events.subscribe() as queue:
        yield "retry: 3000\n\n"
        idle = 0.0
        while not data_events.closing():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_TICK_S)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    return
                idle += _TICK_S
                if idle >= _PING_S:
                    idle = 0.0
                    yield ": ping\n\n"
                continue
            idle = 0.0
            # An event naming its own type (``publish_request``) keeps it.
            yield f"data: {json.dumps({'type': 'data_changed', **event})}\n\n"


@router.get("/api/events")
async def stream_data_events(request: Request):
    return StreamingResponse(
        _event_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
