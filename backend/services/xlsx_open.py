"""Open an .xlsx for READING VALUES, tolerating a stylesheet openpyxl refuses.

openpyxl validates ``xl/styles.xml`` strictly and refuses the WHOLE workbook over a single
out-of-spec value (a font ``family`` of 99, say: "Unable to read workbook: could not read
stylesheet"). Exports written by a broker's or bank's own generator, not by Excel, trip this
routinely, and every cell is still perfectly readable. We only ever read values, so on that
refusal we retry with a repaired stylesheet, and failing that with none at all.

The repair keeps ``numFmts`` and ``cellXfs`` (that is how openpyxl knows a number is a DATE) and
replaces the cosmetic blocks (fonts, fills, borders, named styles, dxfs) with minimal valid ones.
The last resort drops the stylesheet, so date cells read as Excel serial numbers: a degraded
but honest read, which beats refusing the file.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile

logger = logging.getLogger(__name__)

_STYLES = "xl/styles.xml"
# A rebuild decompresses every part; refuse to do that for a zip that inflates past this
# (the upload itself is capped at 25 MB compressed).
_MAX_INFLATED_BYTES = 200 * 1024 * 1024

_MINIMAL_BLOCKS = {
    "fonts": '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>',
    "fills": '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>',
    "borders": '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/>'
    "</border></borders>",
    "cellStyleXfs": '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
    'borderId="0"/></cellStyleXfs>',
    "cellStyles": '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
    "</cellStyles>",
    "dxfs": '<dxfs count="0"/>',
}


def load_values_workbook(content: bytes):
    """``openpyxl.load_workbook(read_only=True, data_only=True)`` with a stylesheet fallback.

    Raises the ORIGINAL error when the file is broken beyond its stylesheet."""
    try:
        return _load(content)
    except Exception as exc:  # noqa: BLE001 — openpyxl raises a zoo of types for one bad attribute
        original = exc
    for label, rebuilt in _repairs(content):
        try:
            wb = _load(rebuilt)
        except Exception:  # noqa: BLE001
            continue
        logger.info("xlsx: read after %s (openpyxl refused: %s)", label, original)
        return wb
    raise original


def _load(content: bytes):
    import openpyxl  # lazily: a non-spreadsheet path never pays for it

    return openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)


def _repairs(content: bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return
    with zf:
        names = zf.namelist()
        if _STYLES not in names:
            return
        if sum(i.file_size for i in zf.infolist()) > _MAX_INFLATED_BYTES:
            return
        parts = {n: zf.read(n) for n in names}
    styles = parts[_STYLES].decode("utf-8", errors="replace")
    yield "repairing its stylesheet", _rezip(parts, _sanitized_styles(styles).encode("utf-8"))
    yield "dropping its stylesheet", _rezip(parts, None)


def _sanitized_styles(xml: str) -> str:
    for tag, minimal in _MINIMAL_BLOCKS.items():
        pattern = re.compile(rf"<(\w+:)?{tag}\b[^>]*?(/>|>.*?</(\w+:)?{tag}>)", re.S)
        xml = pattern.sub(minimal, xml, count=1)
    # Every cell format now points at the single font/fill/border the minimal blocks define.
    return re.sub(r'\b(fontId|fillId|borderId|xfId)="\d+"', r'\1="0"', xml)


def _rezip(parts: dict[str, bytes], styles: bytes | None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for name, data in parts.items():
            if name == _STYLES:
                if styles is None:
                    continue
                data = styles
            zo.writestr(name, data)
    return out.getvalue()
