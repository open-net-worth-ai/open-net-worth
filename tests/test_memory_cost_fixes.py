"""Regression tests for the 2026-07-11 memory cost fixes.

Observed on a real book: 29 chat turns cost $0.72 on haiku — extraction paid ~9.6k input
tokens even for "thanks" turns. The fix pinned here (the consolidator the other two covered is
gone; the daily tidy pass is tests/test_memory_tidy.py):

  Trivial-turn extraction skip (_only_trivial_new_messages, stale-cursor-safe)
"""

from __future__ import annotations

import asyncio

import pytest

import backend.ai.memory as memory
import backend.db.memory_settings_store as settings_store_mod


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # The per-session extraction cursor (SessionStore.set/get_extraction_cursor) is DB-backed —
    # db_isolated already gives this test a fresh, empty database.
    settings_store_mod.MemorySettingsStore().set_enabled(True)
    return tmp_path


# ── Trivial-turn extraction skip ──────────────────────────────────────────────


def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": text}


class TestTrivialTurnSkip:
    def _run_extract(self, history, cursor, monkeypatch, isolated):
        calls: list[str] = []

        class _Prov:
            async def complete(self, prompt, **kw):
                calls.append(prompt)
                return '{"new": [], "updates": [], "remove": []}'

        monkeypatch.setattr(memory, "_provider", lambda: _Prov())
        from backend.db.session_store import SessionStore

        SessionStore().upsert_meta("s1", {"title": "t"})
        memory._save_cursor("s1", cursor)
        asyncio.run(memory.extract_and_merge(history, session_id="s1"))
        return calls

    def test_pure_ack_skips_llm_and_advances_cursor(self, isolated, monkeypatch):
        history = [
            _msg("user", "Should I rebalance into bonds given my age?"),
            _msg("assistant", "Here's my analysis…"),
            _msg("user", "thanks!"),
            _msg("assistant", "Anytime."),
        ]
        calls = self._run_extract(history, cursor=2, monkeypatch=monkeypatch, isolated=isolated)
        assert calls == []  # no LLM call
        assert memory._load_cursor("s1") == 4  # cursor advanced past the trivial tail

    def test_substantive_new_message_still_extracts(self, isolated, monkeypatch):
        history = [
            _msg("user", "hi"),
            _msg("assistant", "hello"),
            _msg("user", "I want to retire at 45 and I'm very risk averse"),
        ]
        calls = self._run_extract(history, cursor=2, monkeypatch=monkeypatch, isolated=isolated)
        assert len(calls) == 1

    def test_short_but_meaningful_is_not_skipped(self, isolated, monkeypatch):
        # 'I hate risk' is short but NOT an ack — must extract.
        history = [_msg("user", "plan question"), _msg("user", "I hate risk")]
        calls = self._run_extract(history, cursor=1, monkeypatch=monkeypatch, isolated=isolated)
        assert len(calls) == 1

    def test_first_run_never_skips(self, isolated, monkeypatch):
        history = [_msg("user", "ok")]
        calls = self._run_extract(history, cursor=0, monkeypatch=monkeypatch, isolated=isolated)
        assert len(calls) == 1  # backfill path untouched

    def test_stale_cursor_backfills_instead_of_skipping(self, isolated, monkeypatch):
        # Regression: cursor(1000) > len(history) once wrongly skipped the reset path.
        history = [_msg("user", "I moved to Singapore and my tax residency changed")]
        calls = self._run_extract(history, cursor=1000, monkeypatch=monkeypatch, isolated=isolated)
        assert len(calls) == 1
