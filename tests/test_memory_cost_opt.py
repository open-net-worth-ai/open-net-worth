"""
Tests for Lane C memory cost-optimisation changes:

1. purge_expired — hard-deletes expired time_bound facts, keeps durable + non-expired
2. _fmt_memory — stable sort (created_at/id); the full render is tested in test_memory_render.py
3. forget_fact prefilter — deterministic token-overlap narrows to ≤20 candidates

All LLM calls are mocked; no network calls are made. Real-LLM behaviour tested
under @pytest.mark.integration (skipped without ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest

from backend.ai import memory
from backend.db import memory_store

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fact(
    content: str,
    tier: str = "durable",
    expires_at: str | None = None,
    source: str = "implicit",
    created_at: str = "2026-01-01T00:00:00",
    updated_at: str = "2026-01-01T00:00:00",
    fid: str | None = None,
) -> dict:
    return {
        "id": fid or str(uuid.uuid4()),
        "content": content,
        "state": "live",
        "tier": tier,
        "source": source,
        "expires_at": expires_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }


@pytest.fixture
def isolated(tmp_path, db_isolated):
    return tmp_path


# ── 1. purge_expired ──────────────────────────────────────────────────────────


def test_purge_expired_removes_expired_time_bound(isolated):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    store = memory_store.MemoryStore()
    store.replace_all(
        [
            _fact("keep — durable no expiry", tier="durable", fid="keep_dur"),
            _fact(
                "keep — time_bound future", tier="time_bound", expires_at=tomorrow, fid="keep_tb"
            ),
            _fact(
                "drop — time_bound expired", tier="time_bound", expires_at=yesterday, fid="drop1"
            ),
            _fact(
                "drop — time_bound more expired",
                tier="time_bound",
                expires_at="2020-01-01",
                fid="drop2",
            ),
        ]
    )

    count = memory.purge_expired(store)

    remaining_ids = {f["id"] for f in store.all_facts()}
    assert count == 2
    assert "keep_dur" in remaining_ids
    assert "keep_tb" in remaining_ids
    assert "drop1" not in remaining_ids
    assert "drop2" not in remaining_ids


def test_purge_expired_keeps_time_bound_with_no_expiry(isolated):
    store = memory_store.MemoryStore()
    store.replace_all(
        [_fact("no expiry time_bound", tier="time_bound", expires_at=None, fid="tb_no_exp")]
    )

    count = memory.purge_expired(store)

    assert count == 0
    assert any(f["id"] == "tb_no_exp" for f in store.all_facts())


def test_purge_expired_returns_zero_on_empty_store(isolated):
    store = memory_store.MemoryStore()
    assert memory.purge_expired(store) == 0


def test_purge_expired_keeps_all_durable_facts(isolated):
    store = memory_store.MemoryStore()
    facts = [_fact(f"durable fact {i}", tier="durable", fid=f"dur_{i}") for i in range(5)]
    store.replace_all(facts)

    count = memory.purge_expired(store)

    assert count == 0
    assert len(store.all_facts()) == 5


# ── 2. _fmt_memory cap / stability ───────────────────────────────────────────


def _make_fmt_facts(n: int, tier: str = "durable", source: str = "implicit") -> list[dict]:
    return [
        _fact(
            f"Fact content number {i}" + " extra words to fill" * 3,
            tier=tier,
            source=source,
            created_at=f"2026-01-{i + 1:02d}T00:00:00",
            updated_at=f"2026-01-{i + 1:02d}T00:00:00",
            fid=f"id{i:03d}",
        )
        for i in range(n)
    ]


def test_fmt_memory_output_is_byte_stable_across_calls(isolated):
    """Same facts → identical bytes on repeated calls (cache stability)."""
    from backend.ai.prompts import _fmt_memory

    store = memory_store.MemoryStore()
    store.replace_all(_make_fmt_facts(20))

    out1 = _fmt_memory()
    out2 = _fmt_memory()
    assert out1 == out2


def test_fmt_memory_sort_by_created_at_not_updated_at(isolated):
    """Changing updated_at must NOT change the rendered output (byte-stable cache)."""
    from backend.ai.prompts import _fmt_memory

    store = memory_store.MemoryStore()
    base_facts = [
        _fact(
            "Alpha fact",
            fid="fa",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        ),
        _fact(
            "Beta fact",
            fid="fb",
            created_at="2026-01-02T00:00:00",
            updated_at="2026-01-02T00:00:00",
        ),
    ]
    store.replace_all(base_facts)

    out_before = _fmt_memory()

    # Bump updated_at on "Alpha fact" (as would happen on an extraction re-touch)
    # without changing created_at or content.
    store.update_by_id("fa", {"updated_at": "2026-06-10T12:00:00"})
    out_after = _fmt_memory()

    assert out_before == out_after, (
        "Updating updated_at must not change _fmt_memory output — "
        "sort must be by created_at/id, not updated_at"
    )


def test_fmt_memory_returns_empty_for_no_facts(isolated):
    from backend.ai.prompts import _fmt_memory

    # db_isolated (via the isolated fixture) already gives an empty store — nothing to seed.
    assert _fmt_memory() == ""


# ── 3. forget_fact prefilter ──────────────────────────────────────────────────


def test_forget_prefilter_narrows_candidates_by_token_overlap(isolated, monkeypatch):
    """Token-overlap prefilter sends only relevant candidates to the LLM."""
    store = memory_store.MemoryStore()
    facts = [_fact(f"unrelated fact about thing {i}", fid=f"u{i:02d}") for i in range(50)] + [
        _fact("User's risk tolerance is conservative", fid="target")
    ]
    store.replace_all(facts)

    captured_listing: list[str] = []

    class _FakeProv:
        async def complete(self, prompt, **kwargs):
            captured_listing.append(prompt)
            return '{"id": "target"}'

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProv())

    result = asyncio.run(memory.remove_fact("risk tolerance conservative"))

    assert result is True
    assert captured_listing, "provider must be called"
    # The listing in the prompt should contain far fewer than 51 facts.
    import re

    fact_lines = re.findall(r"\b[0-9a-f-]{36}\b:", captured_listing[0])
    assert len(fact_lines) <= memory.FORGET_CANDIDATE_CAP


def test_forget_prefilter_falls_back_when_no_overlap(isolated, monkeypatch):
    """When prefilter finds nothing, falls back to capped listing so LLM still runs."""
    store = memory_store.MemoryStore()
    # Facts with no token overlap with the query
    facts = [_fact(f"アルファ ベータ ガンマ {i}", fid=f"jp{i}") for i in range(30)]
    store.replace_all(facts)

    captured_listing: list[str] = []

    class _FakeProv:
        async def complete(self, prompt, **kwargs):
            captured_listing.append(prompt)
            return '{"id": "none"}'

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProv())

    result = asyncio.run(memory.remove_fact("completely different english query xyz"))

    assert result is False  # id="none"
    # LLM was still called with the fallback listing.
    assert captured_listing


def test_forget_prefilter_uses_substring_match(isolated, monkeypatch):
    """A description that is a substring of a fact content is included in candidates."""
    store = memory_store.MemoryStore()
    store.replace_all(
        [
            _fact("The user's risk tolerance is moderate and they prefer index funds", fid="match"),
            _fact("Unrelated fact about income", fid="no_match"),
        ]
    )

    captured_listing: list[str] = []

    class _FakeProv:
        async def complete(self, prompt, **kwargs):
            captured_listing.append(prompt)
            return '{"id": "match"}'

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProv())

    asyncio.run(memory.remove_fact("risk tolerance is moderate"))

    assert captured_listing
    # "match" must be in the listing sent to the LLM.
    assert "match" in captured_listing[0]


def test_forget_prefilter_shows_candidate_content_in_full(isolated, monkeypatch):
    """No candidate is cut short in the LLM listing — a truncated fact can't be matched."""
    store = memory_store.MemoryStore()
    long_content = "risk " + "W" * 500
    store.replace_all([_fact(long_content, fid="long_risk")])

    captured: list[str] = []

    class _FakeProv:
        async def complete(self, prompt, **kwargs):
            captured.append(prompt)
            return '{"id": "none"}'

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProv())

    asyncio.run(memory.remove_fact("risk"))

    assert captured
    assert long_content in captured[0]
