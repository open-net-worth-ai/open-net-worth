"""The tier router (RFC D3): pick the cheapest tier that can read the input, then guard it.

``parse_statement`` consumes the upload-cache shape (``{"kind": "text"|"pdf_pages", ...}``);
``parse_statement_bytes`` adapts raw import-endpoint bytes (pdf/csv/xlsx) into that shape —
including rendering a scanned PDF's pages to images (pymupdf) exactly like ``api/upload.py``
does for chat drops, so BOTH surfaces read scans."""

from __future__ import annotations

import asyncio
import contextvars
import threading

from backend.services.statements import parse_progress
from backend.services.statements.canonical import ParseResult
from backend.services.statements.guards import apply_guards
from backend.services.statements.tiers import (
    StatementParseError,
    looks_tabular,
    tier1_tabular,
    tier2_text,
    tier3_vision,
)
from backend.services.xlsx_open import load_values_workbook

_PDF_MAGIC = b"%PDF-"
_VISION_PAGE_CAP = 40  # cost ceiling (D3): a vision call per page; past this, refuse loudly


async def parse_statement(
    upload: dict,
    *,
    schema: dict | None = None,
    guidance: str | None = None,
    instructions: str | None = None,
    force_relearn: bool = False,
) -> ParseResult:
    """Route one upload through the cheapest capable tier, then run the D4 guards.

    ``schema`` (a saved FormatProfile / taught skill schema) short-circuits tabular inference
    to the zero-LLM fast path; a stale schema raises ``StaleSchemaError`` for the caller's
    re-learn loop. ``instructions`` (SI-67 upload-time owner guidance) rides FENCED into the
    document tiers' row-extraction prompts; the deterministic tabular tier ignores it.
    ``force_relearn`` ignores a saved format profile for this fingerprint (tabular sources
    only) and re-runs cold LLM schema inference — the escape hatch for a profile stuck with a
    wrong/incomplete schema (e.g. a manually-mapped one with no status column)."""
    kind = upload.get("kind", "")
    if kind == "pdf_pages":
        images = upload.get("images") or []
        if len(images) > _VISION_PAGE_CAP:
            raise StatementParseError(
                f"This scan has {len(images)} pages — beyond the {_VISION_PAGE_CAP}-page "
                "vision limit. Split the file or export a text PDF."
            )
        result = await tier3_vision(images, guidance=guidance, instructions=instructions)
        return apply_guards(result)
    if kind == "text":
        text = upload.get("text") or ""
        if not text.strip():
            raise StatementParseError("The file contains no readable text.")
        if upload.get("shape") == "tabular" or looks_tabular(text):
            result = await _parse_tabular(text, schema, force_relearn=force_relearn)
            return apply_guards(result)
        result = await tier2_text(text, guidance=guidance, instructions=instructions)
        return apply_guards(result, source_text=text)
    raise StatementParseError(f"Unsupported upload kind {kind!r}.")


async def _parse_tabular(
    text: str, schema: dict | None, *, force_relearn: bool = False
) -> ParseResult:
    """The tabular fast path with FORMAT MEMORY (RFC D3): a known fingerprint parses with the
    saved schema (0 LLM); validation failure on a hit means the bank changed its layout →
    re-infer + version-bump (never a silent mis-parse); a fresh source is learned after its
    inferred schema survives validation. An explicit ``schema`` (a taught skill / API caller)
    bypasses memory entirely. ``force_relearn`` treats a fingerprint match as a miss — a fresh
    cold inference runs and its schema overwrites the saved profile in place (``profiles.learn``
    always replaces a changed schema, regardless of origin rank)."""
    from backend.services.statements import profiles
    from backend.services.statements.tiers import StaleSchemaError, parse_grid

    if schema is not None:
        return await tier1_tabular(text, schema=schema)
    grid = parse_grid(text)
    prof = None if force_relearn else profiles.find_profile(grid)
    if prof:
        try:
            result = await tier1_tabular(text, schema=prof.get("schema") or {})
            profiles.record_use(prof)
            result.meta.extra["format_profile"] = {
                "id": prof["id"],
                "version": prof.get("version"),
                "origin": prof.get("origin"),
                "source_label": prof.get("source_label"),
                "account_ids": prof.get("account_ids") or [],
            }
            return result
        except StaleSchemaError:
            result = await tier1_tabular(text)
            updated = profiles.learn(grid, result.schema or {})
            result.meta.warnings.append("this source's format changed — its layout was re-learned")
            if updated:
                result.meta.extra["format_profile"] = {
                    "id": updated["id"],
                    "version": updated.get("version"),
                    "origin": updated.get("origin"),
                    "relearned": True,
                }
            return result
    result = await tier1_tabular(text)
    learned = profiles.learn(grid, result.schema or {})
    if learned:
        result.meta.extra["format_profile"] = {
            "id": learned["id"],
            "version": learned.get("version"),
            "origin": learned.get("origin"),
            "learned": True,
        }
    return result


# ── bytes → upload adaptation (the import-endpoint path) ────────────────────────────


def _decode_text(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _xlsx_to_csv_text(content: bytes, password: str | None = None) -> str:
    """XLSX → CSV-shaped text with '[Sheet: NAME]' markers (mirrors api/upload.py so the
    tabular tier sees one shape from both surfaces).

    A password-protected workbook is decrypted first (``services.office_crypt``, the same
    helper the chat/email upload path uses) — previously this ONLY ran on the chat/email
    ingress; the statements/import UI path fed straight into ``openpyxl`` and surfaced a raw
    ``zipfile.BadZipFile`` message with no hint it was a password problem."""
    import csv as _csv
    import io

    from backend.services.office_crypt import decrypt_office

    try:
        content, _ = decrypt_office(
            content, password, resend_hint="Provide the file's password and try again."
        )
    except ValueError as exc:
        raise StatementParseError(str(exc))
    try:
        wb = load_values_workbook(content)
    except Exception as exc:  # zipfile.BadZipFile & friends — user-correctable, never a 500
        raise StatementParseError(f"Not a readable Excel (.xlsx) file: {exc}")
    out = io.StringIO()
    writer = _csv.writer(out)
    for sheet in wb.worksheets:
        out.write(f"[Sheet: {sheet.title}]\n")
        for row in sheet.iter_rows(values_only=True):
            writer.writerow(["" if c is None else str(c) for c in row])
    wb.close()
    return out.getvalue()


def _page_text(page, *, layout: bool = True) -> str:
    """One page's text in LAYOUT mode, degrading per-PAGE rather than failing the file.

    A page with no content stream — every page of a scanned statement — raises inside layout
    extraction (``KeyError: '/Contents'``) where the default mode simply returns "". That
    difference matters: an empty read is the signal that routes a textless PDF to the vision
    tier, so letting the exception escape turned "scan it instead" into "Could not read the
    PDF" (caught by ``test_textless_pdf_routes_to_vision_pages``, 2026-08-09). A mixed file —
    some real text pages, some scanned inserts — must still yield the text it does have.
    """
    try:
        if not layout:
            return page.extract_text() or ""
        return page.extract_text(extraction_mode="layout") or ""
    except Exception:  # noqa: BLE001 - any extraction failure means "no text on this page"
        try:
            return page.extract_text() or ""
        except Exception:  # noqa: BLE001
            return ""


def pdf_text(content: bytes, *, layout: bool = True) -> str:
    """The PDF's selectable text, or ``""`` when it has none.

    Extraction never renders a page, so source fingerprinting does not reopen the import
    progress stage after the main parse has rendered an image-only statement.
    """
    import io

    from pypdf import PdfReader

    if _PDF_MAGIC not in content[:1024]:
        raise StatementParseError("This does not look like a PDF (no %PDF- marker).")
    try:
        return "\n".join(
            _page_text(page, layout=layout) for page in PdfReader(io.BytesIO(content)).pages
        ).strip()
    except Exception as exc:
        raise StatementParseError(f"Could not read the PDF: {exc}")


def _pdf_to_upload(content: bytes, *, layout: bool = True, report_render: bool = True) -> dict:
    """PDF bytes → text upload, or (no selectable text) rendered page images — the scanned
    statement finally imports instead of erroring (RFC Goals).

    ``report_render`` (live QA): the MAIN parse path renders once and wants that progress
    visible. ``profiles._document_fingerprint`` calls back into this SAME function, on the
    SAME scanned PDF, purely to hash a stable identity AFTER the real parse already
    finished — with reporting left on, that second render re-opened the ``render`` stage in
    the snapshot after later stages (``reconcile``) had already run, so the UI's stage bar
    and stage list both stepped backwards. ``False`` renders the pages without touching the
    progress channel at all."""
    import base64

    text = pdf_text(content, layout=layout)
    if text:
        return {"kind": "text", "text": text}
    try:
        import fitz  # pymupdf — same renderer the chat upload path uses

        doc = fitz.open(stream=content, filetype="pdf")
        page_count = doc.page_count
        images = []
        for i, page in enumerate(doc):
            # One "render" entry, counters updated in place per page (parse_progress.report's
            # own same-stage-updates-in-place rule) — not one entry per page. Skipped
            # entirely when report_render=False (a fingerprint-only re-render, not the main
            # parse — see this function's own docstring).
            if report_render:
                parse_progress.report("render", page=i + 1, pages=page_count)
            images.append(
                {
                    "media_type": "image/png",
                    "data": base64.b64encode(page.get_pixmap(dpi=150).tobytes("png")).decode(),
                }
            )
        doc.close()
    except Exception as exc:
        raise StatementParseError(f"Could not render the scanned PDF's pages: {exc}")
    if not images:
        raise StatementParseError("The PDF has no pages.")
    return {"kind": "pdf_pages", "images": images}


def to_upload(
    content: bytes,
    fmt: str,
    *,
    password: str | None = None,
    layout: bool = True,
    report_render: bool = True,
) -> dict:
    """Raw import bytes + detected format → the upload shape ``parse_statement`` consumes.

    ``password`` unlocks an encrypted xlsx (ignored for every other format).

    ``layout`` selects the PDF extraction mode. True (the default) preserves column
    positions, which is what the PARSE needs — a bank encodes withdrawal-vs-deposit as
    horizontal position and nothing else. Pass False for reading-order text, which is what a
    caller wants when it needs a STABLE rendering rather than a faithful one: see
    ``profiles._document_fingerprint``. Ignored for every non-PDF format.

    ``report_render`` — see ``_pdf_to_upload``'s own docstring: ``False`` for a caller
    re-rendering the SAME scanned PDF for a reason other than the main parse (the document
    fingerprint), so its render never re-opens the ``render`` stage after later stages have
    already run. Ignored for every non-PDF format."""
    if not content:
        raise StatementParseError("The file is empty.")
    if fmt == "pdf":
        return _pdf_to_upload(content, layout=layout, report_render=report_render)
    if fmt == "xlsx":
        text = _xlsx_to_csv_text(content, password=password)
        return {"kind": "text", "text": text, "shape": "tabular"}
    return {"kind": "text", "text": _decode_text(content), "shape": "tabular"}


def run_async(coro):
    """Run an async parse to completion from a SYNC entrypoint — works both with no running
    loop (plain call) and inside one (FastAPI route / tool thread), via a dedicated thread.

    The caller's contextvars are COPIED into the thread: a raw Thread starts with an empty
    context, which silently orphaned every ContextVar-based seam across this boundary —
    seen live as the per-import cost collector reading $0 ("parsed from memory") while the
    provider billed the global ledger. Mutations to objects held in those vars (the cost
    scope's dict) remain visible to the caller — same objects, copied bindings."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: dict = {}
    ctx = contextvars.copy_context()

    def _worker() -> None:
        try:
            result["value"] = ctx.run(asyncio.run, coro)
        except BaseException as exc:  # noqa: BLE001 — propagate, never swallow
            result["error"] = exc

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def parse_statement_bytes(
    content: bytes,
    fmt: str,
    *,
    schema: dict | None = None,
    guidance: str | None = None,
    instructions: str | None = None,
    force_relearn: bool = False,
    password: str | None = None,
) -> ParseResult:
    """Sync facade for the import endpoints/tools: bytes + format → guarded ParseResult.

    The structured formats (OFX/QIF/MT940/CAMT) never touch a tier or an LLM — they route
    to the deterministic canonical adapters, so the decision pipeline covers every format
    (``force_relearn`` is meaningless there and simply ignored). ``password`` unlocks an
    encrypted xlsx; every other format ignores it."""
    from backend.services.statements import structured

    if fmt in structured.STRUCTURED_FORMATS:
        return structured.to_canonical(content, fmt)
    return run_async(
        parse_statement(
            to_upload(content, fmt, password=password),
            schema=schema,
            guidance=guidance,
            instructions=instructions,
            force_relearn=force_relearn,
        )
    )
