"""Settings > MCP for the inbound MCP OAuth server (flag ``mcp_oauth``): the public address,
connected apps, and owner-created app credentials. Normal auth (a signed-in owner). Every route
answers 404 while the flag is off; with it on, they work even while the server is unavailable,
so Settings can say why."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import NotFoundError, ValidationError
from backend.db.app_config_store import AppConfigStore
from backend.services.mcp_oauth import clients, grants, settings

router = APIRouter(prefix="/api/mcp/oauth", tags=["mcp-oauth-server"])


def _require_flag() -> None:
    if not settings.flag_on():
        raise HTTPException(status_code=404, detail="Not Found")


def _status() -> dict:
    state = settings.availability()
    return {
        "available": state.available,
        "reasons": list(state.reasons),
        "public_url": state.public_url,
        "public_url_source": state.public_url_source,
        "mcp_url": settings.resource_url() if state.public_url else "",
        "apps": grants.list_connected_apps(),
        "clients": clients.list_clients(),
    }


@router.get("")
def oauth_status():
    _require_flag()
    return _status()


class PublicUrlBody(BaseModel):
    public_url: str = ""


@router.put("/public-url")
def set_public_url(body: PublicUrlBody):
    """Store the public address. Changing it revokes every connected app: their tokens name the
    old address. Refused while ``ONW_PUBLIC_URL`` is set (the env wins)."""
    _require_flag()
    if settings.availability().public_url_source == "env":
        raise HTTPException(status_code=409, detail="ONW_PUBLIC_URL is set; change it there.")
    value = ""
    if body.public_url.strip():
        try:
            value = settings.normalize_public_url(body.public_url)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    store = AppConfigStore()
    if value != store.get_mcp_public_url():
        grants.revoke_all("public_url_changed")
        store.set_mcp_public_url(value)
    return _status()


@router.delete("/apps/{grant_id}")
def revoke_app(grant_id: str):
    _require_flag()
    try:
        grants.revoke(grant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _status()


class ClientBody(BaseModel):
    name: str
    redirect_uris: list[str]


@router.post("/clients")
def create_client(body: ClientBody):
    """Create app credentials; the secret is in this response only."""
    _require_flag()
    try:
        created = clients.create_client(body.name, body.redirect_uris)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"created": created, **_status()}


@router.delete("/clients/{client_id}")
def delete_client(client_id: str):
    _require_flag()
    try:
        clients.delete_client(client_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _status()
