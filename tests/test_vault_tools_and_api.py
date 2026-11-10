"""Vault AI tools, the REST surface, and the Private guard on every model-facing read.

Private is fail-closed: a file the user marked Private is listed (Lucius may know it exists)
but its CONTENTS never reach a model — not through read_documents, read_attachments, an
import/equity-PDF parse, a receipt promotion, or a re-attached ref on the current turn. The
user's own views (thumbnail, download) are unaffected. Synthetic data only.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import registry
from backend.core.errors import NotFoundError, ValidationError
from backend.services import vault, vault_changes


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, db_isolated):
    import backend.db.artifact_store as art_mod
    import backend.db.attachment_store as att_mod
    import backend.db.receipt_store as rcpt_mod
    from backend.services import attachments as attachments_svc

    for mod, attr, name in (
        (att_mod, "ATTACHMENTS_DIR", "attachments"),
        (rcpt_mod, "RECEIPTS_DIR", "receipts"),
        (art_mod, "ARTIFACTS_DIR", "artifacts"),
    ):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(mod, attr, path)
    monkeypatch.setattr(art_mod, "DATA_DIR", tmp_path)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


def _no_reader(monkeypatch):
    async def must_not_call(*a, **k):
        raise AssertionError("a Private file must never reach the image reader")

    monkeypatch.setattr("backend.services.image_extract.extract_image_text", must_not_call)
    monkeypatch.setattr(
        "backend.services.external_context.read_attachment_extraction", must_not_call
    )


def _private_file(body: bytes = b"account 99-1234 password hunter2") -> dict:
    return vault_changes.add_file("will.txt", "text/plain", body, private=True)


async def _call(name: str, args: dict) -> dict:
    text, proposed = await registry.dispatch(name, args)
    assert proposed is None
    return json.loads(text)


class TestPrivateGuards:
    async def test_read_attachments_refuses_a_private_file(self, monkeypatch):
        _no_reader(monkeypatch)
        item = _private_file()
        data = await _call("read_attachments", {"attachment_ids": [item["source_id"]]})
        result = data["results"][0]
        assert (result["ok"], result["private"]) == (False, True)
        assert "hunter2" not in json.dumps(data)

    def test_model_getters_refuse_but_the_users_own_view_still_works(self):
        from backend.services import attachments

        aid = _private_file()["source_id"]
        assert attachments.get_attachment_for_model(aid) is None
        assert attachments.get_attachment_import_source(aid) is None
        assert attachments.get_attachment(aid) is not None
        assert attachments.read_attachment_bytes(aid)

    def test_an_import_from_a_private_attachment_is_refused(self):
        from backend.ai.tools.imports import _resolve_bytes
        from backend.services.import_common import ImportError_

        aid = _private_file(b"date,amount\n2026-01-01,5\n")["source_id"]
        with pytest.raises(ImportError_, match="Private"):
            _resolve_bytes(None, None, aid)

    def test_an_equity_pdf_parse_from_a_private_attachment_is_refused(self):
        from backend.ai.tools.equity import _resolve_pdf_bytes

        aid = _private_file()["source_id"]
        assert "Private" in _resolve_pdf_bytes({"attachment_ref": aid})

    def test_a_private_file_cannot_be_copied_into_a_readable_receipt(self):
        from backend.core.errors import ValidationError
        from backend.services.receipts import apply_receipt_change

        aid = _private_file()["source_id"]
        with pytest.raises(ValidationError, match="Private"):
            apply_receipt_change({"type": "promote_from_attachment", "attachment_id": aid})

    async def test_a_private_ref_on_the_current_turn_is_not_expanded(self, monkeypatch):
        _no_reader(monkeypatch)
        from backend.ai.attachments import resolve_attachment_refs_for_dispatch

        aid = _private_file()["source_id"]
        content = [
            {"type": "attachment_ref", "attachment_id": aid, "filename": "will.txt"},
            {"type": "text", "text": "what is in this?"},
        ]
        resolved = await resolve_attachment_refs_for_dispatch(content, model="")
        assert "hunter2" not in json.dumps(resolved)
        assert any("marked Private" in p.get("text", "") for p in resolved)

    async def test_diff_statement_refuses_a_private_ref(self):
        data = await _call(
            "diff_statement", {"ref_id": _private_file()["source_id"], "account_id": "acc_x"}
        )
        assert (data["ok"], data["private"]) == (False, True)


class TestTools:
    async def test_list_documents_is_metadata_only_and_paged(self):
        for n in range(3):
            vault_changes.add_file(f"f{n}.txt", "text/plain", b"body", year=2025)
        _private_file()
        data = await _call("list_documents", {"limit": 2})
        assert (data["total"], data["count"], data["next_offset"]) == (4, 2, 2)
        assert all("file_url" not in i for i in data["items"])
        rest = await _call("list_documents", {"limit": 2, "offset": 2})
        assert rest["count"] == 2 and "next_offset" not in rest
        assert [i["private"] for i in rest["items"]].count(True) + [
            i["private"] for i in data["items"]
        ].count(True) == 1
        of_2025 = await _call("list_documents", {"year": 2025})
        assert of_2025["total"] == 3

    async def test_read_documents_reads_text_and_refuses_private(self, monkeypatch):
        _no_reader(monkeypatch)
        doc = vault_changes.add_file("a.txt", "text/plain", b"premium 1200 per year")
        secret = _private_file()
        data = await _call("read_documents", {"document_ids": [doc["id"], secret["id"], "file-x"]})
        first, second, third = data["results"]
        assert first["ok"] is True and "premium 1200" in first["text"]
        assert (second["ok"], second["private"]) == (False, True)
        assert third["ok"] is False and "list_documents" in third["error"]
        assert "hunter2" not in json.dumps(data)

    async def test_read_documents_pages_a_long_document(self):
        doc = vault_changes.add_file("long.txt", "text/plain", b"x" * 30_000)
        first = await _call("read_documents", {"document_ids": [doc["id"]]})
        page = first["results"][0]
        assert page["text_chars_total"] >= 30_000 and page["next_offset"] == len(page["text"])
        second = await _call(
            "read_documents", {"document_ids": [doc["id"]], "offset": page["next_offset"]}
        )
        assert "next_offset" not in second["results"][0]

    async def test_read_documents_bounds_the_batch(self):
        data = await _call("read_documents", {"document_ids": [f"file-att_{n}" for n in range(6)]})
        assert data["ok"] is False and "limit" in data["error"]

    async def test_save_to_documents_keeps_an_artifact_or_a_chat_file(self):
        from backend.db.artifact_store import ArtifactStore
        from backend.services.attachment_ingress import persist_inbound_file

        art = ArtifactStore().create("Plan", "plan.html", "html", "<p>plan</p>", "s1")["id"]
        saved = await _call("save_to_documents", {"artifact_id": art})
        assert saved["item"]["kind"] == "artifact"
        aid = persist_inbound_file("p.txt", "text/plain", b"policy", source_channel="chat:web")[
            "attachment_id"
        ]
        kept = await _call("save_to_documents", {"attachment_id": aid, "year": 2024})
        assert (kept["item"]["year"], kept["item"]["origin"]) == (2024, "chat")

    async def test_save_to_documents_refuses_ambiguity_and_can_never_unmark_private(self):
        assert (await _call("save_to_documents", {}))["ok"] is False
        both = await _call("save_to_documents", {"artifact_id": "a", "attachment_id": "b"})
        assert both["ok"] is False
        secret = _private_file()
        out = await _call(
            "save_to_documents", {"attachment_id": secret["source_id"], "private": False}
        )
        assert out["ok"] is False and "Only the user" in out["error"]
        assert vault.is_private_attachment(secret["source_id"]) is True

    async def test_remove_from_documents_unfiles_an_artifact_without_deleting_it(self):
        from backend.db.artifact_store import ArtifactStore

        old = ArtifactStore().create("Week 39", "w39.html", "html", "<p>old</p>", "s1")["id"]
        await _call("save_to_documents", {"artifact_id": old})
        item_id = vault.item_id("artifact", old)
        out = await _call("remove_from_documents", {"id": item_id})
        assert out == {"ok": True, "id": item_id, "action": "removed"}
        assert ArtifactStore().find_by_id(old)["saved"] is False  # out of Documents…
        assert ArtifactStore().find_by_id(old) is not None  # …but the report still exists
        again = await _call("save_to_documents", {"artifact_id": old})  # and can come back
        assert again["item"]["kind"] == "artifact"
        by_artifact = await _call("remove_from_documents", {"artifact_id": old})
        assert by_artifact["ok"] is True

    async def test_remove_from_documents_unfiles_a_chat_file_but_keeps_it(self):
        from backend.services.attachment_ingress import persist_inbound_file

        aid = persist_inbound_file("p.txt", "text/plain", b"policy", source_channel="chat:web")[
            "attachment_id"
        ]
        await _call("save_to_documents", {"attachment_id": aid})
        out = await _call("remove_from_documents", {"id": vault.item_id("file", aid)})
        assert out["ok"] is True and out["action"] == "removed"
        from backend.db.attachment_store import AttachmentStore

        assert AttachmentStore().find_by_id(aid) is not None  # still in its chat

    async def test_remove_from_documents_never_deletes(self):
        """Taking a file added straight to Documents out of it IS deleting it — refused."""
        filed = vault_changes.add_file("deed.txt", "text/plain", b"deed")
        out = await _call("remove_from_documents", {"id": filed["id"]})
        assert out["ok"] is False and "only the user" in out["error"]
        assert vault.get_item(filed["id"])["id"] == filed["id"]  # untouched
        assert (await _call("remove_from_documents", {}))["ok"] is False
        both = await _call("remove_from_documents", {"id": "x", "artifact_id": "y"})
        assert both["ok"] is False


XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def client():
    from backend.api.vault import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestApi:
    def test_add_list_edit_and_delete_a_file(self, client):
        up = client.post(
            "/api/vault/files",
            files={"file": ("w2.txt", io.BytesIO(b"wages"), "text/plain")},
            data={"year": "2025", "title": "W-2"},
            headers=XHR,
        )
        assert up.status_code == 200
        item = up.json()["item"]
        assert (item["title"], item["year"]) == ("W-2", 2025)

        listing = client.get("/api/vault").json()
        assert listing["counts"]["file"] == 1 and listing["items"][0]["id"] == item["id"]

        patched = client.patch(f"/api/vault/items/{item['id']}", json={"private": True})
        assert patched.status_code == 200 and patched.json()["item"]["private"] is True
        assert (
            client.patch(f"/api/vault/items/{item['id']}", json={"private": None}).status_code
            == 422
        )
        assert client.patch(f"/api/vault/items/{item['id']}", json={"size": 1}).status_code == 422

        gone = client.delete(f"/api/vault/items/{item['id']}")
        assert gone.json()["action"] == "deleted"
        assert client.get("/api/vault").json()["items"] == []

    def test_one_item_by_id_for_a_single_file_preview(self, client):
        """The import review previews its statement beside the rows without listing every
        document: one item by its Vault id, and a clean 404 for one that doesn't exist."""
        from backend.db.statement_store import StatementStore

        stmt = StatementStore().create(
            "aug.csv", "text/csv", b"Date,Amount\n", {"kind": "text", "text": "x"}
        )
        got = client.get(f"/api/vault/items/statement-{stmt['id']}")
        assert got.status_code == 200
        item = got.json()["item"]
        assert (item["kind"], item["filename"]) == ("statement", "aug.csv")
        assert item["file_url"] == f"/api/statements/{stmt['id']}/file"
        assert client.get("/api/vault/items/statement-nope").status_code == 404

    def test_rename(self, client):
        up = client.post(
            "/api/vault/files",
            files={"file": ("scan_7.txt", io.BytesIO(b"deed"), "text/plain")},
            headers=XHR,
        )
        vid = up.json()["item"]["id"]
        renamed = client.put(f"/api/vault/items/{vid}/name", json={"title": "House deed"})
        assert renamed.status_code == 200 and renamed.json()["item"]["title"] == "House deed"
        assert client.put(f"/api/vault/items/{vid}/name", json={"size": 1}).status_code == 422
        assert (
            client.put("/api/vault/items/file-att_missing/name", json={"title": "x"}).status_code
            == 404
        )

    def test_bad_input_maps_to_http_errors(self, client):
        bad_year = client.post(
            "/api/vault/files",
            files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
            data={"year": "1492"},
            headers=XHR,
        )
        assert bad_year.status_code == 422
        assert client.delete("/api/vault/items/nonsense").status_code == 422
        assert client.delete("/api/vault/items/file-att_missing").status_code == 404
        assert client.put("/api/vault/artifacts/art_missing").status_code == 404

    def test_save_and_unsave_an_artifact(self, client):
        from backend.db.artifact_store import ArtifactStore

        art = ArtifactStore().create("Plan", "plan.html", "html", "<p>plan</p>", "s1")["id"]
        assert client.put(f"/api/vault/artifacts/{art}").json()["item"]["source_id"] == art
        assert client.get("/api/vault").json()["counts"]["artifact"] == 1
        assert client.delete(f"/api/vault/artifacts/{art}").json() == {
            "artifact_id": art,
            "saved": False,
        }
        assert client.get("/api/vault").json()["counts"]["artifact"] == 0


class TestSecurityReviewFixes:
    """Security review, 2026-09-26 — each test pins one finding's fix."""

    def test_an_upload_without_the_csrf_header_is_refused_and_stores_nothing(self, client):
        r = client.post(
            "/api/vault/files", files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")}
        )
        assert r.status_code == 403
        assert vault.list_items() == []

    def test_an_oversize_upload_is_refused(self, client, monkeypatch):
        import backend.api.vault as vault_api

        monkeypatch.setattr(vault_api, "_MAX_UPLOAD_BYTES", 8)
        r = client.post(
            "/api/vault/files",
            files={"file": ("a.txt", io.BytesIO(b"123456789"), "text/plain")},
            headers=XHR,
        )
        assert r.status_code == 413

    async def test_lucius_cannot_delete_a_kept_document_and_delete_all_keeps_them(self):
        from backend.services import attachments
        from backend.services.attachment_ingress import persist_inbound_file

        kept = vault_changes.add_file("will.txt", "text/plain", b"x")["source_id"]
        loose = persist_inbound_file("a.txt", "text/plain", b"y", source_channel="chat:web")
        out = await _call("delete_upload", {"ref_id": kept})
        assert out["ok"] is False and "Documents" in out["error"]
        assert attachments.delete_all_attachments() == 1
        assert attachments.get_attachment_meta(kept) is not None
        assert attachments.get_attachment_meta(loose["attachment_id"]) is None

    async def test_a_private_files_content_derived_summary_is_hidden_then_restored(self):
        from backend.db.attachment_store import AttachmentStore

        # No header row: the parser's "columns" summary is the first DATA row.
        body = b"4111-1111-1111-1111,Jane Doe\n5500-0000-0000-0004,John Roe\n"
        item = vault_changes.add_file("cards.csv", "text/csv", body, private=True)
        aid = item["source_id"]
        assert AttachmentStore().find_meta(aid)["summary"] == vault.PRIVATE_SUMMARY
        listed = (await _call("get_uploads", {}))["uploads"]
        assert "4111" not in json.dumps(listed)
        assert listed[0]["private"] is True and listed[0]["in_documents"] is True
        vault_changes.update_file(item["id"], {"private": False})
        assert "4111" in AttachmentStore().find_meta(aid)["summary"]

    async def test_a_receipt_copied_from_a_file_later_marked_private_is_private(self, monkeypatch):
        _no_reader(monkeypatch)
        from backend.services.attachment_ingress import persist_inbound_file
        from backend.services.receipts import apply_receipt_change

        aid = persist_inbound_file(
            "r.txt", "text/plain", b"total 99 hunter2", source_channel="chat:web"
        )["attachment_id"]
        receipt = apply_receipt_change({"type": "promote_from_attachment", "attachment_id": aid})[
            "receipt"
        ]
        vault_changes.keep_attachment(aid, private=True)
        tool = await _call("read_receipts", {"receipt_ids": [receipt["id"]]})
        assert (tool["results"][0]["ok"], tool["results"][0]["private"]) == (False, True)
        item = vault.get_item(f"receipt-{receipt['id']}")
        assert item["private"] is True
        assert [i["private"] for i in vault.list_items(kind="receipt")] == [True]
        read = await _call("read_documents", {"document_ids": [item["id"]]})
        assert read["results"][0]["private"] is True
        assert "hunter2" not in json.dumps([tool, read])

    def test_a_failed_keep_leaves_no_half_kept_file(self, monkeypatch):
        from backend.services.attachments import list_attachments

        def boom(*a, **k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(vault_changes, "_keep", boom)
        with pytest.raises(RuntimeError):
            vault_changes.add_file("will.txt", "text/plain", b"x", private=True)
        assert list_attachments() == []

    def test_a_statement_with_a_non_latin_name_downloads(self):
        from backend.api.import_api import router as import_router
        from backend.db.statement_store import StatementStore

        stmt = StatementStore().create(
            "sao-kê-tháng-8.pdf", "application/pdf", b"%PDF-1.4", {"kind": "text", "text": "x"}
        )
        app = FastAPI()
        app.include_router(import_router)
        r = TestClient(app).get(f"/api/statements/{stmt['id']}/file")
        assert r.status_code == 200
        assert "filename*=UTF-8''" in r.headers["content-disposition"]


def _pdf(pages: int) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page().insert_text((72, 72), f"Synthetic page {n + 1}")
    out = doc.tobytes()
    doc.close()
    return out


class TestDeleteFileAndPreview:
    """Follow-up (2026-09-27): delete a kept chat file for good from Documents, and show a
    PDF item's pages."""

    def test_delete_file_removes_a_kept_chat_file_for_good(self):
        from backend.services.attachment_ingress import persist_inbound_file
        from backend.services.attachments import get_attachment_meta

        aid = persist_inbound_file("p.txt", "text/plain", b"x", source_channel="chat:web")[
            "attachment_id"
        ]
        vault_changes.keep_attachment(aid)
        assert vault_changes.delete_file(f"file-{aid}") == {
            "id": f"file-{aid}",
            "action": "deleted",
        }
        assert get_attachment_meta(aid) is None

    def test_delete_file_is_only_for_files(self, client):
        from backend.core.errors import ValidationError
        from backend.db.statement_store import StatementStore

        stmt = StatementStore().create("s.csv", "text/csv", b"d,a\n", {"kind": "text", "text": "x"})
        with pytest.raises(ValidationError):
            vault_changes.delete_file(f"statement-{stmt['id']}")
        r = client.delete(f"/api/vault/items/statement-{stmt['id']}?delete_file=true")
        assert r.status_code == 422

    def test_delete_file_through_the_api(self, client):
        item = vault_changes.add_file("w.txt", "text/plain", b"x")
        r = client.delete(f"/api/vault/items/{item['id']}?delete_file=true")
        assert r.json() == {"id": item["id"], "action": "deleted"}
        assert vault.list_items() == []

    def test_a_pdfs_pages_are_drawn_one_at_a_time(self):
        from backend.services import document_preview

        item = vault_changes.add_file("two.pdf", "application/pdf", _pdf(2), private=True)
        png, count = document_preview.page_image(item["id"], 2)
        assert png.startswith(b"\x89PNG") and count == 2  # Private hides it from Lucius only
        with pytest.raises(NotFoundError):
            document_preview.page_image(item["id"], 3)
        text = vault_changes.add_file("a.txt", "text/plain", b"x")
        with pytest.raises(ValidationError):
            document_preview.page_image(text["id"], 1)

    def test_a_statement_pdf_previews_through_the_api(self, client):
        from backend.db.statement_store import StatementStore

        stmt = StatementStore().create(
            "card.pdf", "application/pdf", _pdf(3), {"kind": "text", "text": "x"}
        )
        r = client.get(f"/api/vault/items/statement-{stmt['id']}/pages/1")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers["x-page-count"] == "3"
        assert r.content.startswith(b"\x89PNG")
        assert client.get(f"/api/vault/items/statement-{stmt['id']}/pages/9").status_code == 404

    def test_a_photo_gets_a_small_thumbnail_and_other_kinds_do_not(self, client):
        import io

        from PIL import Image

        from backend.services import document_preview

        buf = io.BytesIO()
        Image.new("RGB", (1600, 1200), "white").save(buf, format="JPEG")
        photo = vault_changes.add_file("deed.jpg", "image/jpeg", buf.getvalue())
        jpeg = document_preview.thumbnail(photo["id"])
        with Image.open(io.BytesIO(jpeg)) as small:
            assert small.format == "JPEG" and max(small.size) <= 160
        r = client.get(f"/api/vault/items/{photo['id']}/thumb")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        pdf = vault_changes.add_file("one.pdf", "application/pdf", _pdf(1))
        assert client.get(f"/api/vault/items/{pdf['id']}/thumb").status_code == 422
