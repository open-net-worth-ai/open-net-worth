"""File/image upload endpoint — persists parsed content for the AI to reference.

Attachments live in the persistent store (services/attachments.py → the `attachments`
table + ATTACHMENTS_DIR) until the user deletes them — Settings → Uploaded files, or the
list/delete routes below. The old 30-minute in-memory cache is gone.

Persistence goes through ``services.attachment_ingress.persist_inbound_file`` — the same
shared operation Telegram photo/document ingress calls (cross-channel-attachments RFC item
11) — rather than hand-rolling parse + StatementStore + store here. REST paths are
UNCHANGED (still ``/api/upload(s)``) even though the underlying store is now
``AttachmentStore``: renaming the wire contract is a separate, frontend-touching decision
out of this phase's scope.
"""

from __future__ import annotations

import asyncio
import base64
import logging

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response

from backend.api._blob_response import inline_blob_headers

logger = logging.getLogger(__name__)
router = APIRouter()

# Same cap as the statement-import upload (backend/api/import_api.py) — this endpoint had no
# limit at all before, unlike every other upload surface in the app, and is now also the
# entry point for the transaction-receipts attach flow (a photographed receipt).
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB

from backend.services.attachment_ingress import persist_inbound_file  # noqa: E402

# Re-exported for callers that historically reached these via api.upload
# (connectors/processor.py and friends), and for backend.services.attachment_parse's
# parse_upload — kept importable from here too since several call sites still say
# `from backend.api.upload import parse_upload`.
from backend.services.attachment_parse import (
    UnsupportedFileError,  # noqa: E402
    parse_upload,  # noqa: E402, F401
)
from backend.services.attachments import (  # noqa: E402, F401
    get_attachment as get_upload,
)
from backend.services.attachments import (  # noqa: E402, F401
    get_attachment_import_source as get_upload_import_source,
)


def store_upload(entry: dict, filename: str = "") -> str:
    """Persist an already-parsed entry directly — no channel provenance, no StatementStore
    side effect. Low-level: real ingress goes through
    ``services.attachment_ingress.persist_inbound_file`` instead, kept here only because
    several tests already have a parsed entry in hand (no separate raw bytes) and want to
    skip re-parsing it. Raw bytes for the attachment's real file are derived from the
    entry itself (``AttachmentStore.create``'s own ``content=None`` fallback) — empty bytes
    only for the one legacy shape (a plain ``.xls`` entry) that never carried any."""
    from backend.services.attachments import create_attachment

    return create_attachment(entry, filename=filename)


@router.post("/api/upload")
async def upload_file(file: UploadFile):
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    mime = file.content_type or ""
    filename = file.filename or ""
    try:
        # Parsing (PDF rasterizing, spreadsheet decode) is CPU/memory heavy: off the loop.
        result = await asyncio.to_thread(
            persist_inbound_file,
            filename,
            mime,
            content,
            source_channel="chat:web",
        )
    except ValueError as exc:
        raise HTTPException(415 if isinstance(exc, UnsupportedFileError) else 422, str(exc))

    return {
        "ref_id": result["attachment_id"],
        "kind": result["kind"],
        "summary": result["summary"],
    }


@router.get("/api/uploads/{ref_id}/image")
def get_upload_image_route(ref_id: str):
    """Raw bytes for an image attachment — the chat bubble's thumbnail/tap-to-open source.
    404 for an unknown ref_id, a deleted attachment, or a non-image attachment (never
    serves a statement's parsed text/bytes through this route).

    ``entry["media_type"]`` is the ORIGINAL upload's declared Content-Type, never sniffed —
    ``kind == "image"`` only means it matched ``mime.startswith("image/")`` at parse time,
    which "image/svg+xml" also satisfies (an SVG can carry <script>). Same allowlist gate as
    ``GET /api/receipts/{id}/file`` — security review found this route had the identical gap,
    one bug class fixed in one shared place."""
    entry = get_upload(ref_id)
    if not entry or entry.get("kind") != "image" or not entry.get("data"):
        raise HTTPException(404, "No such image")
    content = base64.standard_b64decode(entry["data"])
    effective_type, extra_headers = inline_blob_headers(entry.get("media_type"), "image")
    # A ref_id's bytes never change once stored, so let the browser keep them: without this,
    # every conversation open re-downloads every full-size image in the thread
    # (docs/PERFORMANCE.md flagged exactly this route before the thumbnail UI shipped).
    extra_headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return Response(content=content, media_type=effective_type, headers=extra_headers)


@router.get("/api/uploads/{ref_id}/file")
def get_upload_file_route(ref_id: str):
    """Raw bytes for ANY stored attachment — not just images (see ``.../image`` above,
    which stays image-only and cache-forever for the chat-bubble thumbnail). This is the
    route that closes the real UX gap: before it existed, a non-image chat attachment (a
    PDF, a spreadsheet, a bank-statement text file the user sent Lucius) could never be
    downloaded back — only an image had its bytes reachable in a servable shape. 404 for an
    unknown ref_id, a deleted attachment, or one whose raw file is missing on disk.

    Same allowlist gate as ``GET /api/receipts/{id}/file`` / ``GET /api/uploads/{ref_id}/image``
    (``backend.api._blob_response.inline_blob_headers``) — the stored ``media_type`` is
    whatever the ORIGINAL upload declared, never sniffed, so a spoofed or genuinely
    scriptable type (``text/html``, ``image/svg+xml``) is forced to download as
    ``application/octet-stream`` instead of rendering inline on this origin."""
    from backend.services import attachments as attachments_svc

    row = attachments_svc.get_attachment_meta(ref_id)
    if row is None:
        raise HTTPException(404, "No such upload")
    content = attachments_svc.read_attachment_bytes(ref_id)
    if content is None:
        raise HTTPException(404, "Upload file missing on disk")
    filename = row.get("filename") or "file"
    effective_type, headers = inline_blob_headers(row.get("media_type"), filename)
    # An attachment's bytes never change once stored — same immutable-cache reasoning as
    # GET /api/uploads/{ref_id}/image / GET /api/receipts/{id}/file.
    headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return Response(content=content, media_type=effective_type, headers=headers)


@router.get("/api/uploads")
def list_uploads_route():
    """Metadata for every stored attachment (Settings → Uploaded files), newest first."""
    from backend.services.attachments import list_attachments

    return {"uploads": list_attachments()}


@router.delete("/api/uploads/{ref_id}")
def delete_upload_route(ref_id: str):
    """Delete one stored attachment (metadata + payload)."""
    from backend.services.attachments import delete_attachment

    if not delete_attachment(ref_id):
        raise HTTPException(404, "No such upload")
    return {"deleted": ref_id}


@router.delete("/api/uploads")
def delete_all_uploads_route():
    """Delete every stored attachment."""
    from backend.services.attachments import delete_all_attachments

    return {"deleted_count": delete_all_attachments()}
