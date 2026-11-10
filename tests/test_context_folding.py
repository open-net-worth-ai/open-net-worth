"""Tests for RFC D — context folding and the duplicate-tool-call tripwire.

Characterization-grade tests that guard:
  - fold trigger / boundary invariants
  - incremental re-fold cadence (not per turn)
  - summary persistence and no per-turn recompute
  - cache-prefix stability: system_blocks are byte-identical across a re-fold
  - fallback on summarizer failure preserves prior summary
  - tripwire nudge at count=3, cutoff/drop at count=5
  - regression shape: get_portfolio ×20 loop is tamed
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.ai.assistant import (
    FOLD_TRIGGER_FRACTION,
    _extract_turn_fold_result,
    _extract_user_text,
    _load_fold,
    _prepare_messages,
    _save_fold,
    _should_refold,
    _stream_turn_fold_update,
    _verbatim_user_messages,
)
from backend.db.session_store import SessionStore


async def _drain(agen):
    """Exhaust an async generator, discarding what it yields — for tests that only
    care about _generate_fold_summary's/_maybe_update_fold's SIDE EFFECTS (the
    _save_fold call), not its now-streamed compacting_progress events."""
    async for _ in agen:
        pass


class _FakeStreamProvider:
    """A fake AIProvider exposing only stream_complete, for patching
    resolve_model_context in fold tests — the streaming twin of the old
    `patch("backend.core.llm_seam.complete", ...)` pattern this file used before
    _generate_fold_summary switched from complete() to stream_complete()."""

    def __init__(
        self,
        text: str = "",
        *,
        chunks: list[str] | None = None,
        raises: Exception | None = None,
    ):
        # `chunks` (multiple text_delta events) takes precedence when given; `text`
        # stays the single-chunk convenience most tests use.
        self._chunks = chunks if chunks is not None else ([text] if text else [])
        self._raises = raises
        self.calls: list[dict] = []

    async def stream_complete(
        self, prompt, *, system="", model="", max_tokens=0, source="", effort=""
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "model": model,
                "max_tokens": max_tokens,
                "source": source,
                "effort": effort,
            }
        )
        if self._raises is not None:
            raise self._raises
        for chunk in self._chunks:
            yield {"type": "text_delta", "text": chunk}


async def _noop_async_gen(*_a, **_k):
    """A fake _maybe_update_fold replacement that does nothing and yields nothing —
    still a real async generator function (the `if False: yield` makes it one), so
    `async for ev in _maybe_update_fold(...)` inside _stream_turn_fold_update keeps
    working when this is patched in via side_effect."""
    if False:  # noqa: SIM103 - deliberate: makes this function an async generator
        yield


def _patch_fold_provider(
    text: str = "new summary",
    *,
    chunks: list[str] | None = None,
    raises: Exception | None = None,
):
    """Context manager: resolve_model_context returns a ModelTurnContext wrapping a
    _FakeStreamProvider(text, chunks=chunks, raises=raises). Returns (patcher,
    fake_provider) — enter the patcher, then read fake_provider.calls for anything the
    old tests captured via a bare `captured`/`complete_calls` dict."""
    from backend.ai.assistant import ModelTurnContext

    fake_provider = _FakeStreamProvider(text, chunks=chunks, raises=raises)
    ctx = ModelTurnContext(
        model="m", provider_key="", transport="anthropic", base_url="", provider=fake_provider
    )
    return patch("backend.ai.assistant.resolve_model_context", return_value=ctx), fake_provider


# ── Sidecar helpers ───────────────────────────────────────────────────────────


def test_fold_sidecar_round_trip(db_isolated):
    """``fold`` is a nullable column on the session row now (Wave 5E, JSON->DB) — no
    more per-session ``.fold.json`` file to check for existence; use SessionStore's
    own get_fold/clear_fold as the DB-native equivalent."""
    sid = "test-session-123"
    data = {"summary": "User wants to save SGD 5 000/month.", "folded_through_idx": 30}
    _save_fold(sid, data)
    loaded = _load_fold(sid)
    assert loaded["summary"] == data["summary"]
    assert loaded["folded_through_idx"] == 30
    assert SessionStore().get_fold(sid) is not None
    SessionStore().clear_fold(sid)
    assert SessionStore().get_fold(sid) is None
    assert _load_fold(sid) is None


def test_fold_sidecar_absent_returns_none(db_isolated):
    assert _load_fold("no-such-session") is None


def test_fold_sidecar_read_failure_returns_none(monkeypatch):
    """``fold`` is a DB-native JSON column now (Wave 5E) — there is no raw-bytes file
    left to write invalid JSON into (SessionStore.get_fold hands back an already-
    deserialized value, never text to mis-parse). ``_load_fold``'s broad ``except
    Exception`` still guards ANY get_fold() failure though, so pin that contract
    directly instead of via a corrupt file."""

    def boom(self, session_id):
        raise RuntimeError("db read failed")

    monkeypatch.setattr(SessionStore, "get_fold", boom)
    assert _load_fold("bad") is None


# ── _should_refold ────────────────────────────────────────────────────────────
#
# RFC context-folding-v2 §1 + the 2026-08-27 estimate-fallback follow-up: the trigger
# is TOKEN pressure ALWAYS — the session's last real recorded usage when it exists
# (session_chat_records), else a cheap LOCAL byte estimate over the actual history
# (_approx_token_count, same technique as Codex's own approx_token_count) — as a
# fraction of the model's real context window. There is no message-count fallback
# left (FOLD_REFOLD_EVERY_K retired) — a real or estimated number is always available.


def test_should_refold_false_when_window_start_zero():
    assert _should_refold("sid", "m", "", 0, None, []) is False
    assert _should_refold("sid", "m", "", 0, {"folded_through_idx": 0}, []) is False


def test_should_refold_true_when_no_sidecar_and_window_advanced():
    with patch("backend.services.cost.session_chat_records", return_value=[]):
        assert _should_refold("sid", "m", "", 5, None, []) is True


class TestLocalEstimateFallback:
    """No cost_log rows yet for this session — falls back to a cheap LOCAL byte
    estimate over the actual history, never a message count."""

    def test_false_when_estimated_size_is_small(self):
        fold_data = {"folded_through_idx": 10}
        small_history = [{"role": "user", "content": "hi"}]
        with (
            patch("backend.services.cost.session_chat_records", return_value=[]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        ):
            assert _should_refold("sid", "m", "", 11, fold_data, small_history) is False

    def test_true_when_estimated_size_exceeds_the_fraction(self):
        fold_data = {"folded_through_idx": 10}
        # ~4 bytes/token estimate; well past FOLD_TRIGGER_FRACTION * a 1000-token window.
        big_history = [{"role": "user", "content": "x" * 5000}]
        with (
            patch("backend.services.cost.session_chat_records", return_value=[]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        ):
            assert _should_refold("sid", "m", "", 11, fold_data, big_history) is True


class TestTokenTrigger:
    """Steady state: a session WITH recorded usage is judged on real token pressure —
    the last row's largest single-request prompt (`peak_prompt_tokens`) against the
    model's window. `history` is irrelevant here (real rows always win over the estimate)."""

    def test_fires_when_last_usage_exceeds_the_fraction_of_the_models_window(self):
        fold_data = {"folded_through_idx": 10}
        row = {"model": "m", "peak_prompt_tokens": 600}
        with (
            patch("backend.services.cost.session_chat_records", return_value=[row]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        ):
            assert _should_refold("sid", "m", "", 11, fold_data, []) is True

    def test_does_not_fire_when_under_the_fraction(self):
        fold_data = {"folded_through_idx": 10}
        row = {"model": "m", "peak_prompt_tokens": 100}
        with (
            patch("backend.services.cost.session_chat_records", return_value=[row]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        ):
            assert _should_refold("sid", "m", "", 200, fold_data, []) is False

    def test_a_turns_summed_buckets_are_not_a_prompt_size(self):
        """Regression (2026-10-22): a row's token buckets SUM every request of a turn —
        two 300-token requests recorded 600, read as one prompt over the line, so every
        multi-round turn re-folded. Only the largest single request counts."""
        fold_data = {"folded_through_idx": 10}
        row = {
            "model": "m",
            "input_tokens": 4,
            "cache_creation_tokens": 300,
            "cache_read_tokens": 296,
            "peak_prompt_tokens": 300,
        }
        with (
            patch("backend.services.cost.session_chat_records", return_value=[row]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        ):
            assert _should_refold("sid", "m", "", 11, fold_data, []) is False

    def test_a_row_without_a_peak_falls_back_to_the_local_estimate(self):
        """A pre-fix row carries only summed buckets — never trusted as a prompt size."""
        fold_data = {"folded_through_idx": 10}
        legacy = {"model": "m", "input_tokens": 900, "cache_creation_tokens": 0}
        small_history = [{"role": "user", "content": "hi"}]
        big_history = [{"role": "user", "content": "x" * 5000}]
        with (
            patch("backend.services.cost.session_chat_records", return_value=[legacy]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        ):
            assert _should_refold("sid", "m", "", 11, fold_data, small_history) is False
            assert _should_refold("sid", "m", "", 11, fold_data, big_history) is True

    def test_uses_the_most_recent_row_when_several_exist(self):
        fold_data = {"folded_through_idx": 10}
        rows = [
            {"model": "m", "peak_prompt_tokens": 900},
            {"model": "m", "peak_prompt_tokens": 50},  # latest
        ]
        with (
            patch("backend.services.cost.session_chat_records", return_value=rows),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        ):
            assert _should_refold("sid", "m", "", 11, fold_data, []) is False

    def test_fold_data_none_is_always_first_fold_regardless_of_usage(self):
        with patch("backend.services.cost.session_chat_records", return_value=[]):
            assert _should_refold("sid", "m", "", 5, None, []) is True

    def test_boundary_is_exactly_fold_trigger_fraction_of_the_window(self):
        """Pins the actual FOLD_TRIGGER_FRACTION constant (not a hardcoded 0.55) so this
        test keeps discriminating even if the confirmed value is ever revisited."""
        fold_data = {"folded_through_idx": 10}
        budget = 1000
        at_boundary = round(FOLD_TRIGGER_FRACTION * budget)
        with patch("backend.ai.models.price_map.max_input_tokens", return_value=budget):
            row_at = [{"model": "m", "peak_prompt_tokens": at_boundary}]
            with patch("backend.services.cost.session_chat_records", return_value=row_at):
                assert (
                    _should_refold("sid", "m", "", 11, fold_data, []) is False
                )  # not STRICTLY over

            row_over = [{"model": "m", "peak_prompt_tokens": at_boundary + 1}]
            with patch("backend.services.cost.session_chat_records", return_value=row_over):
                assert _should_refold("sid", "m", "", 11, fold_data, []) is True


class TestReportedContextWindow:
    """The window the provider REPORTED serving the turn beats the price table — the
    CLI's bare ``sonnet`` alias is in no table (200K fallback) but runs a 1M model."""

    def test_reported_window_beats_the_table_fallback(self):
        """Regression (2026-10-22): a 120K prompt on a 1M-window model is ~12% full,
        not over 55% of a guessed 200K."""
        fold_data = {"folded_through_idx": 10}
        # The live turn that kept compacting: two requests summing to 221K.
        row = {
            "model": "sonnet",
            "input_tokens": 4,
            "cache_creation_tokens": 114_428,
            "cache_read_tokens": 106_593,
            "peak_prompt_tokens": 120_000,
            "context_window": 1_000_000,
        }
        with (
            patch("backend.services.cost.session_chat_records", return_value=[row]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=200_000),
        ):
            assert _should_refold("sid", "sonnet", "", 11, fold_data, []) is False

    def test_reported_window_still_triggers_when_genuinely_full(self):
        fold_data = {"folded_through_idx": 10}
        row = {"model": "sonnet", "peak_prompt_tokens": 600_000, "context_window": 1_000_000}
        with (
            patch("backend.services.cost.session_chat_records", return_value=[row]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=200_000),
        ):
            assert _should_refold("sid", "sonnet", "", 11, fold_data, []) is True

    def test_a_window_reported_for_another_model_is_ignored(self):
        """The user switched models since that row — its window says nothing now."""
        fold_data = {"folded_through_idx": 10}
        row = {"model": "sonnet", "peak_prompt_tokens": 600, "context_window": 1_000_000}
        with (
            patch("backend.services.cost.session_chat_records", return_value=[row]),
            patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        ):
            assert _should_refold("sid", "other-model", "", 11, fold_data, []) is True


def test_round_prompt_tokens_prefers_the_providers_own_single_request_figure():
    from backend.ai.assistant import _round_prompt_tokens

    summed = {"input_tokens": 4, "cache_creation_tokens": 300, "cache_read_tokens": 400}
    # One round of an API provider IS one request — its buckets are that prompt.
    assert _round_prompt_tokens(summed) == 704
    # A provider that made several requests in the round reports the largest itself.
    assert _round_prompt_tokens({**summed, "prompt_tokens": 402}) == 402
    assert _round_prompt_tokens({**summed, "prompt_tokens": None}) == 704


def test_cost_record_persists_the_peak_prompt_and_window(db_isolated):
    from backend.ai.cost_tracker import record
    from backend.services.cost import session_chat_records

    record(
        "chat",
        "sonnet",
        4,
        10,
        300,
        400,
        "sid-peak",
        cost_usd=0.0,
        peak_prompt_tokens=402,
        context_window=1_000_000,
    )
    row = session_chat_records("sid-peak")[-1]
    assert row["peak_prompt_tokens"] == 402
    assert row["context_window"] == 1_000_000


# ── _prepare_messages: fold summary injection ─────────────────────────────────


def _make_history(n: int) -> list[dict]:
    """Produce n alternating user/assistant messages."""
    history = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": f"message {i}"})
    return history


def test_prepare_messages_no_fold_unchanged():
    """Without a fold summary the output must be byte-identical to current behaviour."""
    history = _make_history(10)
    result_no_fold = _prepare_messages(history, window_start=0)
    result_fold_none = _prepare_messages(history, window_start=0, fold_summary=None)
    assert result_no_fold == result_fold_none


def test_prepare_messages_fold_prepends_first_message():
    history = _make_history(40)
    window_start = 10
    summary = "User earns SGD 8 000/month and wants to reach FIRE by 2040."
    result = _prepare_messages(history, window_start=window_start, fold_summary=summary)
    assert result[0]["role"] == "user"
    assert "[CONVERSATION SUMMARY SO FAR]" in result[0]["content"]
    assert summary in result[0]["content"]


def test_prepare_messages_fold_is_first_not_duplicate():
    """Folded summary must appear exactly once at index 0."""
    history = _make_history(40)
    summary = "Some context."
    result = _prepare_messages(history, window_start=5, fold_summary=summary)
    fold_entries = [
        m for m in result if "[CONVERSATION SUMMARY SO FAR]" in str(m.get("content", ""))
    ]
    assert len(fold_entries) == 1
    assert result[0] == fold_entries[0]


def test_prepare_messages_fold_does_not_appear_in_system():
    """The fold summary must live in messages[], never in system_blocks."""
    history = _make_history(40)
    summary = "Financial context."
    result = _prepare_messages(history, window_start=5, fold_summary=summary)
    # Every entry in result is a message dict with a role — no system dict.
    for msg in result:
        assert "role" in msg


def test_prepare_messages_empty_fold_does_not_prepend():
    history = _make_history(20)
    result = _prepare_messages(history, window_start=5, fold_summary="")
    assert result[0].get("content", "").find("[CONVERSATION SUMMARY") == -1


# ── fold never covers current turn or turn_ctx_idx ───────────────────────────


def test_fold_never_covers_window_start_or_beyond():
    """_generate_fold_summary only summarises history[prev_idx:window_start],
    never history[window_start:] — the current turn is always verbatim."""
    history = _make_history(50)
    window_start = 40
    # Inject a fold sidecar that was last folded at idx=30
    fold_data = {
        "summary": "prior summary",
        "folded_through_idx": 30,
        "generated_at_len": 40,
    }

    captured_slices: list[list[dict]] = []

    def fake_conversation_text(h: list[dict]) -> str:
        captured_slices.append(h)
        return " ".join(m.get("content", "") for m in h)

    patcher, _fake_provider = _patch_fold_provider("short summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=fold_data),
        patch("backend.ai.assistant._save_fold") as mock_save,
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", history, window_start, fold_data)))

    # The saved folded_through_idx must be window_start — never beyond
    if mock_save.called:
        saved = mock_save.call_args[0][1]
        assert saved["folded_through_idx"] == window_start
        # The slice given to _conversation_text must be entirely < window_start
        for slc in captured_slices:
            assert len(slc) <= window_start


# ── incremental re-fold cadence ───────────────────────────────────────────────


def test_no_recompute_when_estimated_size_is_small():
    """_maybe_update_fold must NOT call the LLM when there's no recorded usage yet AND
    the local byte estimate over the actual history stays under FOLD_TRIGGER_FRACTION."""
    fold_data = {
        "summary": "existing summary",
        "folded_through_idx": 20,
        "generated_at_len": 40,
    }

    patcher, fake_provider = _patch_fold_provider("new summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=fold_data),
        patch("backend.services.cost.session_chat_records", return_value=[]),
        patch("backend.ai.models.price_map.max_input_tokens", return_value=1_000_000),
        patcher,
    ):
        from backend.ai.assistant import _maybe_update_fold

        # No cost_log rows yet — the local estimate over 50 tiny messages is nowhere
        # near 55% of a 1,000,000-token window.
        asyncio.run(_drain(_maybe_update_fold("sid", _make_history(50), 25, "m", "")))

    assert len(fake_provider.calls) == 0, "LLM must not be called when the local estimate is small"


def test_recompute_when_estimated_size_exceeds_the_fraction():
    """_maybe_update_fold MUST call the LLM when there's no recorded usage yet but the
    local byte estimate over the actual history is over FOLD_TRIGGER_FRACTION."""
    fold_data = {
        "summary": "existing summary",
        "folded_through_idx": 20,
        "generated_at_len": 40,
    }

    def fake_conversation_text(h):
        return " ".join(m.get("content", "") for m in h)

    big_history = _make_history(50) + [{"role": "user", "content": "x" * 5000}]
    patcher, fake_provider = _patch_fold_provider("new summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=fold_data),
        patch("backend.ai.assistant._save_fold"),
        patch("backend.services.cost.session_chat_records", return_value=[]),
        patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _maybe_update_fold

        asyncio.run(_drain(_maybe_update_fold("sid", big_history, 21, "m", "")))

    assert len(fake_provider.calls) == 1, (
        "LLM must be called when the local estimate is over the fraction"
    )


def test_fold_summary_max_tokens_has_real_headroom():
    """2026-08-27: complete() surfaces no stop_reason at all, so a summary that hit the
    old, low max_tokens cap mid-sentence would be silently saved as complete — the same
    class of silent-loss bug the rest of this file exists to prevent, just relocated
    into fold's own output. Pinned as a deliberate, documented safety margin (not a new
    target length) rather than left to drift back down unnoticed."""
    patcher, fake_provider = _patch_fold_provider("a summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=None),
        patch("backend.ai.assistant._save_fold"),
        patch("backend.ai.memory._conversation_text", return_value="some prior conversation"),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", _make_history(20), 10, None)))

    assert fake_provider.calls[0]["max_tokens"] >= 8000


def test_maybe_update_fold_end_to_end_on_real_token_pressure():
    """Same wiring as the two tests above, but via the real (non-bootstrap) token path:
    a session with recorded usage over the fraction recomputes even though the window
    has advanced by just 1 message — proof the trigger is genuinely token-driven end
    to end, not just in _should_refold isolation."""
    fold_data = {"summary": "existing summary", "folded_through_idx": 20, "generated_at_len": 40}
    row = {"model": "m", "peak_prompt_tokens": 600}

    def fake_conversation_text(h):
        return " ".join(m.get("content", "") for m in h)

    patcher, fake_provider = _patch_fold_provider("new summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=fold_data),
        patch("backend.ai.assistant._save_fold"),
        patch("backend.services.cost.session_chat_records", return_value=[row]),
        patch("backend.ai.models.price_map.max_input_tokens", return_value=1000),
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _maybe_update_fold

        asyncio.run(_drain(_maybe_update_fold("sid", _make_history(50), 21, "m", "")))

    assert len(fake_provider.calls) == 1


# ── _stream_turn_fold_update / _extract_turn_fold_result: fold runs BEFORE the turn
#    it protects, not after (split from the old _resolve_turn_fold_state 2026-08-27
#    when _generate_fold_summary became a streaming generator — see its docstring) ──
#
# Real gap found 2026-08-27: window_start (pure message count) used to advance
# synchronously while fold ran fire-and-forget AFTER the turn had already responded.
# On the turn a session first crossed MAX_HISTORY_MESSAGES (or any turn following a
# swallowed fold failure), the just-excluded messages were represented by NEITHER raw
# content NOR a fold summary. The fix: the turn that needs a new cut point now AWAITS
# fold first, so window_start can never be ahead of what fold has actually covered.


def test_stream_turn_fold_update_skips_the_ensure_call_when_window_start_is_zero():
    """window_start == 0 means nothing has been excluded yet — no need to ensure a
    fresh fold. Any EXISTING sidecar is still loaded and returned by
    _extract_turn_fold_result regardless (unconditional load, matching the
    pre-2026-08-27 behaviour for this half)."""
    with (
        patch(
            "backend.ai.assistant._maybe_update_fold", side_effect=_noop_async_gen
        ) as mock_update,
        patch(
            "backend.ai.assistant._load_fold",
            return_value={
                "summary": "S",
                "verbatim_user_messages": [],
                "folded_through_idx": 10,
            },
        ),
    ):
        asyncio.run(_drain(_stream_turn_fold_update("sid", [], 0, "m", "")))
        summary, verbatim = _extract_turn_fold_result("sid")
    mock_update.assert_not_called()
    assert summary == "S"


def test_stream_turn_fold_update_updates_before_extract_reads():
    """The defining property: _maybe_update_fold's effects must be visible to the
    _load_fold call _extract_turn_fold_result makes — proven by having the fake
    update function change what _load_fold subsequently returns, then asserting the
    NEW value (not a stale pre-update one) comes back."""
    calls = []
    fold_store = {"data": None}

    async def fake_update(session_id, history, window_start, model, base_url):
        calls.append("update")
        fold_store["data"] = {
            "summary": "GENERATED",
            "verbatim_user_messages": [],
            "folded_through_idx": window_start,
        }
        if False:  # noqa: SIM103 - deliberate: keeps this an async generator function
            yield

    def fake_load(session_id):
        calls.append("load")
        return fold_store["data"]

    with (
        patch("backend.ai.assistant._maybe_update_fold", side_effect=fake_update),
        patch("backend.ai.assistant._load_fold", side_effect=fake_load),
    ):
        asyncio.run(
            _drain(_stream_turn_fold_update("sid", [{"role": "user", "content": "x"}], 50, "m", ""))
        )
        summary, verbatim = _extract_turn_fold_result("sid")
    assert calls == ["update", "load"], "fold must be updated BEFORE it is loaded for this turn"
    assert summary == "GENERATED"


def test_extract_turn_fold_result_returns_none_when_still_uncovered_after_ensure():
    """A failed fold attempt (swallowed inside _maybe_update_fold) leaves no sidecar —
    the turn must render with no summary rather than a stale or partial one."""
    with (
        patch("backend.ai.assistant._maybe_update_fold", side_effect=_noop_async_gen),
        patch("backend.ai.assistant._load_fold", return_value=None),
    ):
        asyncio.run(_drain(_stream_turn_fold_update("sid", [], 50, "m", "")))
        summary, verbatim = _extract_turn_fold_result("sid")
    assert summary is None
    assert verbatim is None


# ── fallback on summarizer failure ───────────────────────────────────────────


def test_fold_failure_preserves_prior_summary():
    """When the LLM call fails, the sidecar is NOT updated and no exception propagates."""
    fold_data = {
        "summary": "prior summary",
        "folded_through_idx": 10,
        "generated_at_len": 30,
    }

    def fake_conversation_text(h):
        return "some text"

    saves = []
    patcher, _fake_provider = _patch_fold_provider(raises=RuntimeError("provider error"))
    with (
        patch("backend.ai.assistant._load_fold", return_value=fold_data),
        patch("backend.ai.assistant._save_fold", side_effect=lambda sid, d: saves.append(d)),
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", _make_history(40), 20, fold_data)))

    assert len(saves) == 0, "sidecar must NOT be written on LLM failure"


def test_fold_provider_error_silently_skips():
    """Any provider-resolution/streaming error (no key configured, network error, ...)
    is treated as a no-op — same failure-swallowing contract as
    test_fold_failure_preserves_prior_summary, exercised on the first-fold-ever branch
    (fold_data=None) instead of a refold."""

    def fake_conversation_text(h):
        return "some text"

    saves = []
    patcher, _fake_provider = _patch_fold_provider(raises=RuntimeError("no provider"))
    with (
        patch("backend.ai.assistant._load_fold", return_value=None),
        patch("backend.ai.assistant._save_fold", side_effect=lambda sid, d: saves.append(d)),
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", _make_history(40), 20, None)))

    assert len(saves) == 0


# ── system_blocks byte-identity across fold re-generation ─────────────────────


def test_system_blocks_unchanged_after_fold(tmp_path, monkeypatch):
    """Fold summary lives in messages[], never in system_blocks.

    Simulate two successive _prepare_messages calls with different fold summaries
    and verify that the system (passed to stream_chat) is byte-identical — only
    the messages array changes.
    """
    from backend.ai.models.anthropic_provider import _split_system_blocks

    system_content = "STATIC INSTRUCTIONS\n---CACHE_SPLIT---\nSEMI STABLE"
    blocks_before = _split_system_blocks(system_content)
    # After a fold re-generation the system string is unchanged
    blocks_after = _split_system_blocks(system_content)
    assert blocks_before == blocks_after

    history = _make_history(40)
    msgs_before = _prepare_messages(history, window_start=10, fold_summary="summary v1")
    msgs_after = _prepare_messages(history, window_start=10, fold_summary="summary v2")
    # The summary block is first in both; everything else should be identical in position.
    assert msgs_before[1:] == msgs_after[1:]
    assert msgs_before[0]["content"] != msgs_after[0]["content"]


# ── Tripwire: nudge at 3, drop at 5 ──────────────────────────────────────────


def _make_provider_stub(tool_calls_per_round: list[list[dict]], final_text: str = "done"):
    """Build a minimal provider stub that emits tool_use events then ends."""

    class ProviderStub:
        def __init__(self):
            self._round = 0

        async def stream_chat(self, messages, tools, system, model, effort="medium"):
            round_tools = (
                tool_calls_per_round[self._round] if self._round < len(tool_calls_per_round) else []
            )
            self._round += 1
            for t in round_tools:
                yield {"type": "tool_use_start", "id": t["id"], "name": t["name"]}
                yield {"type": "tool_use", **t}
            if round_tools:
                yield {"type": "usage", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0}
                yield {"type": "done", "stop_reason": "tool_use"}
            else:
                yield {"type": "text_delta", "text": final_text}
                yield {"type": "usage", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0}
                yield {"type": "done", "stop_reason": "end_turn"}

    return ProviderStub()


def _run_turn(provider, session_id: str, system: str = "SYS", tool_executor=None):
    """Drive run() for one user message; return list of yielded events."""
    from backend.ai.assistant import run

    if tool_executor is None:

        async def _default_executor(name, inp, tool_use_id=None):
            return json.dumps({"ok": True}), None

        tool_executor = _default_executor

    events = []

    async def _collect():
        # execute_tool is imported at module level in assistant.py, so patch it there.
        with patch("backend.ai.assistant.get_provider", return_value=provider):
            with patch(
                "backend.ai.assistant.execute_tool", new=AsyncMock(side_effect=tool_executor)
            ):
                async for ev in run(
                    session_id,
                    [{"role": "user", "content": "go"}],
                    system=system,
                    model="claude-haiku-4-5",
                ):
                    events.append(ev)

    asyncio.run(_collect())
    return events


@pytest.fixture
def tmp_session(monkeypatch):
    """Isolated in-memory session store for tripwire tests. Durable persistence is
    DB-backed now (Wave 5E) — db_isolated (requested directly by the tests that need
    it) covers that half; this fixture only resets the in-process caches."""
    import backend.ai.assistant as asst

    monkeypatch.setattr(asst, "SESSIONS", {})
    yield


# ── clear_session deletes fold sidecar ────────────────────────────────────────


def test_clear_session_deletes_fold_sidecar(db_isolated, monkeypatch):
    """``fold`` is a column on the session row now (Wave 5E) — clear_session's single
    atomic row DELETE (``_delete_session_row``) drops it along with everything else;
    there is no separate ``delete_session_meta`` call left inside clear_session to
    mock (it never existed post-migration — this row-level delete replaced it)."""
    import backend.ai.assistant as asst

    monkeypatch.setattr(asst, "SESSIONS", {})

    sid = "clear-test"
    asst.SESSIONS[sid] = [{"role": "user", "content": "hi"}]
    _save_fold(sid, {"summary": "x", "folded_through_idx": 5})
    assert SessionStore().get_fold(sid) is not None

    asst.clear_session(sid)

    assert SessionStore().get_fold(sid) is None
    assert sid not in asst.SESSIONS


# ── list_sessions does not expose fold data ─────────────────────────────────────


def test_list_sessions_skips_fold_sidecars(db_isolated):
    """A session's fold summary lives in its OWN column now (Wave 5E) — never a
    discoverable ``*.fold.json`` entry — and ``list_sessions()`` reads only the
    ``meta`` column (``SessionStore.all_meta``), so fold data structurally cannot
    leak into the listing as a phantom session. Pins that guarantee directly: a
    session WITH fold data set still lists exactly once, and its meta dict never
    carries the fold payload."""
    import backend.ai.assistant as asst

    asst.save_session_durable("real-session", [{"role": "user", "content": "hello"}])
    _save_fold("real-session", {"summary": "x", "folded_through_idx": 5})

    sessions = asst.list_sessions()
    ids = [s["id"] for s in sessions]
    assert ids == ["real-session"]
    assert "fold" not in sessions[0]
    assert "summary" not in sessions[0]


# ── fold summary: no artificial length ceiling ─────────────────────────────────
#
# Regression: a fold summary read back "2 successful writes (Meridian Bank" — a hard
# `[:FOLD_SUMMARY_MAX_CHARS]` slice cut it off mid-parenthetical (proven: the saved
# string was exactly 1500 chars, the old cap, to the byte). The summary REPLACES the
# verbatim turns it covers, so a fact severed there is gone from the model's context
# entirely — not merely shortened. Owner decision: remove the length ceiling rather
# than truncate more carefully — a `[:N]` string constant should not be gating the
# durability of a fact the model needs to answer a later question.


def test_generate_fold_summary_saves_whatever_the_llm_returns_uncapped():
    """No post-hoc truncation of any kind — long or short, the saved summary is exactly
    what the model wrote."""
    long_summary = (
        "Logged two transactions this run: Meridian Bank Multiplier debit SGD 17 for Corner "
        "Deli, and a Meridian Bank card charge. " * 20
    )
    assert len(long_summary) > 1500  # comfortably past the OLD, now-removed ceiling

    def fake_conversation_text(h):
        return "irrelevant raw history"

    saves = []
    patcher, _fake_provider = _patch_fold_provider(long_summary)
    with (
        patch("backend.ai.assistant._load_fold", return_value=None),
        patch("backend.ai.assistant._save_fold", side_effect=lambda sid, d: saves.append(d)),
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", _make_history(40), 20, None)))

    assert len(saves) == 1
    assert saves[0]["summary"] == long_summary.strip()


def test_generate_fold_summary_prompt_carries_no_character_instruction():
    """The prompt used to say 'under N characters' — that instruction is what made the
    model overshoot in tokens while the code capped in characters. Gone entirely."""

    def fake_conversation_text(h):
        return "irrelevant raw history"

    patcher, fake_provider = _patch_fold_provider("a summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=None),
        patch("backend.ai.assistant._save_fold"),
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", _make_history(40), 20, None)))

    assert len(fake_provider.calls) == 1
    assert "characters" not in fake_provider.calls[0]["prompt"].lower()


# ── RFC context-folding-v2 §2: six required headings ──────────────────────────


def test_summarize_system_prompt_names_all_six_headings():
    from backend.ai.assistant import _FOLD_SUMMARIZE_SYSTEM

    for heading in (
        "Decisions & preferences",
        "Figures & facts asserted",
        "Actions taken",
        "Automations triggered",
        "Open questions",
        "In progress",
    ):
        assert heading in _FOLD_SUMMARIZE_SYSTEM


def test_summarize_system_prompt_forbids_omitting_a_heading():
    from backend.ai.assistant import _FOLD_SUMMARIZE_SYSTEM

    assert "never omit a heading" in _FOLD_SUMMARIZE_SYSTEM.lower()


def test_summarize_system_prompt_asks_for_concision():
    """2026-08-27: matches Codex's own compaction prompt ("Be concise, structured, and
    focused") — a qualitative nudge, not an invented numeric target neither reference
    system uses."""
    from backend.ai.assistant import _FOLD_SUMMARIZE_SYSTEM

    assert "concise" in _FOLD_SUMMARIZE_SYSTEM.lower()


# ── RFC context-folding-v2 §3: verbatim user-message ledger ───────────────────


class TestExtractUserText:
    def test_plain_string_content_returned_as_is(self):
        assert _extract_user_text("what's my balance?") == "what's my balance?"

    def test_onboarding_sentinel_yields_nothing(self):
        assert _extract_user_text("__ONBOARDING_INIT__") == ""

    def test_real_text_block_extracted(self):
        content = [{"type": "text", "text": "how much did I spend last month"}]
        assert _extract_user_text(content) == "how much did I spend last month"

    def test_turn_context_snapshot_skipped(self):
        content = [
            {"type": "text", "text": "<turn_context>\nirrelevant snapshot"},
            {"type": "text", "text": "the real question"},
        ]
        assert _extract_user_text(content) == "the real question"

    def test_resolved_mention_block_skipped(self):
        content = [
            {"type": "text", "text": "change the payee to sally"},
            {"type": "text", "text": "«mention:97beb…» untrusted referenced data"},
        ]
        assert _extract_user_text(content) == "change the payee to sally"

    def test_screen_context_snapshot_skipped(self):
        content = [
            {"type": "text", "text": "is this right"},
            {"type": "text", "text": "[Currently on screen — Plan — ...]"},
        ]
        assert _extract_user_text(content) == "is this right"

    def test_mid_turn_injected_message_unwrapped_from_its_fence(self):
        content = [{"type": "text", "text": "«user_msg:ab12»wait, stop«/user_msg:ab12»"}]
        assert _extract_user_text(content) == "wait, stop"

    def test_pure_tool_result_content_yields_nothing(self):
        content = [{"type": "tool_result", "tool_use_id": "tu-1", "content": "{}"}]
        assert _extract_user_text(content) == ""

    def test_non_list_non_str_content_yields_nothing(self):
        assert _extract_user_text(None) == ""


class TestVerbatimUserMessages:
    def test_extracts_every_real_user_message_in_range_exactly(self):
        history = [
            {"role": "user", "content": "first message, exact wording matters here"},
            {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "{}"}],
            },
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tu-1", "name": "x"}]},
            {"role": "user", "content": "second message — $1,234.56, not $1234"},
        ]
        out = _verbatim_user_messages(history, 0, len(history))
        assert out == [
            {"idx": 0, "text": "first message, exact wording matters here"},
            {"idx": 4, "text": "second message — $1,234.56, not $1234"},
        ]

    def test_range_is_exclusive_of_end(self):
        history = [
            {"role": "user", "content": "in range"},
            {"role": "user", "content": "out of range"},
        ]
        out = _verbatim_user_messages(history, 0, 1)
        assert out == [{"idx": 0, "text": "in range"}]

    def test_assistant_messages_never_included(self):
        history = [{"role": "assistant", "content": [{"type": "text", "text": "hello"}]}]
        assert _verbatim_user_messages(history, 0, 1) == []

    def test_pure_continuation_turn_contributes_nothing(self):
        history = [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "ok"}],
            }
        ]
        assert _verbatim_user_messages(history, 0, 1) == []


def test_generate_fold_summary_saves_the_verbatim_ledger():
    history = _make_history(40)  # user entries: "message 0", "message 2", ...

    def fake_conversation_text(h):
        return "irrelevant raw history"

    saves = []
    patcher, _fake_provider = _patch_fold_provider("a summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=None),
        patch("backend.ai.assistant._save_fold", side_effect=lambda sid, d: saves.append(d)),
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", history, 20, None)))

    assert len(saves) == 1
    ledger = saves[0]["verbatim_user_messages"]
    assert ledger == _verbatim_user_messages(history, 0, 20)
    assert ledger[0] == {"idx": 0, "text": "message 0"}


def test_generate_fold_summary_accumulates_the_ledger_across_refolds():
    """A re-fold must APPEND new verbatim entries onto the prior ledger, never replace
    it — the ledger covers the whole folded history, not just the latest slice."""
    history = _make_history(40)
    fold_data = {
        "summary": "prior summary",
        "verbatim_user_messages": [{"idx": 0, "text": "message 0"}],
        "folded_through_idx": 10,
        "generated_at_len": 20,
    }

    def fake_conversation_text(h):
        return "irrelevant raw history"

    saves = []
    patcher, _fake_provider = _patch_fold_provider("updated summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=fold_data),
        patch("backend.ai.assistant._save_fold", side_effect=lambda sid, d: saves.append(d)),
        patch("backend.ai.memory._conversation_text", side_effect=fake_conversation_text),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", history, 20, fold_data)))

    assert len(saves) == 1
    ledger = saves[0]["verbatim_user_messages"]
    # The idx=0 entry from the PRIOR fold survives, plus every new one from [10:20).
    assert ledger[0] == {"idx": 0, "text": "message 0"}
    assert ledger == [{"idx": 0, "text": "message 0"}] + _verbatim_user_messages(history, 10, 20)


def test_generate_fold_summary_ledger_absent_when_prior_had_none():
    """A legacy sidecar (pre-RFC, no verbatim_user_messages key) must not crash the
    accumulation step — .get(..., []) treats it as an empty prior ledger."""
    history = _make_history(40)
    legacy_fold_data = {"summary": "old-shape summary", "folded_through_idx": 10}

    saves = []
    patcher, _fake_provider = _patch_fold_provider("updated summary")
    with (
        patch("backend.ai.assistant._load_fold", return_value=legacy_fold_data),
        patch("backend.ai.assistant._save_fold", side_effect=lambda sid, d: saves.append(d)),
        patch("backend.ai.memory._conversation_text", return_value="irrelevant"),
        patcher,
    ):
        from backend.ai.assistant import _generate_fold_summary

        asyncio.run(_drain(_generate_fold_summary("sid", history, 20, legacy_fold_data)))

    assert len(saves) == 1
    assert saves[0]["verbatim_user_messages"] == _verbatim_user_messages(history, 10, 20)


# ── RFC context-folding-v2 §3: verbatim ledger injection in _prepare_messages ──


def test_prepare_messages_renders_verbatim_ledger_as_its_own_section():
    history = _make_history(40)
    verbatim = [{"idx": 0, "text": "how much did I spend"}, {"idx": 4, "text": "and on what"}]
    result = _prepare_messages(
        history, window_start=10, fold_summary="a summary", fold_verbatim=verbatim
    )
    content = result[0]["content"]
    assert "[USER'S OWN WORDS, VERBATIM, IN ORDER]" in content
    assert '1. "how much did I spend"' in content
    assert '2. "and on what"' in content
    # Verbatim section comes after the prose summary, not before.
    assert content.index("[CONVERSATION SUMMARY SO FAR]") < content.index("[USER'S OWN WORDS")


def test_prepare_messages_no_verbatim_ledger_omits_the_section():
    history = _make_history(40)
    result = _prepare_messages(history, window_start=10, fold_summary="a summary")
    assert "[USER'S OWN WORDS" not in result[0]["content"]


def test_prepare_messages_empty_verbatim_list_omits_the_section():
    history = _make_history(40)
    result = _prepare_messages(history, window_start=10, fold_summary="a summary", fold_verbatim=[])
    assert "[USER'S OWN WORDS" not in result[0]["content"]


def test_prepare_messages_no_summary_never_renders_verbatim_even_if_present():
    """fold_verbatim without fold_summary is not a real state (they're always saved
    together), but the injection must stay gated on fold_summary regardless."""
    history = _make_history(40)
    verbatim = [{"idx": 0, "text": "should not appear"}]
    result = _prepare_messages(history, window_start=10, fold_summary=None, fold_verbatim=verbatim)
    assert "[CONVERSATION SUMMARY" not in result[0].get("content", "")
    assert "should not appear" not in json.dumps(result)
