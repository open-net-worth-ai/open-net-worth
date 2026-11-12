"""
REST API for the Assets tab — owned things valued by a dated manual mark (ADR
2026-09-06-assets-tab-faithful-port). Thin routes: validate the shape, call ONE
service function, shape the response. All money math and every write lives in
``services.owned_assets``. Every refusal is ``{detail, code, field?, reason?, holder?}``.

    GET    /api/assets                          the whole page: assets + categories +
                                                  loans + cash_accounts + summary
    GET    /api/assets/{asset_id}                one projected asset
    POST   /api/assets                           create
    PATCH  /api/assets/{asset_id}                identity edit (never value/marks)
    DELETE /api/assets/{asset_id}                hard delete
    POST   /api/assets/{asset_id}/marks          record a new value (a dated valuation)
    PATCH  /api/assets/{asset_id}/marks/{mark_id} correct one valuation in place
    DELETE /api/assets/{asset_id}/marks/{mark_id} remove one valuation (never the last)
    POST   /api/assets/{asset_id}/dispose        one ledger transaction + archive
    POST   /api/assets/{asset_id}/photo          multipart `file` (replacing deletes the old file)
    DELETE /api/assets/{asset_id}/photo          no photo (its file is deleted)
    POST   /api/assets/{asset_id}/docs           multipart `file`
    PATCH  /api/assets/{asset_id}/docs/{ref_id}  patch one doc row
    DELETE /api/assets/{asset_id}/docs/{ref_id}  remove one doc row and, unless another
                                                  asset uses it, its file
    POST   /api/assets/categories                create a user category (idempotent)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.core.errors import MissingFxRateError, OpenNetWorthError, to_http_status
from backend.services import owned_assets as owned_assets_service
from backend.services.attachment_ingress import persist_inbound_file
from backend.services.attachment_parse import (
    SUPPORTED_UPLOADS,
    PasswordProtectedPdfError,
    UnsupportedFileError,
)

router = APIRouter()
_log = logging.getLogger(__name__)

# Same cap as the generic upload endpoint (backend/api/upload.py) — the photo/doc uploads
# below share its persistence path (attachment_ingress.persist_inbound_file), so they share
# its limit too.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_TOO_LARGE = "That file is over 25 MB."
_UNSUPPORTED = f"That file type can't be read. Supported: {SUPPORTED_UPLOADS}."
_PASSWORD_PROTECTED = "That file is password-protected — remove the password and upload it again."
_UNREADABLE = "That file couldn't be read — it may be damaged. Try another copy."
_NOT_AN_IMAGE = "The photo must be an image."


# The ONLY context keys an error body carries: `field` (the request key the UI shows the
# message beside), `reason` (which 409) and `holder` (the asset a loan already secures).
_ERROR_CONTEXT_KEYS = ("field", "reason", "holder")


def _refusal(exc: OpenNetWorthError) -> JSONResponse:
    """`{detail, code, field?, reason?, holder?}` — the shape of `api/fx_errors`' body, so a
    reader of a string `detail` keeps working. `detail` is the service's own sentence."""
    context = {key: exc.context[key] for key in _ERROR_CONTEXT_KEYS if key in exc.context}
    return JSONResponse(
        status_code=to_http_status(exc),
        content={"detail": exc.message or str(exc), "code": exc.code, **context},
    )


def _apply(change: dict) -> dict | JSONResponse:
    """Every write route funnels through here: one error-mapping choke instead of a
    repeated try/except per route. An unavailable FX rate is re-raised for the app-level
    handler, which already answers it."""
    try:
        return owned_assets_service.apply_owned_asset_change(change)
    except MissingFxRateError:
        raise
    except OpenNetWorthError as exc:
        return _refusal(exc)


def _missing_asset(asset_id: str) -> JSONResponse | None:
    """L1: 404 on a missing asset BEFORE the upload routes touch attachment storage —
    called before ``persist_inbound_file`` so a bad asset_id never creates an orphaned
    attachment row with no asset to reference it."""
    try:
        owned_assets_service.get_asset(asset_id)
    except OpenNetWorthError as exc:
        return _refusal(exc)
    return None


# ── Bodies ───────────────────────────────────────────────────────────────────────────────


def _validate_facts(value: list[list[str]]) -> list[list[str]]:
    """Shared by every body that carries `facts` — H1/M3 bounds mirrored from the
    service choke (``services.owned_assets_rules.bounded_facts``) so a malformed request is
    rejected with 422 before it ever reaches the store, not just at the choke."""
    if len(value) > owned_assets_service.MAX_FACTS:
        raise ValueError(f"at most {owned_assets_service.MAX_FACTS} facts are allowed")
    for pair in value:
        if len(pair) != 2:
            raise ValueError("each fact must be a [label, value] pair")
        if any(len(part) > owned_assets_service.MAX_FACT_FIELD for part in pair):
            raise ValueError(
                f"fact label/value must be at most {owned_assets_service.MAX_FACT_FIELD} characters"
            )
    return value


class AssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=owned_assets_service.MAX_NAME)
    category: str
    value: float = Field(allow_inf_nan=False)
    description: str = Field(default="", max_length=owned_assets_service.MAX_DESCRIPTION)
    facts: list[list[str]] = []
    notes: str = Field(default="", max_length=owned_assets_service.MAX_NOTES)
    cost: Optional[float] = Field(default=None, allow_inf_nan=False)
    cost_date: Optional[str] = None
    share_pct: float = Field(default=100, allow_inf_nan=False)
    share_note: str = Field(default="", max_length=owned_assets_service.MAX_SHARE_NOTE)
    loan_account_id: Optional[str] = None
    insured: bool = False
    maturity_date: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=owned_assets_service.MAX_MARK_NOTE)

    _check_facts = field_validator("facts")(_validate_facts)


class AssetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict


class MarkCreate(BaseModel):
    value: float = Field(allow_inf_nan=False)
    note: Optional[str] = Field(default=None, max_length=owned_assets_service.MAX_MARK_NOTE)
    date: Optional[str] = None


class MarkPatch(BaseModel):
    """A correction: at least one of value/date/note. `kind` (or any other key) is refused —
    the purchase point is corrected through the asset's cost/cost_date, never here."""

    model_config = ConfigDict(extra="forbid")

    value: Optional[float] = Field(default=None, allow_inf_nan=False)
    date: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=owned_assets_service.MAX_MARK_NOTE)

    @model_validator(mode="after")
    def _changes_something(self) -> "MarkPatch":
        if self.value is None and self.date is None and self.note is None:
            raise ValueError("a correction changes at least one of value, date or note")
        return self


class DisposeBody(BaseModel):
    proceeds: float = Field(allow_inf_nan=False)
    account_id: str
    date: Optional[str] = None


class CategoryCreate(BaseModel):
    label: str = Field(max_length=owned_assets_service.MAX_CATEGORY_LABEL)


class DocPatch(BaseModel):
    changes: dict


# ── Literal routes FIRST (categories sits at the same path depth as {asset_id}) ────────────


@router.post("/api/assets/categories")
def create_category_route(body: CategoryCreate):
    try:
        return owned_assets_service.create_category(body.label)
    except OpenNetWorthError as exc:
        return _refusal(exc)


# ── Collection ───────────────────────────────────────────────────────────────────────────


@router.get("/api/assets")
def get_assets_route(include_archived: bool = False):
    return owned_assets_service.get_assets_page(include_archived=include_archived)


@router.post("/api/assets")
def create_asset_route(body: AssetCreate):
    return _apply({"op": "create", **body.model_dump()})


# ── One asset ────────────────────────────────────────────────────────────────────────────


@router.get("/api/assets/{asset_id}")
def get_asset_route(asset_id: str):
    try:
        return owned_assets_service.get_asset(asset_id)
    except OpenNetWorthError as exc:
        return _refusal(exc)


@router.patch("/api/assets/{asset_id}")
def update_asset_route(asset_id: str, body: AssetUpdate):
    return _apply({"op": "update", "id": asset_id, "changes": body.changes})


@router.delete("/api/assets/{asset_id}")
def delete_asset_route(asset_id: str):
    return _apply({"op": "delete", "id": asset_id})


@router.post("/api/assets/{asset_id}/marks")
def add_mark_route(asset_id: str, body: MarkCreate):
    return _apply({"op": "add_mark", "id": asset_id, **body.model_dump()})


@router.patch("/api/assets/{asset_id}/marks/{mark_id}")
def correct_mark_route(asset_id: str, mark_id: str, body: MarkPatch):
    return _apply(
        {
            "op": "correct_mark",
            "id": asset_id,
            "mark_id": mark_id,
            **body.model_dump(exclude_none=True),
        }
    )


@router.delete("/api/assets/{asset_id}/marks/{mark_id}")
def remove_mark_route(asset_id: str, mark_id: str):
    return _apply({"op": "remove_mark", "id": asset_id, "mark_id": mark_id})


@router.post("/api/assets/{asset_id}/dispose")
def dispose_asset_route(asset_id: str, body: DisposeBody):
    return _apply({"op": "dispose", "id": asset_id, **body.model_dump()})


def _upload_refusal(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"detail": detail, "code": code, "field": "file"}
    )


async def _read_upload(file: UploadFile) -> bytes | JSONResponse:
    # L1: check the DECLARED size (Starlette sets ``UploadFile.size`` as it buffers the
    # multipart body) before pulling the whole thing into memory with `.read()` — a request
    # that already announces itself as oversized skips that read entirely. `size` can be
    # `None` (a stream with no declared length); the after-read check covers that case.
    if file.size is not None and file.size > _MAX_UPLOAD_BYTES:
        return _upload_refusal(413, "too_large", _TOO_LARGE)
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        return _upload_refusal(413, "too_large", _TOO_LARGE)
    return content


def _persist(file: UploadFile, content: bytes, channel: str, unsupported: str):
    """The saved attachment's ingress result, or the readable refusal — never the parser's own
    text: a password-protected Office file raised a `password_required: …` sentinel, and an
    unreadable one the reader's message (openpyxl, xlrd, PyMuPDF)."""
    try:
        return persist_inbound_file(
            file.filename or "", file.content_type or "", content, source_channel=channel
        )
    except ValueError as exc:
        message = str(exc)
        if isinstance(exc, PasswordProtectedPdfError) or message.startswith("password_required:"):
            return _upload_refusal(422, "password_protected", _PASSWORD_PROTECTED)
        if isinstance(exc, UnsupportedFileError):
            return _upload_refusal(415, "unsupported_type", unsupported)
        _log.info("asset upload %r unreadable: %s", file.filename, message)
        return _upload_refusal(422, "validation", _UNREADABLE)


def _apply_uploaded(ref_id: str, change: dict):
    """The choke for an attachment THIS request just saved: when the asset write is refused
    (the asset deleted in between, say) or fails, the file is discarded — unless the write had
    committed and an asset references it (the service decides)."""
    try:
        written = _apply(change)
    except Exception:
        owned_assets_service.discard_upload(ref_id)
        raise
    if isinstance(written, JSONResponse):
        owned_assets_service.discard_upload(ref_id)
    return written


@router.post("/api/assets/{asset_id}/photo")
async def upload_photo_route(asset_id: str, file: UploadFile):
    if missing := _missing_asset(asset_id):
        return missing
    content = await _read_upload(file)
    if isinstance(content, JSONResponse):
        return content
    saved = _persist(file, content, "assets:photo", _NOT_AN_IMAGE)
    if isinstance(saved, JSONResponse):
        return saved
    if saved["kind"] != "image":
        # L2: a photo slot must hold a viewable image — anything else (a PDF, a CSV) is
        # refused, and the attachment just persisted is cleaned up so rejecting it
        # doesn't leave an orphan row behind either.
        owned_assets_service.discard_upload(saved["attachment_id"])
        return _upload_refusal(415, "unsupported_type", _NOT_AN_IMAGE)
    ref = saved["attachment_id"]
    return _apply_uploaded(ref, {"op": "set_photo", "id": asset_id, "ref": ref})


@router.delete("/api/assets/{asset_id}/photo")
def remove_photo_route(asset_id: str):
    if missing := _missing_asset(asset_id):
        return missing
    return _apply({"op": "set_photo", "id": asset_id, "ref": None})


@router.post("/api/assets/{asset_id}/docs")
async def upload_doc_route(asset_id: str, file: UploadFile):
    if missing := _missing_asset(asset_id):
        return missing
    content = await _read_upload(file)
    if isinstance(content, JSONResponse):
        return content
    saved = _persist(file, content, "assets:docs", _UNSUPPORTED)
    if isinstance(saved, JSONResponse):
        return saved
    ref = saved["attachment_id"]
    return _apply_uploaded(
        ref, {"op": "add_doc", "id": asset_id, "ref_id": ref, "name": file.filename or ""}
    )


@router.patch("/api/assets/{asset_id}/docs/{ref_id}")
def update_doc_route(asset_id: str, ref_id: str, body: DocPatch):
    return _apply({"op": "update_doc", "id": asset_id, "ref_id": ref_id, "changes": body.changes})


@router.delete("/api/assets/{asset_id}/docs/{ref_id}")
def remove_doc_route(asset_id: str, ref_id: str):
    return _apply({"op": "remove_doc", "id": asset_id, "ref_id": ref_id})
