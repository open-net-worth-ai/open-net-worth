"""A broker's own export generator writes stylesheets openpyxl refuses (one out-of-spec
attribute fails the WHOLE workbook: "could not read stylesheet"). Every value is still
readable, so every xlsx reader must still read it — the chat upload refused it outright."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import openpyxl
import pytest

from backend.services.xlsx_open import load_values_workbook


def _xlsx(*, break_styles: bool) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Symbol", "Quantity", "Price", "Date"])
    ws.append(["ACME", 10, 12.5, dt.datetime(2026, 1, 2)])
    ws["D2"].number_format = "yyyy-mm-dd"
    buf = io.BytesIO()
    wb.save(buf)
    if not break_styles:
        return buf.getvalue()
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zo:
        for name in src.namelist():
            data = src.read(name)
            if name == "xl/styles.xml":
                data = data.replace(b'<family val="2"/>', b'<family val="99"/>')
            zo.writestr(name, data)
    return out.getvalue()


def _rows(wb) -> list[tuple]:
    return [r for s in wb.sheetnames for r in wb[s].iter_rows(values_only=True)]


def test_openpyxl_itself_refuses_the_fixture():
    """Guards the fixture: if openpyxl ever accepts it, the tests below prove nothing."""
    with pytest.raises(Exception):
        openpyxl.load_workbook(io.BytesIO(_xlsx(break_styles=True)), read_only=True)


def test_a_refused_stylesheet_is_repaired_and_dates_survive():
    rows = _rows(load_values_workbook(_xlsx(break_styles=True)))
    assert rows[1][:3] == ("ACME", 10, 12.5)
    assert rows[1][3] == dt.datetime(2026, 1, 2)  # numFmts/cellXfs kept → still a date


def test_a_clean_workbook_is_untouched():
    assert _rows(load_values_workbook(_xlsx(break_styles=False)))[1][0] == "ACME"


def test_a_file_that_is_not_a_workbook_still_raises():
    with pytest.raises(Exception):
        load_values_workbook(b"not a zip")


def test_chat_upload_reads_a_broker_export_with_a_refused_stylesheet():
    from backend.services.attachment_parse import parse_upload

    entry = parse_upload(
        "Positions_export.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _xlsx(break_styles=True),
    )
    assert "ACME" in entry["text"]
    assert entry["format"] == "xlsx"


def test_the_structured_importer_reads_it_too():
    from backend.services.import_xlsx import parse_xlsx

    result = parse_xlsx(_xlsx(break_styles=True))
    assert "ACME" in repr(result)
