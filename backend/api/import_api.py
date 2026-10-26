"""Transaction file-import endpoints — every format through the ONE decision pipeline.

POST /api/statements/preview     — dry-run: decision preview (bands · weak twins · ledger
                                   check · fingerprints · cached Statement record; no write)
POST /api/statements/import      — one-shot commit: decision preview + a default-create
                                   decision commit (import batch → rollback, statement
                                   record, idempotent via each format's stable external_ref)
POST /api/statements/commit      — decision commit against a cached preview (RFC D7)
POST /api/statements/csv/preview — back-compat aliases that pin ``format=csv``
POST /api/statements/csv
POST /api/statements/source-peek — SI-67 recognition probe (fingerprint-only, no writes)
GET  /api/statements/formats     — learned statement-format profiles
DELETE /api/statements/formats/{id}
POST /api/statements/merge       — fold an imported draft into an existing transaction

RENAMED 2026-08-23 from the incoherent /api/import/* namespace (which collided in spirit
with backend/api/portability.py's whole-book /api/import bundle restore) onto one
/api/statements/* prefix — statement import is a DIFFERENT concept from portability
(export/restore the entire instance), which now lives under /api/data/*. Clean break, no
alias: see docs/adr/2026-08-23-import-export-routes-coherent-prefixes.md.

The source is EITHER an uploaded ``file`` OR a ``source_url`` (a hosted CSV, a Google
Sheets "Publish to web → CSV" link, or any hosted OFX/QIF) — fetched server-side and run
through the EXACT same path. The format is taken from an explicit ``format`` form field
or, when that is ``auto`` / absent, sniffed from the filename / URL extension and the
content. A CSV/XLSX column ``mapping`` (date/description/amount…) TEACHES the pipeline —
saved as the source's format profile (SI-16) before the standard decision preview runs —
so the next import of the same layout needs no mapping; the structured formats
(OFX/QIF/MT940/CAMT) parse deterministically via ``statements/structured.py`` and PDF via
the tier router. All converge on ``statements/decisions.py`` → the ledger choke, so
spend-rules + categorization + base math + dedup apply identically on every format AND
source. Idempotency keeps each format's id namespace (``ofx:<acct>:<FITID>`` /
``csv:<sha>`` / …), so a re-import — file or the SAME url — dedups to zero.

Thin over the services — no parsing/money math here. Auth-gated by the app-wide
dependency; like the export route they carry the export-style CSRF guard (Bearer OR
``X-Requested-With: XMLHttpRequest``) as defence-in-depth, since the commit writes the
user's ledger.

The B20 async-preview lifecycle's OWN three routes (``GET .../preview-progress/{token}``,
``GET .../{id}/parse-status``, ``POST .../{id}/preview`` reopen) live in the sibling
``statement_parse_api.py`` (code review, SHOULD #B — split out once this file grew past the
repo's file-size guideline) — registered right after this module's own router in
``main.py`` so its ``{id}/preview`` template still can't swallow this file's literal
``csv/preview``/``preview`` siblings."""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.api._deps import _bearer_token
from backend.api.ledger import TransactionCreate
from backend.core.errors import (
    NotFoundError,
    OpenNetWorthError,
    ValidationError,
    to_http_status,
)
from backend.services import import_common, import_csv, import_xlsx
from backend.services.import_common import ImportError_, ImportTooLarge
from backend.services.statements import parse_jobs, parse_progress
from backend.services.statements.source_guard import validate_preview_source

# B20 phase 2 async lifecycle — see StatementStore.set_parse_status / tables.py's comment.
_ASYNC_MODES = frozenset({"sync", "async"})

router = APIRouter()

# Hard cap on the raw upload (defence-in-depth — an import file is line-oriented text/SGML).
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def _clean_progress_token(token: Optional[str]) -> Optional[str]:
    """The client-minted progress token (B20): opaque charset, hard length, or a loud 422 —
    unlike the MCP probe's twin, a malformed one here is a caller BUG worth surfacing, not a
    best-effort UI nicety, since the caller just minted it itself and expects to poll it."""
    if token is None:
        return None
    if not parse_progress.TOKEN_RE.match(token):
        raise HTTPException(422, "progress_token must match ^[A-Za-z0-9_-]{8,64}$")
    return token


def _require_csrf(request: Request) -> None:
    """Export-style CSRF guard: a Bearer token OR an ``X-Requested-With`` header (which a
    cross-origin simple request / top-level navigation cannot set without a preflight we
    do not allow). Neither → 403."""
    has_bearer = _bearer_token(request) is not None
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if not has_bearer and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")


def _parse_mapping(mapping: str) -> dict:
    try:
        parsed = json.loads(mapping or "{}")
    except json.JSONDecodeError:
        raise HTTPException(422, "mapping must be valid JSON")
    if not isinstance(parsed, dict):
        raise HTTPException(422, "mapping must be a JSON object")
    return parsed


def _parse_account_ids(raw: str | None) -> list[str]:
    """The Choose step's chip set, as a JSON array of ids. Malformed input is IGNORED rather
    than 422'd: this field only feeds a prefill hint, and losing it must never cost the owner
    a parse they already paid for."""
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [c for c in (str(v or "").strip() for v in parsed) if c]


def _remember_source_accounts(
    preview: dict, account_id: str | None, account_ids: str | None
) -> None:
    """Stamp the Choose step's account chips onto the format profile this preview just used
    or learned, so the next import of the same layout pre-selects them. Anchor first — the
    profile stores order, and the modal restores ``[0]`` as the anchor."""
    profile_id = ((preview or {}).get("format_profile") or {}).get("id")
    if not profile_id:
        return
    from backend.services.statements import profiles as profiles_service

    anchor = (account_id or "").strip()
    chips = _parse_account_ids(account_ids)
    profiles_service.remember_accounts(profile_id, ([anchor] if anchor else []) + chips)


async def _read_file(file: UploadFile) -> bytes:
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    if not content:
        raise HTTPException(422, "empty upload")
    return content


async def _resolve_source(
    file: Optional[UploadFile], source_url: Optional[str]
) -> tuple[bytes, str | None]:
    """Read the import bytes from EITHER an uploaded file OR a ``source_url`` (a hosted
    CSV / Google-Sheet "Publish to web → CSV" link / any hosted OFX/QIF), and return
    ``(content, filename_hint)`` for format detection. Exactly one source must be given.

    The URL path reuses the SAME parse/preview/commit core as the upload path — only the
    bytes' origin differs. A bad/unreachable URL → 422; an oversized fetch → 413."""
    url = (source_url or "").strip()
    has_file = file is not None and bool(getattr(file, "filename", None))
    if url and has_file:
        raise HTTPException(422, "provide either a file or a source_url, not both")
    if url:
        try:
            content, hint = await asyncio.to_thread(import_common.fetch_url_bytes, url)
        except ImportTooLarge as exc:
            raise HTTPException(413, str(exc))
        except ImportError_ as exc:
            raise HTTPException(422, str(exc))
        return content, hint
    if has_file:
        return await _read_file(file), file.filename
    raise HTTPException(422, "provide a file or a source_url")


def _resolve_format(declared: str | None, filename: str | None, content: bytes) -> str:
    """Pick the import format via the shared ``import_common.detect_format`` (explicit
    ``format`` field → extension → content sniff). QFX/QBO (Quicken's OFX variant) fold to
    the OFX parser, ``sta`` to MT940. Raises 422 for an explicitly-declared unsupported
    format."""
    try:
        return import_common.detect_format(declared, filename, content)
    except ImportError_ as exc:
        raise HTTPException(422, str(exc))


def _preview(
    fmt: str,
    content: bytes,
    mapping: dict,
    account_id: str | None,
    *,
    filename: str = "",
    mime: str = "",
    instructions: str | None = None,
    remember_instructions: bool = True,
    force_reparse: bool = False,
    force_relearn: bool = False,
    password: str | None = None,
    source_attachment_id: str | None = None,
) -> dict:
    """EVERY format previews through the ONE decision pipeline (statements/decisions.py):
    bands + weak twins + ledger check + fingerprints + a cached Statement record. A manual
    CSV/XLSX column mapping TEACHES the pipeline first (saved as this source's format
    profile), then the same decision preview runs — no per-format fork, no client brain.
    ``instructions`` (SI-67) is remembered on the source's format profile and threaded,
    fenced, into the two LLM calls — unless ``remember_instructions`` is false (one-off:
    applies this run only, saved instructions untouched). Changed effective instructions
    or ``force_reparse`` bust the cached parse. ``force_relearn`` ignores this source's
    saved tabular format profile (however it got learned) and re-infers fresh via the LLM,
    overwriting the profile in place — the escape hatch for a profile stuck with a wrong or
    incomplete schema. ``password`` unlocks an encrypted xlsx."""
    try:
        from backend.services.statements import decisions

        return decisions.decision_preview(
            content,
            fmt,
            account_id,
            filename=filename,
            mime=mime,
            mapping=mapping if fmt in ("csv", "xlsx") else None,
            instructions=instructions,
            remember_instructions=remember_instructions,
            force_reparse=force_reparse,
            force_relearn=force_relearn,
            password=password,
            source_attachment_id=source_attachment_id,
        )
    except ImportError_ as exc:
        raise HTTPException(422, str(exc))


def _commit(
    fmt: str,
    content: bytes,
    mapping: dict,
    account_id: str | None,
    *,
    password: str | None = None,
) -> dict:
    """One-shot commit (file in, booked rows out) = the SAME decision pipeline: preview
    (cached parse, statement record, fingerprints) then a default-create decision commit —
    so even a scripted/REST import gets an import batch (rollback), a filed statement
    record, and hard dedup. Needs-eyes rows default to skip, exactly like the modal."""
    try:
        from backend.services.statements import decisions

        preview = decisions.decision_preview(
            content,
            fmt,
            account_id,
            mapping=mapping if fmt in ("csv", "xlsx") else None,
            password=password,
        )
        resolved = account_id or preview.get("resolved_account_id")
        return decisions.decision_commit(preview["statement_id"], resolved, [])
    except ImportError_ as exc:
        raise HTTPException(422, str(exc))


def _find_by_content_hash(content: bytes) -> dict | None:
    """The statement record these EXACT bytes would dedup onto, if one already exists —
    read-only, no write. Shared by the sync-mode / one-shot-import attach gate (M4)."""
    import hashlib

    from backend.db.statement_store import StatementStore

    return StatementStore().find_by_hash(hashlib.sha256(content).hexdigest())


def _clean_mode(mode: str) -> str:
    m = (mode or "sync").strip().lower()
    if m not in _ASYNC_MODES:
        raise HTTPException(422, "mode must be 'sync' or 'async'")
    return m


def _parsing_response(statement_id: str, *, attached: bool) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content={"statement_id": statement_id, "parse_status": "parsing", "attached": attached},
    )


def _attached_to_running_job(existing: dict, store=None) -> bool:
    """True only when BOTH signals agree a job is actually in flight for ``existing``:
    ``parse_jobs.is_running`` (the in-process bookkeeping) AND the record's own ``status``
    column reading ``"parsing"``. Neither alone is trustworthy.

    Live QA (round after 63784dccb) fixed the DB-column-alone version of this bug — a stale
    ``parsing`` row with no running job (a restarted process) used to attach to nothing and
    never resolve. Live QA on THAT fix then found the mirror-image race: a job's own DB write
    (``status`` -> ``ready``/``failed``, via the persistence hook, fired from INSIDE the job's
    call) and its ``parse_jobs._running`` discard (in ``_run``'s own ``finally``, right after
    that call returns) are two separate operations, not one atomic step — a request landing in
    the gap between them saw ``is_running() == True`` on a record whose OWN ``status`` had
    already turned ``ready``, "attached" to a job that had in fact already finished, and did
    nothing: no schedule, no re-parse, zero model calls, yet the very next poll reported
    ``ready`` (because it always had been) — indistinguishable from a real live attach unless
    you check what actually ran. ``existing`` is read by the CALLER at the top of the request,
    before this ever touches ``parse_jobs`` — using its ``status`` alongside ``is_running``
    (rather than a fresh re-read here, which would just move the same race) is what breaks the
    tie in the DB write's favor.

    A stale ``parsing`` row with no running job still self-heals to ``interrupted`` here (the
    earlier fix, unchanged) and this returns ``False`` either way, so the caller schedules its
    own fresh parse — with its own args, ``force_reparse`` included — instead of silently
    attaching to nothing OR to something that already finished. ``store`` reuses an instance
    the caller already has; omitted, one is constructed (mirrors ``_find_by_content_hash``'s
    own style)."""
    if parse_jobs.is_running(existing["id"]) and existing.get("status") == "parsing":
        return True
    if existing.get("status") == "parsing":
        from backend.db.statement_store import StatementStore

        (store or StatementStore()).set_parse_status(existing["id"], "interrupted")
    return False


def _async_accepted(store, record: dict, *args, **kwargs) -> JSONResponse:
    """The 202 branch shared by a fresh async upload and an async reopen-by-id 're-run':
    attach to an ACTUALLY-running parse for this exact record with no new job, or schedule
    ``_preview(*args, **kwargs)`` on the shared pool (``parse_jobs``) and THEN stamp
    `parsing` — never the other way around, so a `QueueFull` (security review H2) leaves the
    record exactly as it was, never claiming a parse that was refused. ``*args`` are
    ``fmt, mapping, account_id`` — never ``content``: the job re-reads the record's own
    stored file at run time (H2).

    Live QA (round after e55bba714) — the DRAINING-JOB race: ``_attached_to_running_job``
    correctly says "not attached" (the record's own ``status`` already reads ``ready``/
    ``failed``), but ``parse_jobs.schedule`` can still find the id in its own ``_running``
    bookkeeping for a few more instructions — the PRIOR job's own status write happened
    inside its call, before its ``_running`` discard runs. ``schedule`` returning ``False``
    (never a silent no-op) is what surfaces this: wait it out (``wait_idle`` — a few
    instructions on the job's own thread, not a real parse) and retry ONCE; if it is
    somehow still outstanding after that, attach honestly (``attached: true``, this
    request's own status write skipped) rather than stamping ``parsing`` over a job that
    isn't coming back.

    Live QA (round after 75feada85) — the STALE-CHANNEL glitch: a NEW job's own
    ``bind_statement`` call (the first thing ``decision_preview`` does) is what points
    ``GET .../parse-status``'s in-memory-first read at ITS channel — until that happens, the
    statement id's alias still points at the PREVIOUS job's (finished, ``done``) one. A poll
    landing between this function's 202 and the new job reaching ``bind_statement`` used to
    read ``ready`` while the record already said ``parsing``. ``unbind_statement`` drops the
    stale alias the moment scheduling is decided, so that whole gap falls through to the
    record's own ``status`` column instead."""
    attached = _attached_to_running_job(record, store)
    # File the upload under its account NOW: the Statements sheet lists by account, and the
    # owner looks there while the parse runs. Commit used to be the only thing that stamped it.
    from backend.services.statements import decisions as decisions_service

    decisions_service.claim_for_account(record["id"], args[2] if len(args) > 2 else None)
    if not attached:
        parse_progress.unbind_statement(record["id"])
        try:
            scheduled = parse_jobs.schedule(record["id"], _preview, *args, **kwargs)
        except parse_jobs.QueueFull as exc:
            raise HTTPException(429, str(exc))
        if not scheduled and parse_jobs.wait_idle(record["id"], timeout=5.0):
            try:
                scheduled = parse_jobs.schedule(record["id"], _preview, *args, **kwargs)
            except parse_jobs.QueueFull as exc:
                raise HTTPException(429, str(exc))
        if not scheduled:
            return _parsing_response(record["id"], attached=True)
        store.set_parse_status(record["id"], "parsing")
    return _parsing_response(record["id"], attached=attached)


# ── generic (format-aware) endpoints ───────────────────────────────────────────────


@router.post("/api/statements/preview", response_model=None)
async def preview_import(
    request: Request,
    file: Optional[UploadFile] = File(None),
    source_url: Optional[str] = Form(None),
    source_attachment_id: Optional[str] = Form(None),
    mapping: str = Form("{}"),
    account_id: Optional[str] = Form(None),
    account_ids: Optional[str] = Form(None),
    format: Optional[str] = Form(None),
    columns_only: Optional[str] = Form(None),
    instructions: Optional[str] = Form(None),
    remember_instructions: bool = Form(True),
    force_reparse: bool = Form(False),
    force_relearn: bool = Form(False),
    password: Optional[str] = Form(None),
    progress_token: Optional[str] = Form(None),
    mode: str = Form("sync"),
) -> dict | JSONResponse:
    """Dry-run: detect the format, parse + (for CSV) apply the mapping, and report what
    WOULD import. No write. Source is an uploaded ``file`` OR a ``source_url`` (a hosted
    CSV / Google-Sheet "Publish to web → CSV" link).

    ``progress_token`` (B20 phase 1) is a client-minted key: while this call is in flight
    (``mode="sync"``, the default), the caller polls
    ``GET /api/statements/preview-progress/{progress_token}`` for the live stage (rendering
    pages, reading page i of N, matching, …) since a scanned statement's vision pass can take
    several minutes with no other signal. Omitted → the preview runs exactly as before,
    untracked.

    ``mode="async"`` (B20 phase 2) resolves the source + format exactly as ``sync`` does, then
    SCHEDULES the same parse on a small server-owned pool and returns ``202
    {statement_id, parse_status: "parsing"}`` immediately — the browser can disconnect; the
    parse keeps running and ``GET /api/statements/{id}/parse-status`` (in-memory while live,
    the record's own durable copy after) tracks it, and ``POST /api/statements/{id}/preview``
    reopens the review once it's ``ready``. A second async upload of the SAME bytes while one
    is already ``parsing`` attaches to it rather than starting a duplicate job.
    ``progress_token`` is meaningless here (there is no live request to poll alongside).

    ``columns_only`` (truthy) is the URL-mode column probe: the browser can't read a
    cross-origin URL's bytes to drive the mapping form, so it asks the server to fetch +
    report ``{format, columns}`` (for the sheet-shaped CSV/XLSX) — or, for a structured
    OFX/QIF/MT940/CAMT, just the detected format (no mapping needed).

    ``account_ids`` (JSON array, anchor first) is the Choose step's full chip set — a
    statement can cover several wallets. It is REMEMBERED on this source's format profile so
    the next import of the same layout pre-selects the same accounts; ``account_id`` stays
    the single anchor the pipeline routes against, unchanged.

    ``password`` unlocks an encrypted xlsx — omitted/wrong surfaces a distinct
    ``password_required:``/``Wrong password`` 422 instead of a raw zipfile error."""
    _require_csrf(request)
    async_mode = _clean_mode(mode) == "async"
    token = None if async_mode else _clean_progress_token(progress_token)
    if source_attachment_id and (source_url or "").strip():
        raise HTTPException(422, "attachment provenance requires an uploaded file")
    content, filename_hint = await _resolve_source(file, source_url)
    try:
        await asyncio.to_thread(validate_preview_source, content, source_attachment_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), str(exc))
    fmt = _resolve_format(format, filename_hint, content)
    if (columns_only or "").strip().lower() in ("1", "true", "yes"):
        try:
            if fmt == "csv":
                cols = import_csv.detect_headers(content)
            elif fmt == "xlsx":
                cols = import_xlsx.detect_headers(content, password=password)
            else:
                cols = []
        except ImportError_ as exc:
            raise HTTPException(422, str(exc))
        result: dict = {"format": fmt, "columns": cols}
        if cols:
            # Pre-fill suggestion: the synonym heuristic first; the LLM fires ONLY when the
            # heuristic can't cover the required fields (date/description/amount). Advisory — the
            # user still confirms the mapping in the form.
            suggested = await import_csv.suggest_mapping(cols)
            result["suggested_mapping"] = suggested["mapping"]
            result["mapping_source"] = suggested["source"]
        return result
    mime_type = (getattr(file, "content_type", "") or "") if file else ""
    if async_mode:
        from backend.db.statement_store import StatementStore

        store = StatementStore()
        # Known BEFORE `create()` — decides whether a QueueFull below must clean up a
        # record+file THIS request minted, or leave a pre-existing dedup hit alone (security
        # review, low #1).
        pre_existing = await asyncio.to_thread(_find_by_content_hash, content)
        # M3: `store.create` is a full `find_by_hash` scan + (on a miss) a 25MB file write —
        # off the event loop, exactly like the sync path's own `_preview` offload below.
        record = await asyncio.to_thread(
            store.create,
            filename_hint or "statement",
            mime_type,
            content,
            {"kind": "import"},
            source_attachment_id=source_attachment_id,
        )
        try:
            # The whole accept branch — schedule() AND set_parse_status()'s table lock — off
            # the event loop (security review, low #3): a scan of a busy pool otherwise
            # blocks every OTHER request on this process while it waits for the lock.
            return await asyncio.to_thread(
                _async_accepted,
                store,
                record,
                fmt,
                _parse_mapping(mapping),
                account_id or None,
                filename=filename_hint or "",
                mime=mime_type,
                instructions=_clean_instructions(instructions),
                remember_instructions=remember_instructions,
                force_reparse=force_reparse,
                force_relearn=force_relearn,
                password=password,
                source_attachment_id=source_attachment_id,
            )
        except HTTPException:
            # QueueFull (429): nothing was scheduled. A record `create()` minted FRESH for
            # this request (never a dedup hit) would otherwise sit forever — status NULL,
            # never parsed, up to 25MB on disk nobody will ever clean up (security review,
            # low #1). A dedup hit (`pre_existing` not None) is untouched either way — it
            # existed before this request and outlives its failure.
            if pre_existing is None:
                await asyncio.to_thread(store.delete_statement, record["id"])
            raise
    # M4 / code-review #2: a SYNC upload (or one-shot commit) of a file whose async job is
    # already `parsing` must attach rather than start a second concurrent parse — double
    # billing, and a race on the progress alias. Cheap: a hash lookup, no write.
    existing = await asyncio.to_thread(_find_by_content_hash, content)
    if existing is not None and await asyncio.to_thread(_attached_to_running_job, existing):
        return _parsing_response(existing["id"], attached=True)
    try:
        with parse_progress.track(token):
            preview = await asyncio.to_thread(
                _preview,
                fmt,
                content,
                _parse_mapping(mapping),
                account_id or None,
                filename=filename_hint or "",
                mime=mime_type,
                instructions=_clean_instructions(instructions),
                remember_instructions=remember_instructions,
                force_reparse=force_reparse,
                force_relearn=force_relearn,
                password=password,
                source_attachment_id=source_attachment_id,
            )
    except OpenNetWorthError as exc:
        # There is no app-wide handler for our domain errors, so an uncaught one leaves here
        # as a bare 500 with the body stripped — the client then shows "something went wrong"
        # for a failure that could name itself. The Lucius pass now RAISES rather than
        # returning a degraded preview (2026-08-09), and its message is the whole point:
        # "Lucius could not apply your instructions … re-read the statement to try again".
        raise HTTPException(to_http_status(exc), str(exc))
    _remember_source_accounts(preview, account_id, account_ids)
    return preview


@router.post("/api/statements/source-peek")
async def peek_import_source(
    request: Request,
    file: Optional[UploadFile] = File(None),
    source_url: Optional[str] = Form(None),
    format: Optional[str] = Form(None),
) -> dict:
    """Choose-step recognition probe (SI-67 prefill): fingerprint the picked file against
    the learned format profiles and return any remembered instructions AND the accounts this
    source was last imported into, so the modal PRE-FILLS its instructions box and its
    account chips — saved guidance is visible and editable instead of invisibly applied, and
    a monthly statement doesn't make the owner re-pick the same wallets every time.
    Fingerprint-only: ZERO LLM calls, no statement record, no writes.
    An unknown or unreadable source answers ``{known: false}`` — never an error the file
    picker has to handle. Off the event loop: fingerprinting a PDF is a text extraction of
    every page (hundreds of ms for a scan) and this fires on every file pick."""
    _require_csrf(request)
    from backend.services.statements import profiles as profiles_service

    empty = {"known": False, "instructions": "", "format_label": None, "account_ids": []}
    try:
        content, filename_hint = await _resolve_source(file, source_url)
        fmt = import_common.detect_format(format, filename_hint, content)
    except (HTTPException, ImportError_):
        return empty
    return await run_in_threadpool(profiles_service.peek_source, content, fmt)


def _clean_instructions(text: str | None) -> str | None:
    """Validate the SI-67 instructions field: bounded, whitespace-stripped, None when blank.
    Over-long input 422s loudly — silent truncation would lose owner guidance invisibly."""
    from backend.services.statements.profiles import MAX_INSTRUCTIONS_CHARS

    clean = (text or "").strip()
    if len(clean) > MAX_INSTRUCTIONS_CHARS:
        raise HTTPException(
            422, f"instructions must be at most {MAX_INSTRUCTIONS_CHARS} characters"
        )
    return clean or None


# RENAMED 2026-08-23 (twice, same day). First from "/api/import", which COLLIDED with
# portability.py's bundle-restore endpoint of the same path. FastAPI resolves on first
# registration and main.py includes this router (line ~922) before portability's (line
# ~985), so the portable-bundle import — the documented restore path, paired with
# /api/export and /api/import/validate — was unreachable: an uploaded .zip was handed to
# the CSV statement parser and died on `_csv.Error: new-line character seen in unquoted
# field`. That side moved because it was the odd one out: its siblings were already
# statement-scoped (/api/import/preview, /api/import/source-peek, /api/import/commit) and
# this bare path had no caller at all, while portability's /api/import was its published
# contract.
#
# Then, the SAME day, the whole /api/import/* namespace (this file) moved again onto
# /api/statements/* for coherence — see the module docstring and
# docs/adr/2026-08-23-import-export-routes-coherent-prefixes.md. This path's siblings are
# now /api/statements/preview, /api/statements/source-peek, /api/statements/commit.
@router.post("/api/statements/import", response_model=None)
async def commit_import(
    request: Request,
    file: Optional[UploadFile] = File(None),
    source_url: Optional[str] = Form(None),
    mapping: str = Form("{}"),
    account_id: Optional[str] = Form(None),
    format: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
) -> dict | JSONResponse:
    """One-shot commit through the decision pipeline: books the clean rows (needs-eyes rows
    skip), files a Statement record, and groups everything under an import batch (rollback).
    Idempotent — a re-import (file OR the same ``source_url``) creates zero duplicates.
    Returns the decision-commit summary: ``{created, merged, duplicates, skipped, transfers,
    total_rows, batch_id, statement_record_id, merge_errors}``. ``password`` unlocks
    an encrypted xlsx.

    M4 / code-review #2: if this exact file already has an async job outstanding, attach
    (202, nothing booked) rather than run a second concurrent decision pipeline against it."""
    _require_csrf(request)
    content, filename_hint = await _resolve_source(file, source_url)
    fmt = _resolve_format(format, filename_hint, content)
    existing = await asyncio.to_thread(_find_by_content_hash, content)
    if existing is not None and await asyncio.to_thread(_attached_to_running_job, existing):
        return _parsing_response(existing["id"], attached=True)
    return await asyncio.to_thread(
        _commit, fmt, content, _parse_mapping(mapping), account_id or None, password=password
    )


# ── learned format profiles (format memory — list / forget) ────────────────────────


@router.get("/api/statements/formats")
def list_format_profiles() -> dict:
    """The learned statement-format profiles (fingerprint → schema). Schema bodies are
    summarized — the UI needs identity/provenance, not column indexes."""
    from backend.db.statement_format_store import StatementFormatStore

    rows = StatementFormatStore().all_profiles()
    return {
        "profiles": [
            {
                "id": p.get("id"),
                "source_label": p.get("source_label"),
                "kind": p.get("kind"),
                "origin": p.get("origin"),
                "version": p.get("version"),
                "uses": p.get("uses"),
                "account_ids": p.get("account_ids") or [],
                "last_used_at": p.get("last_used_at"),
                "created_at": p.get("created_at"),
            }
            for p in rows
        ]
    }


@router.delete("/api/statements/formats/{profile_id}")
def delete_format_profile(profile_id: str) -> dict:
    """Forget a learned format (the GnuCash lesson: memory must be resettable). The next
    import of that source simply re-infers."""
    from backend.db.statement_format_store import StatementFormatStore

    if not StatementFormatStore().delete(profile_id):
        raise HTTPException(404, "format profile not found")
    return {"deleted": True}


# ── import-dedup merge (fold an imported row into an existing transaction) ─────────


class MergeImportBody(BaseModel):
    """Merge an imported draft INTO an existing transaction instead of creating a duplicate.
    ``existing_id`` is the ledger row the import preview matched; ``transaction`` is the
    imported draft (same shape the bulk-import endpoint takes)."""

    existing_id: str
    transaction: TransactionCreate


@router.post("/api/statements/merge")
async def merge_import(request: Request, body: MergeImportBody) -> dict:
    """Fold one imported row into an existing transaction (import-dedup "merge" action): the
    statement amount wins, the existing row keeps its category/note/tags, the foreign charge
    fills a gap, and the import's external_ref is linked so a re-import is a no-op."""
    _require_csrf(request)
    from backend.services import ledger as ledger_service

    try:
        merged = ledger_service.apply_import_merge(body.existing_id, body.transaction.model_dump())
    except NotFoundError as exc:
        raise HTTPException(404, str(exc))
    return {"merged": True, "transaction_id": merged["id"]}


# ── decision-based commit (RFC D7) + statement records ─────────────────────────────


class ImportDecision(BaseModel):
    row: int
    action: str  # create | merge | skip
    existing_id: Optional[str] = None  # merge target (from the preview's match flag)
    edits: Optional[dict] = None  # {category?, payee?, amount?, date?, direction?, account_id?}


class DecisionCommitBody(BaseModel):
    statement_id: str
    account_id: Optional[str] = None
    decisions: list[ImportDecision] = []
    default_action: str = "create"  # rows without a decision (needs-eyes rows default skip)
    # Ledger entries the reviewer marked for deletion in the "In your ledger, not on this
    # statement" panel. Validated server-side against this statement's uncovered set — the
    # list is a request, never an authorization.
    delete_ids: list[str] = []
    # The import review's own Confirm: book the reviewed verdicts and decisions as shown
    # (`decision_commit(reviewed=True)`). Absent = an unattended commit, which keeps the guard.
    reviewed: bool = False


@router.post("/api/statements/commit")
async def commit_decisions(request: Request, body: DecisionCommitBody) -> dict:
    """Apply the review decisions against the CACHED preview parse — one atomic batch
    (creates + merges with pre-merge snapshots for rollback-with-unmerge). Never re-parses,
    never re-bills; the display sample size no longer caps anything."""
    _require_csrf(request)
    from backend.services.statements import decisions as decisions_service

    try:
        return await asyncio.to_thread(
            decisions_service.decision_commit,
            body.statement_id,
            body.account_id,
            [d.model_dump() for d in body.decisions],
            default_action=body.default_action,
            delete_ids=body.delete_ids,
            reviewed=body.reviewed,
        )
    except ImportError_ as exc:
        raise HTTPException(422, str(exc))
    except NotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValidationError as exc:
        raise HTTPException(400, str(exc))


def _statement_summary(record: dict) -> dict:
    meta = record.get("import_meta") or {}
    return {
        "id": record.get("id"),
        "filename": record.get("filename"),
        "created_at": record.get("created_at"),
        "account_id": record.get("account_id"),
        "period_start": meta.get("period_start"),
        "period_end": meta.get("period_end"),
        "statement_date": meta.get("statement_date"),
        "payment_due_date": meta.get("payment_due_date"),
        "amount_due": meta.get("amount_due"),
        "minimum_payment": meta.get("minimum_payment"),
        "closing_balance": meta.get("closing_balance"),
        "currency": meta.get("currency"),
        "rewards": meta.get("rewards"),
        "reconcile": record.get("reconcile"),
        "tier": record.get("tier"),
        "batch_ids": record.get("batch_ids") or [],
        # Filed = committed at least once. A filing that wrote nothing mints no batch and is
        # stamped `filed_at` instead, so batch ids alone no longer say it.
        "filed": bool(record.get("batch_ids") or record.get("filed_at")),
        "size_bytes": record.get("size_bytes"),
        "account_ids": record.get("account_ids") or [],
        "llm_cost_usd": record.get("llm_cost_usd"),
        "llm_calls": record.get("llm_calls"),
        # B20: parsing | ready | failed | interrupted | None (legacy/pre-B20 with no parsed
        # rows at all — genuinely unknown, no lifecycle chrome). Same rule as parse-status.
        "parse_status": parse_progress.record_status(record),
        "parse_error": (record.get("parse_progress") or {}).get("error"),
    }


@router.get("/api/accounts/{account_id}/statements")
def list_account_statements(account_id: str) -> dict:
    """The account's statement history — every imported file, its metadata, reconcile badge,
    and the link (batch ids) to the transactions it produced."""
    from backend.db.statement_store import StatementStore
    from backend.services.statements.statement_list import batches_by_statement, import_facts

    by_statement = batches_by_statement()
    rows = [
        _statement_summary(r) | import_facts(r, by_statement)
        for r in StatementStore().list_statements()
        if r.get("account_id") == account_id or account_id in (r.get("account_ids") or [])
    ]
    return {"statements": rows}


@router.delete("/api/statements/{statement_id}")
def delete_statement(statement_id: str) -> dict:
    """Remove an uploaded statement (record + original file). Transactions the import
    created stay in the ledger — roll back the import batch to undo those."""
    from backend.db.statement_store import StatementStore

    if not StatementStore().delete_statement(statement_id):
        raise HTTPException(404, "Statement not found")
    return {"deleted": True}


class StatementInstructionsBody(BaseModel):
    text: str = ""


@router.put("/api/statements/{statement_id}/instructions")
def save_statement_instructions(
    request: Request, statement_id: str, body: StatementInstructionsBody
) -> dict:
    """Save (or clear — empty text) the upload-time instructions for THIS statement's
    source (SI-67): keys the text onto the source's format profile via the statement's own
    cached bytes, so the next import auto-applies them. Never re-parses, never re-bills —
    edited instructions steer the NEXT fresh parse."""
    _require_csrf(request)
    from backend.db.statement_store import StatementStore
    from backend.services.statements import profiles

    text = body.text if body.text is not None else ""
    if len(text.strip()) > profiles.MAX_INSTRUCTIONS_CHARS:
        raise HTTPException(
            422, f"instructions must be at most {profiles.MAX_INSTRUCTIONS_CHARS} characters"
        )
    store = StatementStore()
    record = store.find_by_id(statement_id)
    if record is None:
        raise HTTPException(404, "statement not found")
    content = store.read_file_bytes(statement_id)
    if content is None:
        raise HTTPException(404, "statement file missing on disk")
    profile = profiles.remember_instructions(
        content,
        record.get("format") or "csv",
        text,
        source_label=record.get("filename") or "",
    )
    # saved=False = this source has no persistable key (e.g. a scanned PDF with no
    # extractable text) — the caller says so instead of silently pretending.
    return {"saved": profile is not None, "text": text.strip()}


class RowTypeBody(BaseModel):
    row: int
    # An account books the row as a transfer with it; None books it as a plain expense/income.
    account_id: Optional[str] = None


@router.post("/api/statements/{statement_id}/row-type")
def set_row_type(request: Request, statement_id: str, body: RowTypeBody) -> dict:
    """The review's Edit window Type switch: remember the owner's choice for this row on the
    statement. The caller re-previews (cached parse, free); the commit re-applies it."""
    _require_csrf(request)
    from backend.db.statement_store import StatementStore
    from backend.services import ledger as ledger_service
    from backend.services.statements import stored_previews

    store = StatementStore()
    record = store.find_by_id(statement_id)
    if record is None:
        raise HTTPException(404, "statement not found")
    if body.account_id:
        account = ledger_service.accounts.find_by_id(body.account_id)
        if account is None or account.get("archived"):
            raise HTTPException(400, "That account isn't available for a transfer.")
    routes = dict(record.get("user_routes") or {})
    routes[str(body.row)] = body.account_id or ""
    store.update_by_id(statement_id, {"user_routes": routes})
    stored_previews.drop(statement_id)
    return {"ok": True, "row": body.row, "account_id": body.account_id}


@router.delete("/api/statements/{statement_id}/lucius-ops")
def undo_lucius_ops(request: Request, statement_id: str, row: Optional[int] = None) -> dict:
    """Undo the Lucius-pass ops applied to this statement's preview (SI-68): all of them,
    or just one row's (``?row=``). The pass hash stays stamped — undo means "not these",
    remembered without re-billing (editing the instructions re-opens the pass). The caller
    re-previews (cached parse, free) to see the reverted drafts; commit re-derives from the
    same record, so what books is always what the last preview showed."""
    _require_csrf(request)
    from backend.db.statement_store import StatementStore

    store = StatementStore()
    record = store.find_by_id(statement_id)
    if record is None:
        raise HTTPException(404, "statement not found")
    ops = list(record.get("lucius_ops") or [])
    remaining = [o for o in ops if o.get("row") != row] if row is not None else []
    # "Not a transfer" on a row the owner routed themselves undoes that choice too.
    routes = dict(record.get("user_routes") or {})
    if row is None:
        routes = {}
    elif routes.get(str(row)):
        routes[str(row)] = ""
    store.update_by_id(statement_id, {"lucius_ops": remaining, "user_routes": routes})
    from backend.services.statements import stored_previews

    stored_previews.drop(statement_id)  # the stored review still shows the undone ops
    return {"undone": True, "removed": len(ops) - len(remaining), "remaining": len(remaining)}


@router.get("/api/statements/{statement_id}/ledger-diff")
def statement_ledger_diff(statement_id: str) -> dict:
    """Ledger transactions in the statement's period + accounts that the statement does NOT
    contain — slipped duplicates to remove, or anomalies to look into (owner ask)."""
    from backend.services.statements.ledger_diff import uncovered_ledger_rows

    return uncovered_ledger_rows(statement_id)


# MUST stay above /api/statements/{statement_id} — FastAPI matches in declaration order, so
# below it this path would be swallowed as a statement whose id is the literal "drift".
@router.get("/api/statements/drift")
def statement_drift_check() -> dict:
    """Does the ledger agree with the bank at every statement close? One row per checkable
    statement, plus a per-account verdict that separates posting-date lag from real error."""
    from backend.services.statement_drift import statement_drift

    return statement_drift()


@router.get("/api/statements/{statement_id}")
def get_statement_record(statement_id: str) -> dict:
    from backend.db.statement_store import StatementStore

    record = StatementStore().find_by_id(statement_id)
    if record is None:
        raise HTTPException(404, "statement not found")
    summary = _statement_summary(record)
    summary["meta"] = record.get("import_meta")
    return summary


@router.get("/api/statements/{statement_id}/file")
def download_statement_file(statement_id: str):
    """The original uploaded statement bytes (audit trail)."""
    from fastapi.responses import Response

    from backend.db.statement_store import StatementStore

    store = StatementStore()
    record = store.find_by_id(statement_id)
    if record is None:
        raise HTTPException(404, "statement not found")
    content = store.read_file_bytes(statement_id)
    if content is None:
        raise HTTPException(404, "statement file missing on disk")
    from backend.api._blob_response import content_disposition

    headers = {
        "Content-Disposition": content_disposition(
            "attachment", record.get("filename") or "statement"
        ),
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type="application/octet-stream", headers=headers)


# ── CSV back-compat aliases (format pinned to csv) ─────────────────────────────────


@router.post("/api/statements/csv/preview")
async def preview_csv_import(
    request: Request,
    file: UploadFile = File(...),
    mapping: str = Form(...),
    account_id: Optional[str] = Form(None),
    progress_token: Optional[str] = Form(None),
) -> dict:
    """Dry-run for CSV specifically (kept for the existing UI/clients). No write.

    ``progress_token`` — see ``preview_import``'s docstring."""
    _require_csrf(request)
    token = _clean_progress_token(progress_token)
    content = await _read_file(file)
    with parse_progress.track(token):
        return await asyncio.to_thread(
            _preview, "csv", content, _parse_mapping(mapping), account_id or None
        )


@router.post("/api/statements/csv")
async def commit_csv_import(
    request: Request,
    file: UploadFile = File(...),
    mapping: str = Form(...),
    account_id: Optional[str] = Form(None),
) -> dict:
    """Commit for CSV specifically (kept for the existing UI/clients)."""
    _require_csrf(request)
    content = await _read_file(file)
    return await asyncio.to_thread(
        _commit, "csv", content, _parse_mapping(mapping), account_id or None
    )
