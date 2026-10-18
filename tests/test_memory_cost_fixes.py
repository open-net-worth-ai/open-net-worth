"""Regression tests for the 2026-07-11 memory cost fixes.

Observed on a real book: 29 chat turns cost $0.72 on haiku — the consolidator ran
near-every-turn (69 runs / 114 extractions, ">60 facts after every extraction") and
re-emitted the whole store per run (431k output tokens); extraction paid ~9.6k input
tokens even for "thanks" turns. Three fixes, each pinned here:

  1. Growth-based consolidation cadence (_should_consolidate + state sidecar)
  2. Diff-emitting consolidator (_apply_batch_ops; output scales with changes)
  3. Trivial-turn extraction skip (_only_trivial_new_messages, stale-cursor-safe)
"""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.ai.memory as memory
import backend.db.memory_settings_store as settings_store_mod
import backend.db.memory_store as memory_store


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # Consolidate-state (MemoryConsolidateStateStore) and the per-session extraction cursor
    # (SessionStore.set/get_extraction_cursor) are both DB-backed now — no more
    # _consolidate_state_file/_cursor_file module functions to monkeypatch; db_isolated
    # already gives this test a fresh, empty database.
    settings_store_mod.MemorySettingsStore().set_enabled(True)
    return tmp_path


def _fact(i: int, content: str | None = None) -> dict:
    return {
        "id": f"f{i}",
        "content": content or f"fact number {i}",
        "tier": "durable",
        "source": "implicit",
        "expires_at": None,
        "created_at": "2026-07-01T00:00:00",
        "updated_at": f"2026-07-01T00:00:{i:02d}",
    }


# ── Fix 1: growth-based cadence ───────────────────────────────────────────────


class TestConsolidationCadence:
    def test_below_threshold_never_consolidates(self, isolated):
        assert memory._should_consolidate(60) is False
        assert memory._should_consolidate(10) is False

    def test_first_run_over_threshold_fires(self, isolated):
        assert memory._should_consolidate(61) is True

    def test_no_refire_without_growth(self, isolated):
        # A run that ended at 65 facts must NOT re-fire at 66 next turn.
        memory._save_last_consolidated_count(65)
        assert memory._should_consolidate(66) is False
        assert memory._should_consolidate(65 + memory.CONSOLIDATE_MIN_GROWTH - 1) is False

    def test_refires_after_growth(self, isolated):
        memory._save_last_consolidated_count(65)
        assert memory._should_consolidate(65 + memory.CONSOLIDATE_MIN_GROWTH) is True

    def test_shrunken_store_regrows_from_new_baseline(self, isolated):
        # Run shrank to 40 → gate stays closed until BOTH >threshold and +growth.
        memory._save_last_consolidated_count(40)
        assert memory._should_consolidate(59) is False  # under threshold
        assert memory._should_consolidate(61) is True  # 61-40 ≥ 15 and over threshold

    def test_consolidate_persists_state(self, isolated, monkeypatch):
        store = memory_store.MemoryStore()
        store.replace_all([_fact(i) for i in range(4)])

        class _Prov:
            async def complete(self, prompt, **kw):
                return '{"merge": [], "remove": []}'

        monkeypatch.setattr(memory, "_provider", lambda: _Prov())
        asyncio.run(memory.consolidate())
        assert memory._load_last_consolidated_count() == 4


# ── Fix 2: diff-emitting consolidator ────────────────────────────────────────


class TestDiffConsolidation:
    def _run(self, facts, reply, monkeypatch):
        store = memory_store.MemoryStore()
        store.replace_all(facts)

        class _Prov:
            async def complete(self, prompt, **kw):
                return reply if isinstance(reply, str) else json.dumps(reply)

        monkeypatch.setattr(memory, "_provider", lambda: _Prov())
        asyncio.run(memory.consolidate())
        return memory_store.MemoryStore().all_facts()

    def test_merge_absorbs_and_rewrites(self, isolated, monkeypatch):
        facts = [_fact(1, "dup A"), _fact(2, "dup B"), _fact(3, "unrelated keeper")]
        out = self._run(
            facts,
            {
                "merge": [{"keep_id": "f1", "absorb_ids": ["f2"], "content": "merged AB"}],
                "remove": [],
            },
            monkeypatch,
        )
        by_id = {f["id"]: f for f in out}
        assert set(by_id) == {"f1", "f3"}
        assert by_id["f1"]["content"] == "merged AB"
        assert by_id["f3"]["content"] == "unrelated keeper"  # unmentioned → untouched

    def test_remove_deletes_listed_only(self, isolated, monkeypatch):
        out = self._run(
            [_fact(1), _fact(2), _fact(3), _fact(4)],
            {"merge": [], "remove": ["f2"]},
            monkeypatch,
        )
        assert {f["id"] for f in out} == {"f1", "f3", "f4"}

    def test_noop_reply_touches_nothing(self, isolated, monkeypatch):
        facts = [_fact(1), _fact(2)]
        out = self._run(facts, {"merge": [], "remove": []}, monkeypatch)
        assert {f["id"] for f in out} == {"f1", "f2"}

    def test_unknown_ids_ignored(self, isolated, monkeypatch):
        out = self._run(
            [_fact(1), _fact(2)],
            {"merge": [{"keep_id": "ghost", "absorb_ids": ["f1"]}], "remove": ["ghost2"]},
            monkeypatch,
        )
        assert {f["id"] for f in out} == {"f1", "f2"}

    def test_old_array_shape_is_refused_batch_untouched(self, isolated, monkeypatch):
        # The pre-fix rewrite-everything reply must no longer be applied.
        out = self._run(
            [_fact(1), _fact(2)],
            [{"id": "f1", "content": "rewritten"}],
            monkeypatch,
        )
        assert {f["content"] for f in out} == {"fact number 1", "fact number 2"}

    def test_bare_remove_wipe_refused(self, isolated, monkeypatch):
        facts = [_fact(i) for i in range(6)]
        out = self._run(
            facts,
            {"merge": [], "remove": [f["id"] for f in facts]},
            monkeypatch,
        )
        assert len(out) == 6  # refused — nothing deleted

    def test_heavy_but_legit_merge_allowed(self, isolated, monkeypatch):
        # 4 dupes + 1 keeper: absorbing 3 into 1 is the INTENDED behavior (the
        # over-strict first guard refused this — regression).
        facts = [_fact(i, f"ONWAI sale variant {i}") for i in range(1, 5)]
        facts.append(_fact(9, "risk tolerance moderate"))
        out = self._run(
            facts,
            {"merge": [{"keep_id": "f4", "absorb_ids": ["f1", "f2", "f3"]}], "remove": []},
            monkeypatch,
        )
        assert {f["id"] for f in out} == {"f4", "f9"}

    def test_prompt_demands_ops_not_restated_facts(self, isolated, monkeypatch):
        prompt = memory._consolidate_batch_prompt([_fact(1)], "2026-07-11")
        assert "keep_id" in prompt and "absorb_ids" in prompt
        assert "never restate unchanged facts" in prompt.lower()


# ── Fix 3: trivial-turn extraction skip ───────────────────────────────────────


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
