"""Chat-artifact routes — serve stored artifact documents to the chat UI.

Four read-only routes (the app-wide ``require_auth`` gate already covers them):

* ``GET /api/artifacts/{id}`` — the document source for the chat card's sandboxed
  iframe ``srcDoc``. Served as ``text/plain`` + ``nosniff`` + a ``CSP: sandbox``
  header so a DIRECT browser navigation to the URL can never execute AI-authored
  HTML with same-origin (session-cookie) privileges — the only place an artifact
  executes is the chat's null-origin ``sandbox="allow-scripts"`` iframe.
* ``GET /api/artifacts/{id}/frame`` — the prepared document (CSP meta + height probe) the
  self-hosted UI frames with ``<iframe sandbox src>``; the desktop shell has its own protocol.
* ``GET /api/artifacts/{id}/download`` — the same bytes as a named attachment.
* ``GET /api/artifacts?limit=`` — recent artifact METADATA only (id/title/
  filename/artifact_type/size_bytes/created_at — never the document bytes),
  newest first, across every session. Backs the Overview `lux-html` widget's
  "pick a recent artifact" picker. Plain JSON, no CSRF guard needed (unlike the
  two routes above, this never serves AI-authored document content).

The two single-artifact GETs carry the export-style CSRF guard (Bearer token OR
``X-Requested-With``, the portability.py precedent): the ``onw_session`` cookie
is ``SameSite=Lax`` and WOULD ride a top-level cross-site GET navigation, and
those GETs serve AI-authored content — so a bare navigation is refused outright.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from backend.core.errors import NotFoundError
from backend.services import artifacts as artifacts_service
from backend.services.artifact_frame import RESPONSE_CSP, frame_shell

router = APIRouter()

_BINARY_TYPES = {"pdf", "xlsx"}
_MEDIA_TYPES = {
    "html": "text/html; charset=utf-8",
    "svg": "image/svg+xml; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_INLINE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox",  # belt-and-braces: even if rendered, no scripts/origin
    "Cache-Control": "private, no-store",
}


def _csrf_guard(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    has_bearer = auth.lower().startswith("bearer ") and auth.split(" ", 1)[1].strip() != ""
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if not has_bearer and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")


@router.get("/api/artifacts")
def list_recent_artifacts(limit: int = 20) -> dict:
    """Recent artifact metadata across every session, newest first — never bytes."""
    return {"artifacts": artifacts_service.list_recent(limit)}


@router.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, request: Request) -> Response:
    """The raw document source (for the sandboxed iframe). Never rendered same-origin."""
    _csrf_guard(request)
    try:
        if artifacts_service.get_meta(artifact_id).get("artifact_type") in _BINARY_TYPES:
            raise HTTPException(415, "This artifact is a binary file — use /download.")
        text = artifacts_service.read_text(artifact_id)
    except NotFoundError as exc:
        raise HTTPException(404, exc.message) from exc
    return Response(content=text, media_type="text/plain; charset=utf-8", headers=_INLINE_HEADERS)


@router.get("/api/artifacts/{artifact_id}/frame")
def get_artifact_frame(artifact_id: str) -> Response:
    """The prepared document for the chat's sandboxed ``<iframe src>``.

    An iframe navigation cannot send ``X-Requested-With``, so this route has no CSRF guard;
    what keeps it safe is the response ``CSP: sandbox`` (null origin even when opened
    directly: no cookies, no same-origin requests) plus ``default-src 'none'`` (no egress) and
    ``frame-ancestors 'self'``. It exists because a ``srcdoc`` frame would inherit the app
    page's CSP and could not run the artifact's inline script. The artifact itself sits one
    level down, inside ``frame_shell``, so it cannot navigate its frame to a URL carrying data."""
    try:
        meta = artifacts_service.get_meta(artifact_id)
        artifact_type = meta.get("artifact_type")
        if artifact_type not in artifacts_service.RENDERABLE_TYPES:
            raise HTTPException(415, "Only html and svg artifacts are framed.")
        text = artifacts_service.read_text(artifact_id)
    except NotFoundError as exc:
        raise HTTPException(404, exc.message) from exc
    return Response(
        content=frame_shell(text, artifact_type),
        media_type="text/html; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": RESPONSE_CSP[artifact_type],
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, request: Request) -> Response:
    """The document as a named file attachment (Content-Disposition forces download)."""
    _csrf_guard(request)
    try:
        meta = artifacts_service.get_meta(artifact_id)
        data = artifacts_service.read_bytes(artifact_id)
    except NotFoundError as exc:
        raise HTTPException(404, exc.message) from exc
    media = _MEDIA_TYPES.get(meta["artifact_type"], "text/html; charset=utf-8")
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": _attachment(meta["filename"]),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",  # belt-and-braces parity with the inline route
        },
    )


def _attachment(filename: str) -> str:
    """``Content-Disposition`` for a human filename: an ASCII fallback for old clients plus the
    exact name as RFC 5987 ``filename*`` (the service already removed quotes and controls)."""
    from urllib.parse import quote

    fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
