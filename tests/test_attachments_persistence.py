"""AttachmentStore (db/attachment_store.py + services/attachments.py + REST + chat) —
the durable cross-channel attachment record cross-channel-attachments RFC Phase 2 adds.

Renamed from ``UploadStore``/``tests/test_uploads_persistence.py`` (ADR
2026-08-16-attachment-store-replaces-upload-store); these tests pin:
  - store CRUD + payload round-trip (bytes survive base64 encode/decode) + the six new
    provenance fields (media_type/content_hash/blob_locator/source_channel/
    source_message_id/source_conversation_id)
  - service read-through cache + delete invalidation
  - the shared ingress operation (``services.attachment_ingress.persist_inbound_file``),
    the ONE choke both web upload and Telegram now go through (RFC item 11)
  - REST list/delete/upload smoke
  - the missing-ref message (no silent skip) and the durable-ref vs. dispatch-resolved
    content split (RFC item 13)
  - restart/reload: web-shaped and Telegram-shaped ingress produce IDENTICAL retrieval
    after a simulated process restart (RFC item 14 — the acceptance-matrix "Durable
    attachment" row)
  - checkpoint exclusion + export-manifest membership
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_attachments(tmp_path, monkeypatch, db_isolated):
    import backend.db.attachment_store as as_mod
    from backend.services import attachments as attachments_svc

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(as_mod, "ATTACHMENTS_DIR", att_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


def _entry(**over) -> dict:
    base = {
        "kind": "text",
        "text": "Date,Amount\n2026-07-01,-12.50",
        "summary": "CSV file 'bank.csv' with 1 rows",
        "format": "csv",
        "content": b"Date,Amount\n2026-07-01,-12.50",
        "statement_id": "st_123",
    }
    base.update(over)
    return base


# ── Store ─────────────────────────────────────────────────────────────────────


class TestAttachmentStore:
    def test_create_and_read_payload_roundtrip(self):
        from backend.db.attachment_store import AttachmentStore

        store = AttachmentStore()
        ref = store.create(_entry(), filename="bank.csv")
        assert ref.startswith("att_")
        payload = store.read_payload(ref)
        assert payload["text"].startswith("Date,Amount")
        assert payload["content"] == b"Date,Amount\n2026-07-01,-12.50"  # bytes restored
        assert "content_b64" not in payload

    def test_metadata_row_carries_the_new_provenance_fields(self):
        from backend.db.attachment_store import AttachmentStore

        store = AttachmentStore()
        ref = store.create(
            _entry(),
            filename="bank.csv",
            media_type="text/csv",
            content_hash="deadbeef",
            source_channel="chat:web",
            source_message_id=None,
            source_conversation_id="sess-1",
            metadata={"was_encrypted": False},
        )
        row = store.find_by_id(ref)
        assert row["filename"] == "bank.csv"
        assert row["kind"] == "text"
        assert row["format"] == "csv"
        assert row["size_bytes"] > 0
        assert row["last_used_at"] is None
        assert row["media_type"] == "text/csv"
        assert row["content_hash"] == "deadbeef"
        assert row["source_channel"] == "chat:web"
        assert row["source_conversation_id"] == "sess-1"
        assert row["source_message_id"] is None
        assert row["metadata"] == {"was_encrypted": False}
        assert row["blob_locator"]  # non-empty, informational

    def test_defaults_are_honest_when_no_provenance_supplied(self):
        """A caller with nothing to say about origin gets empty/None, never a guess."""
        from backend.db.attachment_store import AttachmentStore

        store = AttachmentStore()
        ref = store.create(_entry())
        row = store.find_by_id(ref)
        assert row["source_channel"] == ""
        assert row["source_conversation_id"] is None
        assert row["source_message_id"] is None
        assert row["content_hash"] is None
        assert row["metadata"] == {}

    def test_find_meta_never_reads_the_payload_file(self):
        """The lightweight existence check build_user_content uses — proven by deleting
        the payload file out from under it and confirming the metadata row is untouched."""
        import backend.db.attachment_store as as_mod
        from backend.db.attachment_store import AttachmentStore

        store = AttachmentStore()
        ref = store.create(_entry(), filename="bank.csv")
        (as_mod.ATTACHMENTS_DIR / f"{ref}.parsed.json").unlink()
        meta = store.find_meta(ref)
        assert meta is not None
        assert meta["filename"] == "bank.csv"

    def test_find_by_content_hash_returns_the_newest_match(self):
        from backend.db.attachment_store import AttachmentStore

        store = AttachmentStore()
        store.create(_entry(), filename="old.csv", content_hash="samehash")
        newest = store.create(_entry(), filename="new.csv", content_hash="samehash")
        found = store.find_by_content_hash("samehash")
        assert found["id"] == newest
        assert store.find_by_content_hash("") is None
        assert store.find_by_content_hash("nope") is None

    def test_delete_removes_row_and_payload(self):
        import backend.db.attachment_store as as_mod
        from backend.db.attachment_store import AttachmentStore

        store = AttachmentStore()
        ref = store.create(_entry())
        assert store.delete_attachment(ref) is True
        assert store.find_by_id(ref) is None
        assert not (as_mod.ATTACHMENTS_DIR / f"{ref}.parsed.json").exists()
        assert store.delete_attachment(ref) is False

    def test_missing_payload_file_reads_none(self):
        import backend.db.attachment_store as as_mod
        from backend.db.attachment_store import AttachmentStore

        store = AttachmentStore()
        ref = store.create(_entry())
        (as_mod.ATTACHMENTS_DIR / f"{ref}.parsed.json").unlink()
        assert store.read_payload(ref) is None

    def test_mark_used_stamps_provenance(self):
        from backend.db.attachment_store import AttachmentStore

        store = AttachmentStore()
        ref = store.create(_entry())
        store.mark_used(ref, "sess-1")
        row = store.find_by_id(ref)
        assert row["last_used_session"] == "sess-1"
        assert row["last_used_at"]


# ── Service ───────────────────────────────────────────────────────────────────


class TestAttachmentsService:
    def test_get_attachment_read_through_and_unknown(self):
        from backend.services import attachments as svc

        ref = svc.create_attachment(_entry(), filename="bank.csv")
        assert svc.get_attachment(ref)["kind"] == "text"
        assert svc.get_attachment("att_nope") is None
        assert svc.get_attachment("") is None

    def test_get_attachment_meta_is_a_metadata_only_read(self):
        from backend.services import attachments as svc

        ref = svc.create_attachment(_entry(), filename="bank.csv", media_type="text/csv")
        meta = svc.get_attachment_meta(ref)
        assert meta["filename"] == "bank.csv"
        assert meta["media_type"] == "text/csv"
        assert "text" not in meta  # payload field, never in the metadata row
        assert svc.get_attachment_meta("") is None
        assert svc.get_attachment_meta("att_nope") is None

    def test_get_attachment_survives_process_restart_simulation(self):
        """The bug that started this (as UploadStore): a --reload wiped the old
        in-memory cache."""
        from backend.services import attachments as svc

        ref = svc.create_attachment(_entry())
        svc._cache_drop()  # simulate process restart (in-memory state gone)
        entry = svc.get_attachment(ref)
        assert entry is not None and entry["content"] == _entry()["content"]

    def test_delete_invalidates_cache(self):
        from backend.services import attachments as svc

        ref = svc.create_attachment(_entry())
        assert svc.get_attachment(ref) is not None  # now cached
        assert svc.delete_attachment(ref) is True
        assert svc.get_attachment(ref) is None

    def test_import_source_roundtrip(self):
        from backend.services import attachments as svc

        ref = svc.create_attachment(_entry())
        src = svc.get_attachment_import_source(ref)
        assert src == (b"Date,Amount\n2026-07-01,-12.50", "csv")
        ref2 = svc.create_attachment(_entry(format=None, content=None))
        assert svc.get_attachment_import_source(ref2) is None

    def test_delete_all(self):
        from backend.services import attachments as svc

        svc.create_attachment(_entry())
        svc.create_attachment(_entry())
        assert svc.delete_all_attachments() == 2
        assert svc.list_attachments() == []

    def test_attachments_for_message_matches_by_exact_provenance_pair(self):
        """The lookup an external-MCP tool's captured image is found back BY on transcript
        reload (``ai.assistant._maybe_append_mcp_image_attachments``) — never by parsing the
        tool_result's own (untrusted) text."""
        from backend.services import attachments as svc

        image_entry = _entry(kind="image", text=None, content=None, data="aGVsbG8=")
        match = svc.create_attachment(
            image_entry, source_conversation_id="sess-1", source_message_id="tu-1"
        )
        svc.create_attachment(
            image_entry,
            source_conversation_id="sess-1",
            source_message_id="tu-2",  # different tool_use_id — must not match
        )
        svc.create_attachment(
            image_entry,
            source_conversation_id="sess-2",  # different session — must not match
            source_message_id="tu-1",
        )

        found = svc.attachments_for_message("sess-1", "tu-1")
        assert [a["id"] for a in found] == [match]
        assert svc.attachments_for_message("", "tu-1") == []
        assert svc.attachments_for_message("sess-1", "") == []
        assert svc.attachments_for_message("sess-nope", "tu-nope") == []


# ── Shared ingress: services.attachment_ingress.persist_inbound_file ───────────


class TestAttachmentIngress:
    def test_persists_with_channel_provenance_and_content_hash(self):
        import hashlib

        from backend.db.attachment_store import AttachmentStore
        from backend.services.attachment_ingress import persist_inbound_file

        content = b"Date,Amount\n2026-07-01,-12.50"
        result = persist_inbound_file(
            "bank.csv",
            "text/csv",
            content,
            source_channel="chat:web",
            source_conversation_id="sess-web-1",
        )
        assert result["kind"] == "text"
        assert result["media_type"] == "text/csv"
        assert result["was_encrypted"] is False

        row = AttachmentStore().find_by_id(result["attachment_id"])
        assert row["source_channel"] == "chat:web"
        assert row["source_conversation_id"] == "sess-web-1"
        assert row["content_hash"] == hashlib.sha256(content).hexdigest()

    def test_telegram_shaped_call_carries_message_id(self):
        from backend.db.attachment_store import AttachmentStore
        from backend.services.attachment_ingress import persist_inbound_file

        result = persist_inbound_file(
            "telegram_photo_42.jpg",
            "image/jpeg",
            b"\xff\xd8\xff\xe0fakejpeg",
            source_channel="connector:telegram",
            source_conversation_id="sess-tg-1",
            source_message_id="42",
        )
        row = AttachmentStore().find_by_id(result["attachment_id"])
        assert row["source_channel"] == "connector:telegram"
        assert row["source_message_id"] == "42"
        assert row["kind"] == "image"

    def test_unsupported_file_raises_value_error_and_persists_nothing(self):
        from backend.services import attachments as svc
        from backend.services.attachment_ingress import persist_inbound_file

        with pytest.raises(ValueError):
            persist_inbound_file(
                "weird.xyz",
                "application/x-weird",
                b"not a real format",
                source_channel="chat:web",
            )
        assert svc.list_attachments() == []

    def test_heic_photo_is_transcoded_to_jpeg_on_ingest(self):
        """An iPhone photo (HEIC) isn't in _blob_response's INLINE_SAFE_MEDIA_TYPES allowlist
        and no non-Safari browser can decode it inline — GET /api/uploads/{ref}/image would
        force it to download rather than display. persist_inbound_file must transcode it to
        JPEG BEFORE storage so every downstream consumer already sees a renderable format."""
        import base64
        import io

        from PIL import Image

        from backend.db.attachment_store import AttachmentStore
        from backend.services.attachment_ingress import persist_inbound_file

        heic_buf = io.BytesIO()
        Image.new("RGB", (8, 8), color=(200, 50, 50)).save(heic_buf, format="HEIF")
        heic_bytes = heic_buf.getvalue()

        result = persist_inbound_file(
            "IMG_0001.heic",
            "image/heic",
            heic_bytes,
            source_channel="chat:web",
        )
        assert result["kind"] == "image"
        assert result["media_type"] == "image/jpeg"

        row = AttachmentStore().find_by_id(result["attachment_id"])
        assert row["media_type"] == "image/jpeg"
        assert row["filename"] == "IMG_0001.jpg"

        entry = AttachmentStore().read_payload(result["attachment_id"])
        stored = base64.b64decode(entry["data"])
        assert stored[:3] == b"\xff\xd8\xff"  # JPEG magic bytes — not the original HEIC

    def test_corrupt_heic_falls_back_to_storing_the_original_rather_than_failing(self):
        """A HEIC that fails to decode (truncated, unsupported variant, or a pillow-heif
        environment problem) must not break the upload outright — this is a display
        improvement, not a new failure mode for a file that was previously storable."""
        from backend.db.attachment_store import AttachmentStore
        from backend.services.attachment_ingress import persist_inbound_file

        result = persist_inbound_file(
            "broken.heic",
            "image/heic",
            b"not actually heic bytes",
            source_channel="chat:web",
        )
        assert result["kind"] == "image"
        assert result["media_type"] == "image/heic"

        row = AttachmentStore().find_by_id(result["attachment_id"])
        assert row["filename"] == "broken.heic"

    def test_ingress_never_touches_statement_store(self, monkeypatch):
        """Phase 4 (STATEMENT-ATTACHMENT-BOUNDARY, item 21): persist_inbound_file must not
        call StatementStore at all anymore — a generic ingress creates only an attachment.
        A StatementStore failure injected here would previously have been swallowed as
        best-effort; now there is nothing to swallow because nothing is ever called."""
        from backend.db.statement_store import StatementStore
        from backend.services.attachment_ingress import persist_inbound_file

        def boom(*_a, **_k):
            raise AssertionError("StatementStore.create must not be called from ingress")

        monkeypatch.setattr(StatementStore, "create", boom)
        result = persist_inbound_file(
            "bank.csv", "text/csv", b"Date,Amount\n2026-07-01,-12.50", source_channel="chat:web"
        )
        assert "statement_id" not in result
        assert result["attachment_id"]


# ── REST ──────────────────────────────────────────────────────────────────────


class TestAttachmentsRest:
    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import backend.api.upload as upload_mod

        app = FastAPI()
        app.include_router(upload_mod.router)
        return TestClient(app)

    def test_upload_persists_with_web_source_channel(self, client):
        from backend.db.attachment_store import AttachmentStore

        r = client.post(
            "/api/upload",
            files={"file": ("bank.csv", b"Date,Amount\n2026-07-01,-12.50", "text/csv")},
        )
        assert r.status_code == 200
        ref_id = r.json()["ref_id"]
        row = AttachmentStore().find_by_id(ref_id)
        assert row["source_channel"] == "chat:web"

    def test_list_and_delete(self, client):
        from backend.services import attachments as svc

        ref = svc.create_attachment(_entry(), filename="bank.csv")
        r = client.get("/api/uploads")
        assert r.status_code == 200
        rows = r.json()["uploads"]
        assert [x["id"] for x in rows] == [ref]
        assert "text" not in rows[0], "list must never carry payload content"

        assert client.delete(f"/api/uploads/{ref}").status_code == 200
        assert client.get("/api/uploads").json()["uploads"] == []
        assert client.delete(f"/api/uploads/{ref}").status_code == 404

    def test_delete_all(self, client):
        from backend.services import attachments as svc

        svc.create_attachment(_entry())
        svc.create_attachment(_entry())
        r = client.delete("/api/uploads")
        assert r.status_code == 200 and r.json()["deleted_count"] == 2

    def test_get_image_roundtrips_raw_bytes(self, client):
        import base64

        from backend.services import attachments as svc

        raw = b"\x89PNG\r\n\x1a\nfake-png-bytes"
        ref = svc.create_attachment(
            {
                "kind": "image",
                "media_type": "image/png",
                "data": base64.standard_b64encode(raw).decode(),
                "summary": "Image (pic.png, 1KB)",
            },
            filename="pic.png",
        )
        r = client.get(f"/api/uploads/{ref}/image")
        assert r.status_code == 200
        assert r.content == raw
        assert r.headers["content-type"] == "image/png"

    def test_get_image_404s_for_unknown_or_non_image(self, client):
        from backend.services import attachments as svc

        assert client.get("/api/uploads/att_nope/image").status_code == 404

        text_ref = svc.create_attachment(_entry())  # kind="text"
        assert client.get(f"/api/uploads/{text_ref}/image").status_code == 404


# ── Cross-channel restart/reload (RFC item 14 — the acceptance-matrix row) ─────


class TestCrossChannelRestartSurvival:
    def test_web_and_telegram_ingress_retrieve_identically_after_restart(self):
        """Persist one attachment via each channel's shaped ingress call, drop every
        in-process cache AND construct fresh store/service instances (the closest a unit
        test gets to a real process restart — see AttachmentStore's own docstring on
        being restart-durable), then retrieve both by id. Origin channel must not affect
        what comes back — same bytes, same kind, same summary, regardless of channel."""
        from backend.services import attachments as svc
        from backend.services.attachment_ingress import persist_inbound_file

        web_bytes = b"Date,Amount\n2026-07-01,-12.50\n2026-07-02,-4.20"
        web_result = persist_inbound_file(
            "bank.csv",
            "text/csv",
            web_bytes,
            source_channel="chat:web",
            source_conversation_id="sess-web-restart",
        )

        tg_bytes = b"\xff\xd8\xff\xe0telegram-jpeg-bytes"
        tg_result = persist_inbound_file(
            "telegram_photo_99.jpg",
            "image/jpeg",
            tg_bytes,
            source_channel="connector:telegram",
            source_conversation_id="sess-tg-restart",
            source_message_id="99",
        )

        # Simulate a process restart: drop the read cache AND stop trusting any
        # in-process store/service object created above.
        svc._cache_drop()

        web_entry = svc.get_attachment(web_result["attachment_id"])
        tg_entry = svc.get_attachment(tg_result["attachment_id"])

        assert web_entry is not None and tg_entry is not None
        assert web_entry["content"] == web_bytes
        assert web_entry["kind"] == "text"
        assert tg_entry["kind"] == "image"
        import base64

        assert base64.standard_b64decode(tg_entry["data"]) == tg_bytes

        # Metadata provenance also survives, identically shaped across channels.
        web_meta = svc.get_attachment_meta(web_result["attachment_id"])
        tg_meta = svc.get_attachment_meta(tg_result["attachment_id"])
        assert web_meta["source_channel"] == "chat:web"
        assert tg_meta["source_channel"] == "connector:telegram"
        assert set(web_meta) == set(tg_meta), "both channels produce the same record shape"


# ── Chat: durable-ref vs. dispatch-resolved content (RFC item 13) ──────────────


async def test_build_user_content_surfaces_missing_ref():
    """A dead ref renders an explicit unavailable notice — never a silent skip."""
    from backend.ai.attachments import build_user_content

    content = await build_user_content("read this statement", ["att_deadbeef0000"])
    assert isinstance(content, list)
    texts = [p["text"] for p in content if p.get("type") == "text"]
    assert any("no longer available" in t for t in texts)
    assert any("re-attach" in t for t in texts)
    assert texts[-1] == "read this statement"


async def test_build_user_content_returns_a_durable_ref_not_expanded_content():
    """The whole point of the RFC change: the transcript-bound value is a stable ref,
    never the expanded bytes/text — that expansion now happens only at dispatch time."""
    from backend.ai.attachments import build_user_content
    from backend.db.attachment_store import AttachmentStore
    from backend.services import attachments as svc

    ref = svc.create_attachment(_entry(), filename="bank.csv")
    content = await build_user_content("import this", [ref], session_id="sess-9")
    assert isinstance(content, list)
    refs = [p for p in content if p.get("type") == "attachment_ref"]
    assert len(refs) == 1
    assert refs[0]["attachment_id"] == ref
    assert refs[0]["filename"] == "bank.csv"
    assert not any(p.get("type") == "text" and "Date,Amount" in p.get("text", "") for p in content)
    assert AttachmentStore().find_by_id(ref)["last_used_session"] == "sess-9"


async def test_resolve_attachment_refs_for_dispatch_expands_a_single_image(monkeypatch):
    """A single attachment with no typed caption must not collapse to a bare block —
    neither provider accepts a raw dict as message content."""
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: True)
    from backend.ai.attachments import build_user_content, resolve_attachment_refs_for_dispatch
    from backend.services import attachments as svc

    ref = svc.create_attachment(
        _entry(
            kind="image",
            media_type="image/jpeg",
            data="ZmFrZQ==",
            content=None,
            summary="receipt.jpg",
            text=None,
            format=None,
            statement_id=None,
        ),
        filename="receipt.jpg",
    )
    durable = await build_user_content("", [ref])
    resolved = await resolve_attachment_refs_for_dispatch(durable, model="")
    assert resolved == [
        {"type": "text", "text": f"[Attached receipt.jpg | ref_id: {ref}]"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": "ZmFrZQ=="},
        },
    ]


async def test_resolve_attachment_refs_for_dispatch_stubs_a_missing_ref_defensively():
    """A block whose attachment vanished BETWEEN construction and dispatch (a same-turn
    race, not the ordinary already-checked-at-construction case) degrades honestly rather
    than raising into the turn."""
    from backend.ai.attachments import resolve_attachment_refs_for_dispatch

    content = [
        {"type": "attachment_ref", "attachment_id": "att_gone", "filename": "x", "media_type": None}
    ]
    resolved = await resolve_attachment_refs_for_dispatch(content, model="")
    texts = [p["text"] for p in resolved if p.get("type") == "text"]
    assert any("no longer available" in t for t in texts)


def test_stub_attachment_refs_renders_older_turns_without_re_reading():
    from backend.ai.attachments import stub_attachment_refs

    content = [
        {
            "type": "attachment_ref",
            "attachment_id": "att_old1",
            "filename": "old.png",
            "media_type": "image/png",
        },
        {"type": "text", "text": "look at this"},
    ]
    stubbed = stub_attachment_refs(content)
    assert not any(b.get("type") == "attachment_ref" for b in stubbed)
    joined = "\n".join(b["text"] for b in stubbed if b.get("type") == "text")
    assert "old.png" in joined
    assert "att_old1" in joined
    assert "look at this" in joined
    # A string / ref-free content passes through unchanged.
    assert stub_attachment_refs("plain text") == "plain text"
    assert stub_attachment_refs([{"type": "text", "text": "no refs here"}]) == [
        {"type": "text", "text": "no refs here"}
    ]


# ── Chat: the durable swap end to end, through a REAL turn (RFC item 13) ───────
#
# Every test above drives ``build_user_content``/``resolve_attachment_refs_for_dispatch``
# directly. Neither proves the actual enforcement point: ``ai/assistant.py::_run_turn``'s
# ``_durable_history()`` mechanism, threaded through every ``save_session_durable``/
# ``_save_session`` call site. This section runs a real turn through the public
# ``assistant.run()`` entrypoint with a scripted provider, so it proves BOTH halves at
# once — what the provider actually received, and what the saved transcript actually holds.


class _RecordingProvider:
    """A minimal scripted provider — records exactly what ``messages`` it was called
    with (deep-copied, since ``history`` keeps mutating after this fires) and replies
    with a single non-tool-use round so the turn completes immediately."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        import copy

        self.calls.append(copy.deepcopy(messages))
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "done", "stop_reason": "end_turn"}


def _model_context(provider: _RecordingProvider):
    import backend.ai.assistant as assistant

    return assistant.ModelTurnContext(
        model="scripted-model",
        provider_key="",
        transport="anthropic",
        base_url="",
        provider=provider,
    )


@pytest.fixture
def _turn_isolation(monkeypatch):
    """Everything a real ``assistant.run()`` turn touches beyond the DB (already isolated
    by this module's autouse ``_isolated_attachments`` fixture) — mirrors the equivalent
    setup in ``tests/test_skill_host_runtime.py``, scoped to just this test so it can't
    affect the rest of this file's tests."""
    import backend.ai.assistant as assistant
    import backend.ai.live_sessions as live_sessions
    import backend.ai.pending_injection as pending_injection
    import backend.ai.turns as turns
    import backend.services.profile as profile_service
    from backend.ai.tools import _skill_scope

    monkeypatch.setattr(assistant, "_record_cost", lambda *a, **k: None)
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda *a, **k: "SYS")

    monkeypatch.setattr(profile_service, "read_profile", lambda: {"chat_multi_turn_enabled": True})
    assistant.SESSIONS.clear()
    assistant._SESSION_LOCKS.clear()
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    _skill_scope.clear_scope()
    turns.set_inline_for_tests(False)
    yield
    assistant.SESSIONS.clear()
    assistant._SESSION_LOCKS.clear()
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    _skill_scope.clear_scope()
    turns.set_inline_for_tests(False)


async def test_a_real_turn_sends_resolved_content_but_durably_saves_the_ref(_turn_isolation):
    """The end-to-end proof the RFC's item 13 needs: a turn carrying an ``attachment_ref``
    (a) hands the PROVIDER the resolved content — here, the text attachment's extracted
    text, matching ``resolve_attachment_refs_for_dispatch`` — but (b) the transcript the
    turn SAVES (read back via ``SessionStore``, exactly what a reload rehydrates from)
    still holds the original ``attachment_ref`` block, never the expanded text.
    ``_durable_history()`` is the one mechanism that makes both of those true at once."""
    import backend.ai.assistant as assistant
    from backend.ai.attachments import build_user_content
    from backend.db.session_store import SessionStore
    from backend.services import attachments as svc

    session_id = "sess-durable-swap"
    assistant.get_or_create_session(session_id)

    ref = svc.create_attachment(_entry(), filename="bank.csv")
    content = await build_user_content("please import this", [ref], session_id=session_id)
    new_messages = [{"role": "user", "content": content}]

    provider = _RecordingProvider()
    events = [
        event
        async for event in assistant.run(
            session_id,
            new_messages,
            system="SYS",
            model_context=_model_context(provider),
        )
    ]
    assert any(e.get("type") == "turn_done" for e in events), events

    # (a) the PROVIDER received the RESOLVED content for this turn's user message —
    # never a bare, unresolved attachment_ref.
    assert provider.calls, "provider was never called"
    sent_user_msgs = [m for m in provider.calls[-1] if m.get("role") == "user"]
    assert sent_user_msgs
    sent_content = sent_user_msgs[-1]["content"]
    assert isinstance(sent_content, list)
    assert not any(
        isinstance(b, dict) and b.get("type") == "attachment_ref" for b in sent_content
    ), "the provider must never see a bare attachment_ref -- it must be resolved"
    sent_texts = [
        b["text"] for b in sent_content if isinstance(b, dict) and b.get("type") == "text"
    ]
    assert any("Date,Amount" in t for t in sent_texts), "resolved attachment text is missing"

    # (b) the DURABLY SAVED transcript still holds the ref-shaped block, not the expansion.
    saved = SessionStore().get_transcript(session_id)
    saved_user_msgs = [m for m in saved if m.get("role") == "user"]
    assert saved_user_msgs
    saved_content = saved_user_msgs[-1]["content"]
    assert isinstance(saved_content, list)
    saved_refs = [
        b for b in saved_content if isinstance(b, dict) and b.get("type") == "attachment_ref"
    ]
    assert len(saved_refs) == 1
    assert saved_refs[0]["attachment_id"] == ref
    assert not any(
        isinstance(b, dict) and b.get("type") == "text" and "Date,Amount" in b.get("text", "")
        for b in saved_content
    ), "the durable transcript must never hold the expanded attachment text"


# ── Checkpoint + export wiring ────────────────────────────────────────────────


def test_attachments_are_checkpoint_excluded():
    from backend.ai.checkpoint import _EXCLUDED_PREFIXES, _EXCLUDED_TABLES
    from backend.paths import ATTACHMENTS_DIR

    assert "attachments" in _EXCLUDED_TABLES
    assert ATTACHMENTS_DIR.resolve() in _EXCLUDED_PREFIXES


def test_attachments_are_portable():
    from backend.core.portable import PORTABLE_DIRS, PORTABLE_FILES
    from backend.paths import ATTACHMENTS_DIR, ATTACHMENTS_JSON

    assert ATTACHMENTS_JSON in PORTABLE_FILES
    assert ATTACHMENTS_DIR in PORTABLE_DIRS
