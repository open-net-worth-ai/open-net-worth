"""GET /api/query?q=<json> — a screen's reads in one request (see services/data_query.py).

``q`` is ``{"queries": [{"refId": "...", "source": "net_worth_stats", "params": {...}}, ...]}``.
The answer is NDJSON, one line per query in request order, each sent as soon as it is computed::

    {"refId": "...", "status": 200, "data": {...}}
    {"refId": "...", "status": 409, "error": "..."}

A bad batch (unknown source, bad parameter, malformed JSON) is refused whole with a 422 before
anything runs. A GET: it only reads, and the queries are small.
"""

from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from backend.core.errors import OpenNetWorthError
from backend.services import data_query

router = APIRouter()

_MAX_Q_CHARS = 16_000


def _line(result: data_query.Result) -> bytes:
    body: dict = {"refId": result.ref_id, "status": result.status}
    if result.error is None:
        try:
            body["data"] = jsonable_encoder(result.data)
            return (json.dumps(body, allow_nan=False, separators=(",", ":")) + "\n").encode()
        except ValueError:
            body = {"refId": result.ref_id, "status": 500, "error": "Unencodable result"}
    else:
        body["error"] = result.error
    return (json.dumps(body, separators=(",", ":")) + "\n").encode()


def _stream(queries: list[data_query.Query]) -> Iterator[bytes]:
    for result in data_query.run(queries):
        yield _line(result)


@router.get("/api/query")
def get_query(q: str = Query(..., max_length=_MAX_Q_CHARS)) -> StreamingResponse:
    try:
        payload = json.loads(q)
    except ValueError as exc:
        raise HTTPException(422, "q must be JSON") from exc
    try:
        queries = data_query.parse_queries(
            payload.get("queries") if isinstance(payload, dict) else None
        )
    except OpenNetWorthError as exc:
        raise HTTPException(422, exc.message or str(exc)) from exc
    return StreamingResponse(
        _stream(queries),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )
