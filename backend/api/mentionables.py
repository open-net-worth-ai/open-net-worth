"""GET /api/mentionables — mention-entity search endpoint.

Accepts query params, builds a SearchQuery, delegates entirely to the
services.mentions.search engine, and returns the result dict.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from fastapi import APIRouter, Query, Request

from backend.services.mentions.search import search
from backend.services.mentions.types import SearchQuery

router = APIRouter()


async def _set_when_disconnected(request: Request, gone: threading.Event) -> None:
    # Await the next ASGI message (Starlette's own StreamingResponse pattern), never
    # ``request.is_disconnected()``: its instantly-cancelled receive never sees the disconnect
    # through the BaseHTTPMiddleware in front of every route.
    while not gone.is_set():
        message = await request.receive()
        if message["type"] == "http.disconnect":
            gone.set()


@router.get("/api/mentionables")
async def get_mentionables(
    request: Request,
    q: str = Query(default=""),
    types: Optional[str] = Query(default=None),
    surface: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: Optional[str] = Query(default=None),
) -> dict:
    """Search mentionable entities.

    - ``q``: substring query (empty = all, stable order)
    - ``types``: CSV of type names to restrict (default = all)
    - ``surface``: CSV of surfaces to filter on (default = {"entity"})
    - ``limit``: page size, 1–50 (default 20)
    - ``cursor``: opaque pagination cursor from a prior response
    """
    types_set: frozenset[str] | None = None
    if types:
        types_set = frozenset(t.strip() for t in types.split(",") if t.strip())

    surfaces_set: frozenset[str] | None = frozenset({"entity"})
    if surface is not None:
        surfaces_set = frozenset(s.strip() for s in surface.split(",") if s.strip()) or None

    sq = SearchQuery(
        q=q,
        types=types_set,
        surfaces=surfaces_set,
        limit=limit,
        cursor=cursor or None,
    )
    # The picker aborts every superseded keystroke's fetch; without this the server still ran
    # each abandoned fan-out to the end, and typing "@herbal" left ~5 full searches fighting
    # over one GIL (2+ minutes each under load). The search stops at its next provider.
    gone = threading.Event()
    watcher = asyncio.create_task(_set_when_disconnected(request, gone))
    try:
        return await asyncio.to_thread(search, sq, gone.is_set)
    finally:
        gone.set()
        watcher.cancel()
