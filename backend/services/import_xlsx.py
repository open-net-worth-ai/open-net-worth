"""XLSX transaction import (Excel workbook — "CSV with cells").

An XLSX is just a CSV the user kept in Excel: a header row + data rows on a sheet. So this
module does the LEAST possible — it reads the FIRST worksheet into the SAME ``(columns, rows)``
shape ``import_csv.parse_csv`` produces, then DELEGATES the column→field mapping, draft build,
dedup and base math to ``import_csv`` (``map_sheet_rows`` / ``build_preview`` / ``build_commit``).
The mapping flow (date/description · amount XOR inflow/outflow · account · category) is
therefore byte-for-byte the CSV path — no per-format duplication.

Reads the workbook with ``openpyxl`` (the standard pure-Python xlsx reader, already a project
dependency). ``read_only`` + ``data_only`` so a formula cell yields its cached value and a big
sheet streams. Idempotency reuses the CSV row-hash scheme under the ``xlsx`` namespace
(``external_source="xlsx"`` + ``external_ref="<sha-of-row>"``), so a re-import dedups exactly.
Money/base figures are NEVER fabricated
— the ledger recomputes the base from the native leg.
"""

from __future__ import annotations

import re
import unicodedata

from backend.services import import_csv
from backend.services.import_common import (
    ImportError_,
    PreviewResult,
    build_commit,
    build_preview,
)
from backend.services.xlsx_open import load_values_workbook

_XLSX_PREFIX = "xlsx"
# The external_source namespace every XLSX-imported draft carries (== ``_XLSX_PREFIX``); the
# actual stamping happens in ``import_csv`` via the shared ``prefix`` param.
EXTERNAL_SOURCE = _XLSX_PREFIX
# An XLSX is a ZIP (PK\x03\x04) whose archive contains the OOXML workbook part. The sniff
# checks the ZIP magic AND the workbook marker so a plain .zip isn't mistaken for a workbook.
_ZIP_MAGIC = b"PK\x03\x04"

# Maximum number of rows to scan when searching for the header.
_HEADER_SCAN_LIMIT = 40

# Keyword bonus patterns for header scoring. Matched after normalization (NFKD casefold,
# whitespace/slash collapsed). Vietnamese bank statements use these in their column labels.
_HEADER_KEYWORDS = re.compile(
    r"\b("
    r"date|day|ngay|ngày|debit|credit|amount|balance|description|transaction|"
    r"withdrawal|deposit|so tien|ghi no|ghi co|so du|noi dung|dien giai"
    r")\b"
)

# A cell is "date-like" when it looks like a date after very basic stripping.
_DATE_RE = re.compile(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}")

# Detect a purely-numeric cell (integers and decimals, possibly with commas as thousands sep).
_NUMERIC_RE = re.compile(r"^-?[\d,]+(\.\d+)?$")


class XlsxImportError(ImportError_):
    """A user-correctable problem with the XLSX upload."""


def _looks_like_xlsx(content: bytes) -> bool:
    """Cheap content sniff so the API can auto-detect format. An XLSX is a ZIP whose central
    directory names the OOXML workbook part — we look for the ZIP magic plus the
    ``xl/workbook.xml`` marker (present in the archive bytes), not just any ZIP."""
    if not content.startswith(_ZIP_MAGIC):
        return False
    return b"xl/workbook.xml" in content[:8192] or b"[Content_Types].xml" in content[:8192]


# ── parsing ────────────────────────────────────────────────────────────────────────


def _cell_str(value: object) -> str:
    """Render a cell value as the string a CSV row would carry. Excel dates arrive as
    ``datetime``; emit ISO ``YYYY-MM-DD`` (date-only) so the ledger sees a clean date. Numbers
    stringify plainly; ``None`` → ``""``."""
    if value is None:
        return ""
    # datetime / date — emit an ISO date (the time component, if any, is dropped).
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso().split("T", 1)[0]
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_label(label: str) -> str:
    """Normalize a column label for fuzzy matching: NFKD decompose, strip combining marks,
    casefold, collapse whitespace/newlines/'/' runs to single spaces, strip edges."""
    nfkd = unicodedata.normalize("NFKD", label)
    stripped = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    casefolded = stripped.casefold()
    # Collapse whitespace, newlines, and '/' sequences to a single space.
    collapsed = re.sub(r"[\s/]+", " ", casefolded).strip()
    return collapsed


def _score_header_row(cells: list[str]) -> float:
    """Score a candidate row as a header. Higher = more likely to be the column-name row.

    Scoring:
    - +2 per non-empty cell that is a non-numeric, non-date string
    - +3 bonus per cell whose normalized label matches a known header keyword
    - +1 for distinctness (all non-empty values are unique)
    - -2 per cell that looks like a pure date (data-row indicator)
    - -1 per cell that looks like a pure number (data-row indicator)
    """
    score = 0.0
    non_empty = [c for c in cells if c]
    if not non_empty:
        return -1.0

    labels_seen: set[str] = set()
    for cell in non_empty:
        norm = _normalize_label(cell)
        if _DATE_RE.match(cell):
            score -= 2
        elif _NUMERIC_RE.match(cell.replace(",", "")):
            score -= 1
        else:
            score += 2
            if _HEADER_KEYWORDS.search(norm):
                score += 3
        labels_seen.add(norm)

    # Bonus for all distinct labels (headers are unique; data rows often repeat patterns).
    if len(labels_seen) == len(non_empty):
        score += 1

    return score


def _find_header_row(
    all_rows: list[list[str]],
) -> tuple[int, list[str]]:
    """Scan the first ``_HEADER_SCAN_LIMIT`` rows and return ``(index, header_cells)`` for the
    best-scoring candidate. Falls back to the first non-empty row when nothing scores above 0
    (keeps existing simple files working)."""
    best_idx = -1
    best_score = -1.0
    best_cells: list[str] = []
    first_nonempty_idx = -1
    first_nonempty_cells: list[str] = []

    for i, cells in enumerate(all_rows[:_HEADER_SCAN_LIMIT]):
        if not any(cells):
            continue
        if first_nonempty_idx < 0:
            first_nonempty_idx = i
            first_nonempty_cells = cells
        score = _score_header_row(cells)
        if score > best_score:
            best_score = score
            best_idx = i
            best_cells = cells

    if best_score <= 0 and first_nonempty_idx >= 0:
        return first_nonempty_idx, first_nonempty_cells
    if best_idx < 0 and first_nonempty_idx >= 0:
        return first_nonempty_idx, first_nonempty_cells
    return best_idx, best_cells


def parse_xlsx(content: bytes, password: str | None = None) -> tuple[list[str], list[dict]]:
    """Decode the first worksheet into ``(columns, rows)`` — the SAME shape
    ``import_csv.parse_csv`` returns (columns = the header row, rows = list of dicts keyed by
    header). Raises XlsxImportError on an empty / unreadable / header-less workbook. Pure — no
    ledger access.

    A password-protected workbook is decrypted first (``services.office_crypt``, shared with
    ``api/upload.py`` and ``services/statements/parser.py``) instead of reaching ``openpyxl``
    and surfacing a raw ``zipfile.BadZipFile`` message with no hint it's a password problem."""
    if not content:
        raise XlsxImportError("The file is empty.")
    try:
        import openpyxl  # noqa: F401 — availability check; the load is xlsx_open's
    except ImportError:  # pragma: no cover - openpyxl is a declared dependency
        raise XlsxImportError("XLSX support requires the openpyxl package.")

    from backend.services.office_crypt import decrypt_office

    try:
        content, _ = decrypt_office(
            content, password, resend_hint="Provide the file's password and try again."
        )
    except ValueError as exc:
        raise XlsxImportError(str(exc))

    try:
        wb = load_values_workbook(content)
    except Exception as exc:  # openpyxl raises a variety of types on a bad file
        raise XlsxImportError(f"Could not read the Excel file: {exc}")

    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            raise XlsxImportError("The workbook has no sheets.")

        # Collect all raw rows first (up to _HEADER_SCAN_LIMIT for the scan + the rest for data).
        all_raw: list[list[str]] = []
        for raw_row in ws.iter_rows(values_only=True):
            all_raw.append([_cell_str(c) for c in raw_row])
    finally:
        wb.close()

    if not all_raw:
        raise XlsxImportError("No header row found — the first row must name the columns.")

    header_idx, raw_header = _find_header_row(all_raw)
    if header_idx < 0:
        raise XlsxImportError("No header row found — the first row must name the columns.")

    # Trim trailing all-empty header columns (Excel pads sparse sheets).
    while raw_header and raw_header[-1] == "":
        raw_header.pop()

    # Leading all-empty columns — tolerate them (VCB has an empty first column).
    # Build header list preserving the column positions (including leading empties as "") so
    # data cells stay index-aligned; we'll strip the leading empty names after alignment.
    header = raw_header

    columns = [c.strip() for c in header if c is not None]
    if not any(columns):
        raise XlsxImportError("No header row found — the first row must name the columns.")

    # Build data rows from every row AFTER the detected header row.
    rows: list[dict] = []
    for raw_row in all_raw[header_idx + 1 :]:
        cells = raw_row
        if not any(cells):
            continue
        row = {header[i]: (cells[i] if i < len(cells) else "") for i in range(len(header))}
        rows.append(row)

    return columns, rows


def detect_headers(content: bytes, password: str | None = None) -> list[str]:
    """Just the column names, for the UI's mapping form (header auto-detect / column probe)."""
    columns, _ = parse_xlsx(content, password=password)
    return columns


# ── public API: preview + commit (delegates the mapping core to import_csv) ─────────


def preview_import(content: bytes, mapping: dict, account_id: str | None = None) -> PreviewResult:
    """Dry-run: read the sheet → map (via the shared CSV mapping core) → report what WOULD
    import. Writes nothing. XLSX uses the SAME column mapping as CSV."""
    columns, rows = parse_xlsx(content)
    results = import_csv.map_sheet_rows(columns, rows, mapping, account_id, _XLSX_PREFIX)
    return build_preview(columns, len(rows), results)


def commit_import(content: bytes, mapping: dict, account_id: str | None = None) -> dict:
    """Read the sheet → map → persist the non-duplicate drafts through the ONE ledger choke
    point (``bulk_create_transactions`` → spend-rules + categorization + base math apply).
    Idempotent via the ``xlsx`` row-hash external_ref. Returns ``{created, duplicates,
    skipped, total_rows}``."""
    columns, rows = parse_xlsx(content)
    results = import_csv.map_sheet_rows(columns, rows, mapping, account_id, _XLSX_PREFIX)
    return build_commit(len(rows), results)
