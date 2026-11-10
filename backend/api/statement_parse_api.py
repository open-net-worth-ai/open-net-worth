"""B20 async statement-preview lifecycle — the NEW routes this feature added, split out of
``import_api.py`` (code review, SHOULD #B: that file had grown past this repo's ~400-line file
guideline with these three routes mixed into the original preview/commit surface).

GET  /api/statements/preview-progress/{token} — live stage of an IN-FLIGHT sync preview
GET  /api/statements/{statement_id}/parse-status — the durable async lifecycle for one id
POST /api/statements/{statement_id}/preview — reopen a statement BY ID (no re-upload);
                                               ``mode=async`` is how a lifecycle row's
                                               "Re-run" restarts a parse in the background

Every shared helper (``_require_csrf``, ``_preview``, ``_async_accepted``,
``_attached_to_running_job``, ``_parsing_response``, ``_clean_mode``, ``_resolve_format``,
``_clean_instructions``, ``_remember_source_accounts``) is imported from ``import_api`` rather
than duplicated or promoted to a shared module — this module depends on that one, never the
reverse, so there is no import cycle to avoid.

ROUTE ORDER (main.py): this router is registered AFTER ``import_router`` — ``{statement_id}/
preview`` is a template that matches the literal segment "csv" (``/api/statements/csv/
preview``) or "preview" itself just as readily as a real id, and Starlette matches routes in
OVERALL registration order across every included router, not per-router. Registering this
router any earlier would let the reopen route swallow those literal siblings exactly the way
declaring it earlier WITHIN one router already had to be guarded against (see this module's
own route docstring below)."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.api.import_api import (
    _async_accepted,
    _attached_to_running_job,
    _clean_instructions,
    _clean_mode,
    _parsing_response,
    _preview,
    _remember_source_accounts,
    _require_csrf,
    _resolve_format,
)
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services.statements import parse_progress
from backend.services.statements.source_guard import validate_preview_source

router = APIRouter()


@router.get("/api/statements/preview-progress/{token}")
def preview_progress(token: str) -> dict:
    """Live stage of a preview minted with the given ``progress_token``, polled WHILE the
    ``POST /api/statements/preview`` (or ``.../csv/preview``) call above is still in flight —
    same shape as the MCP probe's twin (``mcp.probe_progress``), same reason: the preview
    stays a synchronous call, so this is a separate read the UI polls alongside it rather than
    a background job with a registry to abandon.

    Unlike that twin, an unknown/expired token here is a 404, not a finished-probe shape: the
    caller minted this token itself moments ago, so a miss means it mistyped it or the server
    restarted — worth surfacing, not papering over. Never carries statement content — stage
    names, labels and counters only."""
    snap = parse_progress.snapshot(token)
    if snap is None:
        raise HTTPException(404, "unknown or expired progress token")
    return snap


@router.get("/api/statements/{statement_id}/parse-status")
def statement_parse_status(statement_id: str) -> dict:
    """The B20 async-preview lifecycle for one statement: ``{parse_status, progress, error}``.

    In-memory FIRST (``parse_progress.snapshot`` — the live registry, while a parse is
    actually running in THIS process) and the record's own durable copy (``StatementStore.
    status``/``parse_progress``, written by the same registry's persistence hook) otherwise —
    so this answers correctly whether the parse is live, finished moments ago, or survived a
    restart (the boot sweep's ``interrupted``). 404 only for an id that never existed."""
    from backend.db.statement_store import StatementStore

    live = parse_progress.snapshot(statement_id)
    if live is not None:
        return {
            "parse_status": parse_progress.status_for(live),
            "progress": live,
            "error": live.get("error"),
        }
    record = StatementStore().find_by_id(statement_id)
    if record is None:
        raise HTTPException(404, "statement not found")
    progress = record.get("parse_progress")
    return {
        "parse_status": parse_progress.record_status(record),
        "progress": progress,
        "error": (progress or {}).get("error"),
    }


# MUST stay below EVERY literal `/api/statements/<word>/preview` route (csv/preview included,
# both still in `import_api.py`'s router — registered BEFORE this one in main.py) — see this
# module's own docstring for why registration order across routers matters here exactly the
# way declaration order within one router would.
@router.post("/api/statements/{statement_id}/preview", response_model=None)
async def reopen_statement_preview(
    request: Request,
    statement_id: str,
    account_id: Optional[str] = Form(None),
    account_ids: Optional[str] = Form(None),
    instructions: Optional[str] = Form(None),
    remember_instructions: bool = Form(True),
    force_reparse: bool = Form(False),
    force_relearn: bool = Form(False),
    password: Optional[str] = Form(None),
    mode: str = Form("sync"),
) -> dict | JSONResponse:
    """Reopen a statement BY ID — no re-upload. Reads the ORIGINAL bytes back off this
    record's own file (a server-owned path, never client-supplied) and runs the identical
    decision preview a fresh upload of the same file would: a cache hit (the common case —
    review a ``ready`` statement, or "Review →" from the statements list) is instant and
    free; ``force_reparse``/``force_relearn`` re-read it. This is also the SYNC-mode recovery
    path after a dropped connection, and — with ``mode="async"`` — how an ``interrupted`` or
    ``failed`` row's "Re-run" restarts the parse in the background."""
    _require_csrf(request)
    from backend.db.statement_store import StatementStore

    store = StatementStore()
    record = store.find_by_id(statement_id)
    if record is None:
        raise HTTPException(404, "statement not found")
    content = store.read_file_bytes(statement_id)
    if content is None:
        raise HTTPException(404, "statement file missing on disk")
    source_attachment_id = record.get("source_attachment_id")
    try:
        await asyncio.to_thread(validate_preview_source, content, source_attachment_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), str(exc))
    fmt = record.get("format") or _resolve_format(None, record.get("filename"), content)
    # A reopen that names no account opens the statement for the account it is FILED under
    # (stamped at upload by `claim_for_account`) — the Review buttons pass none, and a
    # statement whose own number can't be matched then came back "needs an account".
    account_id = account_id or record.get("account_id") or None
    if _clean_mode(mode) == "async":
        # Off the event loop (security review, low #3) — same reasoning as the fresh-upload
        # branch in `preview_import`. No orphan-cleanup concern here: a reopen never mints a
        # new record or file, only ever operates on `statement_id`'s own already-existing row.
        return await asyncio.to_thread(
            _async_accepted,
            store,
            record,
            fmt,
            {},
            account_id or None,
            filename=record.get("filename") or "",
            mime=record.get("mime") or "",
            instructions=_clean_instructions(instructions),
            remember_instructions=remember_instructions,
            force_reparse=force_reparse,
            force_relearn=force_relearn,
            password=password,
            source_attachment_id=source_attachment_id,
        )
    # M4: this exact record already has an async job outstanding — attach instead of running
    # a second, sync, concurrent decision_preview against the same statement.
    if await asyncio.to_thread(_attached_to_running_job, record, store):
        return _parsing_response(statement_id, attached=True)
    try:
        preview = await asyncio.to_thread(
            _preview,
            fmt,
            content,
            {},
            account_id or None,
            filename=record.get("filename") or "",
            mime=record.get("mime") or "",
            instructions=_clean_instructions(instructions),
            remember_instructions=remember_instructions,
            force_reparse=force_reparse,
            force_relearn=force_relearn,
            password=password,
            source_attachment_id=source_attachment_id,
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), str(exc))
    _remember_source_accounts(preview, account_id, account_ids)
    return preview
