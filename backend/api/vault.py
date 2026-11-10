"""REST surface for the Vault (ADR 2026-09-26-vault-is-a-view-over-existing-stores).

Thin: validate -> call ONE service -> shape the response. The Vault is a read model over
attachments, artifacts, statements and receipts (``services/vault.py``); writes go through
``services/vault_changes.py``. Raw bytes are served by each store's existing file route —
every item carries its own ``file_url`` — so there is no second download path to secure.
"""

from __future__ import annotations

from typing import NoReturn, Optional

from fastapi import APIRouter, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict

from backend.api._deps import _bearer_token
from backend.api.upload import _MAX_UPLOAD_BYTES
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import document_preview, vault, vault_changes

router = APIRouter()


def _http(exc: OpenNetWorthError) -> NoReturn:
    raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


def _require_csrf(request: Request) -> None:
    """The import routes' guard: a Bearer token or an ``X-Requested-With`` header. A multipart
    POST is a CORS-simple request, so without this any page could plant a file in the Vault."""
    has_bearer = _bearer_token(request) is not None
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if not has_bearer and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")


@router.get("/api/vault")
def list_vault():
    """Every Vault item, newest first, plus per-kind counts."""
    try:
        items = vault.list_items()
    except OpenNetWorthError as exc:
        _http(exc)
    return {"items": items, "counts": vault.counts(items)}


@router.post("/api/vault/files")
async def add_vault_file(
    request: Request,
    file: UploadFile,
    title: Optional[str] = Form(None),
    year: Optional[str] = Form(None),
    private: bool = Form(False),
):
    """Store a new file straight into the Vault (same parser and size cap as a chat upload)."""
    _require_csrf(request)
    content = await file.read(_MAX_UPLOAD_BYTES + 1)  # never buffers past the cap
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    try:
        item = vault_changes.add_file(
            file.filename or "",
            file.content_type or "",
            content,
            title=title,
            year=year,
            private=private,
        )
    except OpenNetWorthError as exc:
        _http(exc)
    except ValueError as exc:  # the shared parser's "unsupported / unreadable file"
        raise HTTPException(415 if "Unsupported" in str(exc) else 422, str(exc)) from exc
    return {"item": item}


class VaultFilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    year: Optional[int] = None
    private: Optional[bool] = None


@router.patch("/api/vault/items/{item_id}")
def update_vault_file(item_id: str, body: VaultFilePatch):
    """Change a file's title, year or Private switch. An explicit ``null`` clears the title
    (back to the file name) or the year."""
    changes = body.model_dump(exclude_unset=True)
    if changes.get("private", False) is None:
        raise HTTPException(422, "private cannot be null")
    try:
        return {"item": vault_changes.update_file(item_id, changes)}
    except OpenNetWorthError as exc:
        _http(exc)


@router.get("/api/vault/items/{item_id}")
def get_vault_item(item_id: str):
    """One Vault item by id — what a single file preview needs (the import review shows its
    statement this way) without listing every document."""
    try:
        return {"item": vault.get_item(item_id)}
    except OpenNetWorthError as exc:
        _http(exc)


@router.get("/api/vault/items/{item_id}/table")
def vault_item_table(item_id: str, sheet: Optional[str] = None, offset: int = 0):
    """One page of a CSV or Excel item's sheet from ``offset``, as strings
    (``document_tables``)."""
    from backend.services import document_tables

    try:
        return document_tables.table(item_id, sheet, offset)
    except OpenNetWorthError as exc:
        _http(exc)


class VaultRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None


@router.put("/api/vault/items/{item_id}/name")
def rename_vault_item(item_id: str, body: VaultRename):
    """Rename any item (``vault_changes.rename_item``). An empty name puts a file back to its
    file name and a statement or receipt back to its automatic one."""
    try:
        return {"item": vault_changes.rename_item(item_id, body.title)}
    except OpenNetWorthError as exc:
        _http(exc)


class VaultNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = None


@router.put("/api/vault/items/{item_id}/note")
def set_vault_item_note(item_id: str, body: VaultNote):
    """Write the user's note on any item (``vault_changes.set_note``); empty removes it."""
    try:
        return {"item": vault_changes.set_note(item_id, body.note)}
    except OpenNetWorthError as exc:
        _http(exc)


class VaultTags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tags: list[str] = []


@router.put("/api/vault/items/{item_id}/tags")
def set_vault_item_tags(item_id: str, body: VaultTags):
    """Set the user's tags on any item (``vault_changes.set_tags``); an empty list clears
    them."""
    try:
        return {"item": vault_changes.set_tags(item_id, body.tags)}
    except OpenNetWorthError as exc:
        _http(exc)


@router.delete("/api/vault/items/{item_id}")
def remove_vault_item(item_id: str, delete_file: bool = False):
    """Take an item out of the Vault — see ``vault_changes.remove_item`` for what that
    means per kind (``action`` says whether anything was deleted). ``delete_file=true``
    deletes one of the user's files for good, even one kept from a chat."""
    try:
        if delete_file:
            return vault_changes.delete_file(item_id)
        return vault_changes.remove_item(item_id)
    except OpenNetWorthError as exc:
        _http(exc)


@router.get("/api/vault/items/{item_id}/pages/{page}")
def vault_item_page(item_id: str, page: int):
    """One page of a PDF item drawn as a PNG (1-based), with the page count in
    ``X-Page-Count`` — the panel's preview (``services/document_preview.py``)."""
    try:
        png, count = document_preview.page_image(item_id, page)
    except OpenNetWorthError as exc:
        _http(exc)
    headers = {
        "X-Page-Count": str(count),
        "X-Content-Type-Options": "nosniff",
        # An item's file never changes under its id, so neither does a page of it.
        "Cache-Control": "private, max-age=31536000, immutable",
    }
    return Response(content=png, media_type="image/png", headers=headers)


@router.get("/api/vault/items/{item_id}/thumb")
def vault_item_thumbnail(item_id: str):
    """A small JPEG of a photo item — the list's thumbnail, never the full-size original."""
    try:
        jpeg = document_preview.thumbnail(item_id)
    except OpenNetWorthError as exc:
        _http(exc)
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=31536000, immutable",
    }
    return Response(content=jpeg, media_type="image/jpeg", headers=headers)


@router.put("/api/vault/artifacts/{artifact_id}")
def save_artifact_to_vault(artifact_id: str):
    """Keep an artifact Lucius made. Idempotent."""
    try:
        return {"item": vault_changes.save_artifact(artifact_id)}
    except OpenNetWorthError as exc:
        _http(exc)


@router.delete("/api/vault/artifacts/{artifact_id}")
def remove_artifact_from_vault(artifact_id: str):
    """Take an artifact out of the Vault. It stays in the chat that made it."""
    try:
        return vault_changes.unsave_artifact(artifact_id)
    except OpenNetWorthError as exc:
        _http(exc)
