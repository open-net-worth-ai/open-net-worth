"""Parse raw uploaded bytes into an attachment payload entry — pure, no store writes.

Moved out of ``backend/api/upload.py`` (cross-channel-attachments RFC item 11: "one
service operation that validates and persists an inbound channel file before turn
construction"): parsing is business logic (services/), not a router concern, and the
Telegram connector needs the SAME parser the web upload endpoint uses rather than
importing it from the api/ layer (a connector reaching into api/ is exactly the kind of
duplicated-logic drift the RFC flags — see ``services.attachment_ingress`` for the shared
persist operation both channels now call).
"""

from __future__ import annotations

import base64
import csv
import io

import fitz  # pymupdf
import pypdf
import xlrd
from pypdf.errors import FileNotDecryptedError

from backend.services.office_crypt import decrypt_office
from backend.services.xlsx_open import load_values_workbook

# The structured statement formats that carry their own machine-readable transaction
# structure (no column mapping). They are plain text (OFX/QFX SGML/XML, QIF, MT940) or XML
# (CAMT.053) — we extract a text view for Lucius to read AND stash the raw bytes + detected
# format so ``import_transactions(attachment_ref=…)`` can run the structured importer.
_STRUCTURED_EXTS = (".ofx", ".qfx", ".qbo", ".qif", ".mt940", ".sta", ".camt")

# What a person can attach, in their words — the ONE list every refusal names. The composer's
# file picker (frontend ``ATTACH_ACCEPT``) offers exactly these.
SUPPORTED_UPLOADS = (
    "images (PNG, JPG, HEIC, …), PDF, CSV, TXT, Excel (.xlsx, .xls), "
    "and bank statement files (OFX, QFX, QBO, QIF, MT940, CAMT)"
)


class UnsupportedFileError(ValueError):
    """A file type no reader handles — answered with 415 and the supported list."""


def unsupported_file_message(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    what = f"a .{ext} file" if ext else "a file type"
    return f"Lucius can't read {what}. Supported: {SUPPORTED_UPLOADS}."


class PasswordProtectedPdfError(ValueError):
    """A PDF that needs a password to open. Nothing hands the PDF reader one, so unlike an
    Office file's ``password_required:`` sentinel — which invites a resend with the password —
    the only way through is to remove it."""


def _detect_import_format(filename: str, content: bytes) -> str | None:
    """Detect the import format for a cached attachment, or None if it isn't importable.
    Reuses ``import_common.detect_format`` (the single source of truth) on the filename +
    a content sniff — so the uploader and the import tool agree on format. Best-effort: a
    detection failure (an unparseable blob) is treated as not-importable, never an error."""
    from backend.services.import_common import ImportError_, detect_format

    try:
        return detect_format(None, filename, content)
    except ImportError_:
        return None


def parse_upload(filename: str, mime: str, content: bytes, password: str | None = None) -> dict:
    """
    Parse file bytes into an attachment cache entry dict (no ref_id / expires_at).
    Raises ValueError for unsupported or unreadable files — ``PasswordProtectedPdfError`` for
    a PDF that needs a password.

    Importable statement uploads (CSV / OFX / QFX / QIF / MT940 / CAMT / XLSX / PDF) also
    carry the RAW ``content`` bytes + a detected ``format`` on the entry, so the
    ``import_transactions`` tool can run the structured importer (dedup/preview/commit
    through the ledger choke point) directly from the cached attachment — not just read
    the text.
    """
    filename = filename or ""

    if mime.startswith("image/"):
        b64 = base64.standard_b64encode(content).decode()
        return {
            "kind": "image",
            "media_type": mime,
            "data": b64,
            "summary": f"Image ({filename}, {len(content) // 1024}KB)",
        }

    if mime in ("text/csv", "text/plain") or filename.lower().endswith((".csv", ".txt")):
        text = content.decode("utf-8", errors="replace")
        try:
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            summary = f"CSV file '{filename}' with {len(rows)} rows, columns: {', '.join(reader.fieldnames or [])}"
        except Exception:
            summary = f"Text file '{filename}' ({len(content)} bytes)"
        return {
            "kind": "text",
            "text": text,
            "summary": summary,
            "format": _detect_import_format(filename, content),
            "content": content,
        }

    # Structured statement formats (OFX/QFX/QIF/MT940/CAMT) — plain text or XML the
    # uploader used to reject. Keep a readable text view for Lucius AND stash the raw bytes
    # + format so the structured importer can run directly from the attachment.
    lower = filename.lower()
    is_structured = lower.endswith(_STRUCTURED_EXTS)
    if not is_structured and lower.endswith(".xml"):
        # An .xml might be CAMT.053; defer to the content sniff (only a real CAMT doc is
        # importable — arbitrary XML falls through to the unsupported error below).
        is_structured = _detect_import_format(filename, content) == "camt"
    if is_structured:
        fmt = _detect_import_format(filename, content)
        if fmt is None:
            raise ValueError(
                "Could not recognise this statement file (expected OFX/QFX/QIF/MT940/CAMT)."
            )
        text = content.decode("utf-8", errors="replace")
        return {
            "kind": "text",
            "text": text,
            "summary": f"{fmt.upper()} statement '{filename}' ({len(content) // 1024}KB)",
            "format": fmt,
            "content": content,
        }

    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    xls_mime = "application/vnd.ms-excel"
    is_xlsx = mime == xlsx_mime or filename.lower().endswith(".xlsx")
    is_xls = mime == xls_mime or filename.lower().endswith(".xls")
    if is_xlsx or is_xls:
        content, was_encrypted = decrypt_office(
            content, password, resend_hint="Resend it with the password in the caption."
        )
        try:
            sheets_text: list[str] = []
            total_rows = 0
            if is_xls and not is_xlsx:
                wb_xls = xlrd.open_workbook(file_contents=content)
                for sheet_name in wb_xls.sheet_names():
                    ws = wb_xls.sheet_by_name(sheet_name)
                    if ws.nrows == 0:
                        continue
                    total_rows += ws.nrows - 1
                    buf = io.StringIO()
                    writer = csv.writer(buf)
                    for r in range(ws.nrows):
                        writer.writerow([ws.cell_value(r, c) for c in range(ws.ncols)])
                    sheets_text.append(f"[Sheet: {sheet_name}]\n{buf.getvalue()}")
                n_sheets = wb_xls.nsheets
            else:
                wb = load_values_workbook(content)
                for sheet_name in wb.sheetnames:
                    ws = wb[sheet_name]
                    rows = list(ws.iter_rows(values_only=True))
                    if not rows:
                        continue
                    total_rows += len(rows) - 1
                    buf = io.StringIO()
                    writer = csv.writer(buf)
                    for row in rows:
                        writer.writerow(["" if v is None else v for v in row])
                    sheets_text.append(f"[Sheet: {sheet_name}]\n{buf.getvalue()}")
                n_sheets = len(wb.sheetnames)
                wb.close()
            text = "\n\n".join(sheets_text)
            summary = f"Excel file '{filename}' — {n_sheets} sheet(s), ~{total_rows} data rows"
        except Exception as exc:
            raise ValueError(
                "This Excel file couldn't be opened. Open it, save it again as .xlsx (or "
                "export it as CSV), and attach that copy."
            ) from exc
        entry = {"kind": "text", "text": text, "summary": summary, "was_encrypted": was_encrypted}
        # Only modern .xlsx has a structured importer (import_xlsx reads OOXML); a legacy
        # .xls has none, so it stays text-only (no `format`). Stash the DECRYPTED bytes so
        # the importer reads the same workbook this text view came from.
        if is_xlsx:
            entry["format"] = "xlsx"
            entry["content"] = content
        return entry

    if mime == "application/pdf" or filename.lower().endswith(".pdf"):
        pages = _pdf_pages_text(content)
        page_count = len(pages)
        text = "\n\n".join(f"[Page {i + 1}]\n{p}" for i, p in enumerate(pages) if p.strip())

        if text.strip():
            # A text-bearing PDF is importable by the LLM-parsed import_pdf path; stash the
            # raw bytes + format so `import_transactions(attachment_ref=…)` can parse it.
            return {
                "kind": "text",
                "text": text,
                "summary": f"PDF '{filename}' ({page_count} pages, {len(content) // 1024}KB)",
                "format": "pdf",
                "content": content,
            }

        try:
            doc = fitz.open(stream=content, filetype="pdf")
            _assert_renderable(doc, filename)
            images = []
            for page in doc:
                pix = page.get_pixmap(dpi=_PDF_RENDER_DPI)
                data = pix.tobytes("png")
                images.append(
                    {
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(data).decode(),
                    }
                )
            doc.close()
        except Exception as exc:
            raise ValueError(f"Could not render PDF pages: {exc}")
        return {
            "kind": "pdf_pages",
            "images": images,
            "summary": f"PDF '{filename}' ({page_count} pages, scanned, {len(content) // 1024}KB)",
            # Importable too: the statement importer reads a scan through its vision tier, the
            # same way Accounts → Import does. Asked of the same detector that path uses, so the
            # two can't disagree again (a scan was left unlabelled here in June, when the
            # importer still refused scans, and stayed that way after it learned to read them).
            "format": _detect_import_format(filename, content),
            # The rendered per-page PNGs above are for Lucius to READ (a scanned PDF has no
            # extractable text) — they were never meant to be the only record of this file.
            # Keeping the original bytes too, same as the text-bearing branch above, means
            # anything that needs the real file back (receipts promotion) gets it, instead of
            # reaching for a lossy substitute or refusing outright.
            "content": content,
        }

    raise UnsupportedFileError(unsupported_file_message(filename))


_PDF_RENDER_DPI = 150
_PDF_MAX_RENDER_PAGES = 30
# A4 at 150 dpi is ~2.2 MP, A3 ~4.3 MP. A pixmap is width x height x 3 bytes in memory, and a
# page box can declare 14400 x 14400 pt (~434 MP at 150 dpi, >1 GB) in a few hundred bytes.
_PDF_MAX_RENDER_PIXELS = 12_000_000


def _assert_renderable(doc, filename: str) -> None:
    """Refuse, BEFORE any pixmap is allocated, a scanned PDF too big to rasterize safely."""
    if doc.page_count > _PDF_MAX_RENDER_PAGES:
        raise ValueError(
            f"PDF '{filename}' has {doc.page_count} pages and no text layer; scanned PDFs over "
            f"{_PDF_MAX_RENDER_PAGES} pages can't be read. Split it into smaller files."
        )
    scale = _PDF_RENDER_DPI / 72
    for page in doc:
        if page.rect.width * scale * page.rect.height * scale > _PDF_MAX_RENDER_PIXELS:
            raise ValueError(
                f"PDF '{filename}' has a page far larger than a normal sheet and can't be read. "
                "Re-export it at a standard page size."
            )


def _pdf_pages_text(content: bytes) -> list[str]:
    """Each page's extracted text. pypdf raises its own errors on a locked or damaged file,
    never the ValueError every caller handles — each was a 500 (security review S5)."""
    try:
        return [page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(content)).pages]
    except FileNotDecryptedError as exc:
        raise PasswordProtectedPdfError(
            "This PDF is password-protected — remove the password and send it again."
        ) from exc
    except Exception as exc:
        raise ValueError("Could not read this PDF — it may be damaged.") from exc
