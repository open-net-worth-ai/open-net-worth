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
# A rebuild decompresses every part. The upload itself is capped at 25 MB compressed; these bound
# what a crafted zip can make us inflate and copy (the sizes are the zip's own claims, but
# ``ZipFile.read`` refuses a part that inflates past what it declared).
_MAX_INFLATED_BYTES = 64 * 1024 * 1024
_MAX_PARTS = 2000
# The repair splices the stylesheet; a real one is kilobytes. Past this we skip straight to
# dropping it.
_MAX_STYLES_BYTES = 2 * 1024 * 1024

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
    try:
        repairs = list(_repairs(content))
    except Exception:  # noqa: BLE001 — a zip that lies about itself is still the ORIGINAL failure
        raise original from None
    for label, rebuilt in repairs:
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
        infos = zf.infolist()
        if len(infos) > _MAX_PARTS or _STYLES not in zf.namelist():
            return
        if sum(i.file_size for i in infos) > _MAX_INFLATED_BYTES:
            return
        parts = {i.filename: zf.read(i) for i in infos}
    styles = parts[_STYLES]
    if len(styles) <= _MAX_STYLES_BYTES:
        repaired = _sanitized_styles(styles.decode("utf-8", errors="replace"))
        yield "repairing its stylesheet", _rezip(parts, repaired.encode("utf-8"))
    yield "dropping its stylesheet", _rezip(parts, None)


def _sanitized_styles(xml: str) -> str:
    """Swap each cosmetic block for its minimal form — plain ``str.find`` splicing, linear in the
    size of the (untrusted) stylesheet; a backtracking regex here was quadratic."""
    for tag, minimal in _MINIMAL_BLOCKS.items():
        xml = _replace_block(xml, tag, minimal)
    # Every cell format now points at the single font/fill/border the minimal blocks define.
    return _ID_ATTR.sub(r'\1="0"', xml)


# Linear: an attribute name, then a quoted run of digits — no nested or overlapping repetition.
_ID_ATTR = re.compile(r'\b(fontId|fillId|borderId|xfId)="[0-9]{1,9}"')


def _replace_block(xml: str, tag: str, minimal: str) -> str:
    start = _find_open_tag(xml, tag)
    if start < 0:
        return xml
    head_end = xml.find(">", start)
    if head_end < 0:
        return xml
    if xml[head_end - 1] == "/":
        return xml[:start] + minimal + xml[head_end + 1 :]
    close = xml.find(f"</{tag}>", head_end)
    if close < 0:
        return xml
    return xml[:start] + minimal + xml[close + len(tag) + 3 :]


def _find_open_tag(xml: str, tag: str) -> int:
    """Index of ``<tag`` as a whole element name (``<fonts`` not ``<fontsX``), or -1."""
    needle = f"<{tag}"
    at = xml.find(needle)
    while at >= 0:
        nxt = xml[at + len(needle) : at + len(needle) + 1]
        if nxt in (">", "/", " ", "\t", "\n", "\r"):
            return at
        at = xml.find(needle, at + 1)
    return -1


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
