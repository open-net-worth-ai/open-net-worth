"""A refused upload names what IS supported, from one list, with a real 415."""

from __future__ import annotations

import pytest

from backend.services.attachment_parse import (
    SUPPORTED_UPLOADS,
    UnsupportedFileError,
    parse_upload,
)


def test_an_unsupported_type_names_its_extension_and_the_supported_list():
    with pytest.raises(UnsupportedFileError) as ei:
        parse_upload("letter.docx", "application/msword", b"PK")
    msg = str(ei.value)
    assert "a .docx file" in msg
    assert SUPPORTED_UPLOADS in msg
    assert "Excel (.xlsx, .xls)" in msg


def test_a_txt_with_no_declared_type_is_read_not_refused():
    entry = parse_upload("notes.txt", "", b"hello")
    assert entry["kind"] == "text"


def test_an_unreadable_excel_file_says_what_to_do_not_the_library_error():
    with pytest.raises(ValueError) as ei:
        parse_upload("broken.xlsx", "", b"not a workbook")
    msg = str(ei.value)
    assert "save it again as .xlsx" in msg
    assert "zip" not in msg.lower()  # never the reader's own exception text


def test_the_chat_upload_endpoint_answers_415_with_the_list(monkeypatch):
    import asyncio
    import io

    from fastapi import HTTPException, UploadFile

    from backend.api import upload

    def refuse(*_a, **_k):
        raise UnsupportedFileError("Lucius can't read a .docx file. Supported: x.")

    monkeypatch.setattr(upload, "persist_inbound_file", refuse)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(upload.upload_file(UploadFile(io.BytesIO(b"PK"), filename="a.docx")))
    assert ei.value.status_code == 415
    assert "Supported" in ei.value.detail
