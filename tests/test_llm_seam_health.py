"""`llm_seam.complete_json` records every background call's outcome for Settings → AI, and the
recording never changes what the call returns or raises."""

from __future__ import annotations

import asyncio

import pytest

from backend.core import background_ai_health as health
from backend.core import llm_seam
from backend.core.errors import ExternalServiceError


@pytest.fixture(autouse=True)
def _fresh():
    health.reset_for_tests()
    yield
    health.reset_for_tests()


def _stub(monkeypatch, reply):
    async def _complete(prompt, **kwargs):
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _row(source):
    return next(r for r in health.snapshot() if r["source"] == source)


def test_a_valid_answer_is_recorded_as_a_success(monkeypatch):
    _stub(monkeypatch, '{"ok": true}')
    assert asyncio.run(llm_seam.complete_json("x", source="briefing")) == {"ok": True}
    row = _row("briefing")
    assert (row["status"], row["calls_24h"], row["failures_24h"]) == ("working", 1, 0)


def test_no_usable_answer_is_recorded_and_still_returns_none(monkeypatch):
    _stub(monkeypatch, "not json at all")
    assert asyncio.run(llm_seam.complete_json("x", source="news_screen", max_reasks=1)) is None
    row = _row("news_screen")
    assert row["last_failure_kind"] == "no_usable_answer"
    assert row["calls_24h"] == 1  # one call, however many reasks it took


def test_a_refusal_is_recorded_and_the_error_still_reaches_the_caller(monkeypatch):
    refusal = ExternalServiceError(
        "Claude Code CLI reported an error: usage policy … Details: `[reasoning_extraction]`"
    )
    _stub(monkeypatch, refusal)
    with pytest.raises(ExternalServiceError) as raised:
        asyncio.run(llm_seam.complete_json("x", source="analysis"))
    assert raised.value is refusal
    row = _row("analysis")
    assert (row["last_failure_kind"], row["last_failure_detail"]) == (
        "refused",
        "reasoning_extraction",
    )


def test_a_broken_recorder_never_breaks_the_call(monkeypatch):
    _stub(monkeypatch, '{"ok": true}')

    def _explode(*args, **kwargs):
        raise RuntimeError("recorder down")

    monkeypatch.setattr(health, "record_success", _explode)
    assert asyncio.run(llm_seam.complete_json("x", source="briefing")) == {"ok": True}
