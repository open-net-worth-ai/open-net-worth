"""REST connector settings — the owner's APIs, each its own connector (``rest:<slug>``).

Thin router over ``services.rest_api``:
  * ``GET  /api/rest-connector/apis/{slug}`` — one API for its edit form (never a credential value).
  * ``POST /api/rest-connector/apis``        — add one: a new connector, switched on.
  * ``PUT  /api/rest-connector/apis/{slug}`` — replace one; a blank credential keeps the saved one.
  * ``POST   /api/rest-connector/openapi``     — read an OpenAPI / Swagger document (pasted, or
    downloaded from an address) into a DRAFT that pre-fills the form. Saves nothing.

Pause, Resume and Remove are the connector's own (``/api/connectors/{id}``), like any other.
Auth is the app-wide ``require_auth`` dependency. Calling an API is not a route: it happens
only inside a Lucius turn (the API's own ``rest__<slug>__…`` tools), under the tool's
permission.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services.rest_api import apis as rest_apis
from backend.services.rest_api import openapi

router = APIRouter()


class RestAuth(BaseModel):
    type: str = "none"
    # Header or query-parameter name, for the two API-key types.
    name: Optional[str] = None
    username: Optional[str] = None
    # The key, token or password. Blank on an update keeps the saved one.
    value: Optional[str] = None


class RestApiBody(BaseModel):
    # A new API's id, when the owner typed one instead of taking the name's. Ignored on an edit:
    # the id is the route's.
    id: Optional[str] = None
    name: str
    base_url: str
    auth: RestAuth = Field(default_factory=RestAuth)
    # Fixed headers sent with every request. Omitted keeps the saved set; a blank value keeps
    # the saved value of that name.
    headers: Optional[dict[str, str]] = None
    endpoints: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""


class OpenApiBody(BaseModel):
    url: Optional[str] = None
    text: Optional[str] = None


def _run(call, *args):
    try:
        return call(*args)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


def _sync_tools() -> None:
    """A new or renamed API's tools exist from this moment, not from the next turn — the
    connector's Tool permissions list them straight away."""
    from backend.ai.tools.rest_api import sync_quietly

    sync_quietly()


@router.get("/api/rest-connector/apis/{slug}")
def get_rest_api(slug: str):
    return _run(rest_apis.get_operator_api, slug)


@router.post("/api/rest-connector/apis")
def create_rest_api(body: RestApiBody):
    saved = _run(rest_apis.save_api, body.model_dump())
    _sync_tools()
    return saved


@router.put("/api/rest-connector/apis/{slug}")
def update_rest_api(slug: str, body: RestApiBody):
    saved = _run(rest_apis.save_api, body.model_dump(), slug)
    _sync_tools()
    return saved


@router.post("/api/rest-connector/openapi")
def read_openapi_document(body: OpenApiBody):
    return _run(lambda: openapi.draft(url=body.url, text=body.text))
