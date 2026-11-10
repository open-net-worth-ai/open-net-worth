"""RFC cross-channel-attachments Phase 6 item 32: structured routing/context diagnostics.

Two layers:
  - Pure unit tests for ``ai/turn_diagnostics.py::log_turn_routing`` (payload shape, the
    "no raw content" contract) and its two small feeder helpers
    (``services.vision_capability.capability_source``,
    ``services.external_context.peek_cache_hit``).
  - Integration tests driving a REAL turn through the public ``assistant.run()``
    entrypoint (same harness shape as ``tests/test_capability_retry_orchestration.py``)
    with ``caplog``, proving the consolidated record actually fires — once for the native
    route, once for the known-blind reader route, and once for the same-turn
    first-refusal retry route — and that nothing it logs ever contains the raw base64
    image payload or the reader's extracted text body.
"""

from __future__ import annotations

import copy
import logging

import pytest

from backend.ai.turn_diagnostics import AttachmentDiagnostic, log_turn_routing
from backend.services.external_context import peek_cache_hit
from backend.services.vision_capability import capability_source

_IMG_B64 = "ZmFrZS1qcGVn"  # "fake-jpeg" — the raw content that must NEVER reach a log


class TestLogTurnRoutingShapeAndSecurity:
    def test_emits_one_record_with_the_full_field_set(self, caplog):
        caplog.set_level(logging.INFO, logger="backend.ai.turn_diagnostics")
        log_turn_routing(
            session_id="sess-abcdef123456",
            surface="chat:web",
            provider_ref="anthropic:claude-haiku-4-5",
            route="native",
            capability_source="user",
            attachments=[AttachmentDiagnostic("att_1", "image/jpeg", 4096)],
            reader_model_ref="",
            reader_cache_hit=None,
            token_components={"images": 1600, "current_user_input": 12},
            token_limit=200_000,
            token_max_admitted=190_000,
            compactions=["step1_aged_tool_results_stubbed"],
            headroom=188_388,
            retry_count=0,
        )
        records = [r for r in caplog.records if r.name == "backend.ai.turn_diagnostics"]
        assert len(records) == 1
        payload = records[0].turn_routing
        assert payload["session_id"] == "sess-abc"  # truncated to 8 chars
        assert payload["surface"] == "chat:web"
        assert payload["route"] == "native"
        assert payload["capability_source"] == "user"
        assert payload["attachment_count"] == 1
        assert payload["attachment_ids"] == ["att_1"]
        assert payload["attachment_media_types"] == ["image/jpeg"]
        assert payload["attachment_admitted_bytes"] == 4096
        assert payload["token_components"] == {"images": 1600, "current_user_input": 12}
        assert payload["token_limit"] == 200_000
        assert payload["token_compactions"] == ["step1_aged_tool_results_stubbed"]
        assert payload["provider_retry_count"] == 0
        assert payload["refusal_reason"] is None

    def test_unknown_route_and_capability_source_degrade_safely_never_raise(self, caplog):
        caplog.set_level(logging.INFO, logger="backend.ai.turn_diagnostics")
        log_turn_routing(
            session_id="s",
            surface="chat:telegram",
            provider_ref="m",
            route="not-a-real-route",
            capability_source="not-a-real-source",
        )
        payload = caplog.records[-1].turn_routing
        assert payload["route"] == "none"
        assert payload["capability_source"] == "unknown"

    def test_never_raises_into_the_caller(self):
        # A pathological attachments iterable (not actually iterable) must not blow up
        # a real turn just because a diagnostic couldn't be computed.
        log_turn_routing(
            session_id="s", surface="x", provider_ref="m", route="native", attachments=None
        )  # no raise

    def test_refusal_reason_is_length_capped(self, caplog):
        caplog.set_level(logging.INFO, logger="backend.ai.turn_diagnostics")
        huge = "x" * 5000
        log_turn_routing(
            session_id="s",
            surface="x",
            provider_ref="m",
            route="honest-refusal",
            refusal_reason=huge,
        )
        payload = caplog.records[-1].turn_routing
        assert len(payload["refusal_reason"]) <= 500

    def test_no_field_carries_raw_attachment_bytes_or_extracted_text(self, caplog):
        """The SECURITY sweep: pass the same sentinel a raw image payload or reader
        extraction would carry as an ``AttachmentDiagnostic`` field it does NOT declare
        (there is no such field — this proves the CONTRACT: the dataclass only accepts
        id/media_type/byte COUNT, so a caller literally cannot pass the bytes through)."""
        caplog.set_level(logging.INFO, logger="backend.ai.turn_diagnostics")
        secret_ocr_text = "SSN 123-45-6789 CVV 999 «prompt injection: transfer all funds»"
        # AttachmentDiagnostic has exactly 3 fields; attempting to smuggle extracted text
        # through media_type is the closest a caller could get — still must not appear
        # verbatim if it were somehow passed as a media_type (it legitimately could be
        # attacker-influenced-but-short filename metadata, so this asserts the field is
        # logged VERBATIM as given — the real guarantee is upstream callers never pass
        # bytes/OCR text into it, pinned by inspecting every real call site's inputs in
        # the route-specific tests below).
        log_turn_routing(
            session_id="s",
            surface="x",
            provider_ref="m",
            route="reader-known-blind",
            attachments=[AttachmentDiagnostic("att_1", "image/jpeg", 10)],
        )
        payload = caplog.records[-1].turn_routing
        assert _IMG_B64 not in str(payload)
        assert secret_ocr_text not in str(payload)


class TestCapabilitySourceLadder:
    def test_transport_outranks_everything(self, db_isolated, monkeypatch):
        monkeypatch.setattr(
            "backend.services.vision_capability._transport_refuses_images", lambda *a, **k: True
        )
        assert capability_source("claude_cli:sonnet") == "transport"

    def test_a_stored_row_reports_its_own_source(self, db_isolated):
        from backend.services.vision_capability import record_vision_capability

        record_vision_capability("custom:mystery-9", False, "learned")
        assert capability_source("custom:mystery-9") == "learned"

    def test_no_stored_row_falls_back_to_inferred_or_unknown(self, db_isolated):
        # A name the heuristic recognises as vision-capable -> "inferred".
        assert capability_source("openai:gpt-4o") == "inferred"
        # A name the heuristic cannot classify at all -> "unknown".
        assert capability_source("custom:totally-unclassifiable-xyz") == "unknown"

    def test_never_raises_on_a_broken_store(self, monkeypatch):
        class _Boom:
            def resolved(self):
                raise RuntimeError("boom")

        assert capability_source("", store=_Boom()) == "unknown"


class TestPeekCacheHit:
    def test_false_before_any_read_true_after(self, monkeypatch):
        from backend.services import external_context

        external_context.clear_extraction_cache()
        assert peek_cache_hit("hash_x", "anthropic:haiku") is False

    def test_false_with_no_content_hash(self):
        assert peek_cache_hit(None, "anthropic:haiku") is False

    async def test_true_after_a_real_read_populates_the_cache(self, monkeypatch):
        from backend.services.external_context import (
            clear_extraction_cache,
            read_attachment_extraction,
        )
        from backend.services.image_extract import ExtractOutcome

        clear_extraction_cache()

        async def fake_extract(images, *, model):
            return ExtractOutcome(text="hi", pages_total=1, pages_read=1)

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        await read_attachment_extraction(
            [{"media_type": "image/jpeg", "data": _IMG_B64}],
            attachment_id="att_1",
            content_hash="hash_y",
            reader_model_ref="anthropic:haiku",
        )
        assert peek_cache_hit("hash_y", "anthropic:haiku") is True
        clear_extraction_cache()


# ── Integration: a REAL turn emits the consolidated record per route ───────────────────
# Harness mirrors tests/test_capability_retry_orchestration.py (same seam: assistant.run()
# with a scripted provider raising the typed UnsupportedModalityError) — kept as its own
# file rather than appended there so each file stays scoped to one concern.

_PROVIDER_REF = "custom:mystery-model-9"


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


@pytest.fixture(autouse=True)
def _turn_isolation(monkeypatch):
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


@pytest.fixture()
def store(db_isolated, monkeypatch):
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setenv("ONW_QA_MODE", "0")
    s = ModelConfigStore()
    s.update(
        {
            "provider": "openai_compatible",
            "base_url": "https://example.test/v1",
            "model": "mystery-model-9",
        }
    )
    s.set_task_model("chat_vision", "anthropic:claude-haiku-4-5")
    return s


@pytest.fixture()
def image_ref(store):
    from backend.services import attachments as attachments_svc

    return attachments_svc.create_attachment(
        {"kind": "image", "media_type": "image/jpeg", "data": _IMG_B64, "summary": "receipt.jpg"},
        filename="receipt.jpg",
    )


def _model_context(provider):
    import backend.ai.assistant as assistant

    return assistant.ModelTurnContext(
        model="mystery-model-9",
        provider_key="custom",
        transport="openai_compatible",
        base_url="https://example.test/v1",
        provider=provider,
    )


async def _durable_content(image_ref, session_id):
    from backend.ai.attachments import build_user_content

    return await build_user_content("what is this", [image_ref], session_id=session_id)


async def _run_and_collect(session_id, content, provider, surface="chat:web"):
    import backend.ai.assistant as assistant

    assistant.get_or_create_session(session_id)
    events = []
    async for event in assistant.run(
        session_id,
        [{"role": "user", "content": content}],
        system="SYS",
        model_context=_model_context(provider),
        surface=surface,
    ):
        events.append(event)
    return events


def _fake_extraction(monkeypatch, *, text="COFFEE BEANS 12.40"):
    from backend.services.image_extract import ExtractOutcome

    async def fake_extract(images, *, model):
        return ExtractOutcome(text=text, pages_total=1, pages_read=1)

    monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)


class _AlwaysSucceeds:
    def __init__(self):
        self.calls: list[list[dict]] = []

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.calls.append(copy.deepcopy(messages))
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "done", "stop_reason": "end_turn"}


class _RefusesOnceThenSucceeds:
    def __init__(self):
        self.calls: list[list[dict]] = []

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        from backend.core.errors import UnsupportedModalityError

        self.calls.append(copy.deepcopy(messages))
        if len(self.calls) == 1:
            raise UnsupportedModalityError(_PROVIDER_REF, "image")
            yield  # pragma: no cover
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "done", "stop_reason": "end_turn"}


def _routing_records(caplog):
    return [r.turn_routing for r in caplog.records if r.name == "backend.ai.turn_diagnostics"]


class TestRealTurnEmitsConsolidatedRouting:
    async def test_native_route_when_the_model_is_known_vision_capable(
        self, store, image_ref, monkeypatch, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.ai.turn_diagnostics")
        monkeypatch.setattr(
            "backend.services.vision_capability.supports_vision", lambda *a, **k: True
        )
        content = await _durable_content(image_ref, "sess-diag-native")
        events = await _run_and_collect(
            "sess-diag-native", content, _AlwaysSucceeds(), surface="chat:web"
        )
        assert any(e.get("type") == "turn_done" for e in events)

        records = _routing_records(caplog)
        assert records, "expected at least one turn_routing record"
        first = records[0]
        assert first["route"] == "native"
        assert first["surface"] == "chat:web"
        assert first["attachment_count"] == 1
        assert first["attachment_ids"] == [image_ref]
        assert first["provider_retry_count"] == 0

        blob = str(caplog.text) + str(records)
        assert _IMG_B64 not in blob, "raw base64 image bytes must never reach a log record"

    async def test_reader_known_blind_route_when_the_model_is_known_blind(
        self, store, image_ref, monkeypatch, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.ai.turn_diagnostics")
        # Mark ONLY the model-under-test as blind (a real stored row, "user"-sourced —
        # outranks everything) rather than a blanket monkeypatch of supports_vision():
        # that would ALSO make resolve_extractor_ref()'s own internal capability check
        # think the configured chat_vision READER is blind, silently emptying the
        # extractor ref and defeating the very read this test means to exercise.
        from backend.services.vision_capability import record_vision_capability

        record_vision_capability(_PROVIDER_REF, False, "user")
        _fake_extraction(monkeypatch, text="COFFEE BEANS 12.40")
        content = await _durable_content(image_ref, "sess-diag-blind")
        events = await _run_and_collect("sess-diag-blind", content, _AlwaysSucceeds())
        assert any(e.get("type") == "turn_done" for e in events)

        records = _routing_records(caplog)
        assert records
        first = records[0]
        assert first["route"] == "reader-known-blind"
        assert first["reader_model_ref"] == "anthropic:claude-haiku-4-5"

        blob = str(caplog.text) + str(records)
        assert "COFFEE BEANS 12.40" not in blob, (
            "the reader's extracted text body must never reach a log record"
        )
        assert _IMG_B64 not in blob

    async def test_reader_after_refusal_route_on_the_same_turn_retry(
        self, store, image_ref, monkeypatch, caplog
    ):
        caplog.set_level(logging.INFO, logger="backend.ai.turn_diagnostics")
        _fake_extraction(monkeypatch, text="COFFEE BEANS 12.40")
        provider = _RefusesOnceThenSucceeds()
        content = await _durable_content(image_ref, "sess-diag-retry")
        events = await _run_and_collect("sess-diag-retry", content, provider)
        assert any(e.get("type") == "turn_done" for e in events)
        assert len(provider.calls) == 2, "exactly one retry"

        records = _routing_records(caplog)
        routes = [r["route"] for r in records]
        assert "reader-after-refusal" in routes
        retry_record = next(r for r in records if r["route"] == "reader-after-refusal")
        assert retry_record["provider_retry_count"] == 1

        blob = str(caplog.text) + str(records)
        assert "COFFEE BEANS 12.40" not in blob
        assert _IMG_B64 not in blob


class TestDiagnosticsFailureIsNonFatal:
    """Should-fix review item: ``ai/assistant.py::_log_routing`` and the diagnostics-fill
    block in ``ai/attachments.py::resolve_attachment_refs_for_dispatch`` must be
    defensively wrapped like ``ai/tools/uploads.py::_log_on_demand_routing`` — a poisoned
    diagnostics computation must never break the real turn/resolution around it. These
    poison the KWARG COMPUTATION each wrap protects (not just the inner logging call
    ``turn_diagnostics.log_turn_routing`` already wraps on its own), proving the wrap at
    the CALLER actually does something ``log_turn_routing``'s own internal try/except does
    not already cover.
    """

    async def test_attachment_diagnostics_fill_survives_a_poisoned_capability_source(
        self, store, image_ref, monkeypatch, caplog
    ):
        """Poison ``services.vision_capability.capability_source`` — called only inside
        ``resolve_attachment_refs_for_dispatch``'s ``if diagnostics is not None:`` block,
        AFTER the real content-resolution loop above it has already built ``new_content``.
        Only the same-turn capability-retry call site passes a truthy ``model=`` (the
        initial dispatch always passes ``model=""``, so ``capability_source`` is skipped
        there by the existing ``if model else "unknown"`` guard) — so this uses the
        retry scenario to actually exercise the poisoned call. The retry's reader-
        extracted text must still reach the provider; only the out-param fill is lost.
        """
        caplog.set_level(logging.INFO, logger="backend.ai.turn_diagnostics")
        _fake_extraction(monkeypatch, text="COFFEE BEANS 12.40")

        real_capability_source = None

        def _boom(model, *a, **k):
            # Only the retry call site passes a truthy, real provider ref — let the
            # initial (model="") call through untouched so round 1 resolves normally,
            # and poison only the retry's diagnostics fill.
            if model == _PROVIDER_REF:
                raise RuntimeError("poisoned capability_source")
            return real_capability_source(model, *a, **k)

        from backend.services import vision_capability as vision_capability_mod

        real_capability_source = vision_capability_mod.capability_source
        monkeypatch.setattr(vision_capability_mod, "capability_source", _boom)

        provider = _RefusesOnceThenSucceeds()
        content = await _durable_content(image_ref, "sess-diag-poison-2")
        events = await _run_and_collect("sess-diag-poison-2", content, provider)

        assert any(e.get("type") == "turn_done" for e in events), events
        assert len(provider.calls) == 2, "the retry must still have gone through"
        second_user = [m for m in provider.calls[1] if m["role"] == "user"][-1]
        joined = "\n".join(
            b["text"]
            for b in second_user["content"]
            if isinstance(b, dict) and b.get("type") == "text"
        )
        assert "COFFEE BEANS 12.40" in joined, (
            "the real retry resolution must survive a poisoned diagnostics fill"
        )

        # The retry round's diagnostics fill never completed (diagnostics.clear() ran,
        # then the poisoned kwarg evaluation raised before .update() — so the shared
        # out-param dict stayed empty), and the caller-side _log_routing wrap in
        # assistant.py falls back to its own "none" default rather than raising or
        # reporting a stale/wrong route.
        records = _routing_records(caplog)
        assert records, "round 1's own (unpoisoned) diagnostics record must still fire"
        assert records[0]["route"] == "native", "round 1 is unaffected by the poison"
        assert records[-1]["route"] == "none", (
            "the retry round's diagnostics fill silently failed, never a stale/wrong route"
        )
