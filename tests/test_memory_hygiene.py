"""
Regression tests for memory hygiene — MEM-2 (volatile snapshots stored as facts) and the
stricter extractor (one routing guide, the user's instructions, every existing fact listed in
full, sensitive facts flagged, re-confirmed facts reported).

MEM-2: extract_and_merge must (a) instruct the extractor to prefer durable, low-churn
facts and avoid memorizing live-readable figures (share counts / prices / balances); and
(b) the write choke deterministically drops a background-written fact that still carries a
perishable figure, so a stale share count / price / balance never reaches the prompt.

Prompt-content assertions mirror test_grounding_prompt.py. Fully isolated — no network/LLM.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import memory
from backend.ai.memory_structure import ROUTING_GUIDE
from backend.ai.memory_write import is_perishable_figure
from backend.db import memory_store


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


@pytest.fixture
def isolated(tmp_path, db_isolated):
    return tmp_path


# ── MEM-2: extractor prompt guidance ────────────────────────────────────────────


def test_extractor_prompt_prefers_durable_and_avoids_live_figures(isolated, monkeypatch):
    captured: dict[str, str] = {}

    class _FakeProvider:
        async def complete(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProvider())

    history = [
        {"role": "user", "content": "I hold META and care about my long-term allocation."},
        {"role": "assistant", "content": "Noted."},
    ]
    asyncio.run(memory.extract_and_merge(history))

    assert "prompt" in captured, "extractor never called the provider"
    n = _norm(captured["prompt"])

    # Prefer durable, low-churn facts.
    assert "prefer durable, low-churn facts" in n
    assert "preferences, goals, risk posture, account structure" in n
    # Avoid memorizing live-readable figures; the choke drops one that slips through.
    assert "avoid memorizing quantities, share counts, live prices, or account balances" in n
    assert "a fact that carries one is dropped" in n
    # Where a fact goes is decided by the one routing guide, and the reply names a section.
    assert _norm(ROUTING_GUIDE) in n
    assert '"section": "you|money|plans|open_items|other"' in captured["prompt"]


# ── MEM-2: deterministic perishable-figure guard ────────────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        "User holds META (12.5 shares), trading at ~$598 (underwater).",
        "AMD 16 shares ~$522.",
        "MSFT 38 shares trading at $441.",
        "Meridian Bank Multiplier balance of $12,000.",
        "Holds 890.4 units of ONWAI.",
    ],
)
def test_perishable_figures_are_recognised(content):
    assert is_perishable_figure(content), f"should be a ledger figure: {content!r}"


@pytest.mark.parametrize(
    "content",
    [
        "Risk tolerance is moderate.",
        "Targeting retirement at 45.",
        "Does not want crypto above 10% of total portfolio.",
        "Holds META as a long-term equity position.",
        "Banks primarily with Meridian Bank in Singapore.",
    ],
)
def test_genuine_preferences_are_not_ledger_figures(content):
    assert not is_perishable_figure(content), f"should NOT be a ledger figure: {content!r}"


def test_extract_and_merge_drops_a_perishable_figure_and_keeps_a_preference(isolated, monkeypatch):
    """End-to-end: a figure-bearing fact the model emits is dropped; a plain preference is filed."""

    class _FakeProvider:
        async def complete(self, prompt, **kwargs):
            return json.dumps(
                {
                    "new": [
                        {
                            "content": "User holds META (12.5 shares) trading at ~$598.",
                            "section": "money",
                            "topic": "Investing",
                        },
                        {
                            "content": "Risk tolerance is moderate.",
                            "section": "money",
                            "topic": "Investing",
                        },
                    ],
                    "updates": [],
                    "remove": [],
                }
            )

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProvider())

    history = [
        {"role": "user", "content": "Talked through META and my risk posture."},
        {"role": "assistant", "content": "Noted."},
    ]
    asyncio.run(memory.extract_and_merge(history))

    (risk,) = memory_store.MemoryStore().all_facts()
    assert risk["content"] == "Risk tolerance is moderate."
    assert (risk["section"], risk["topic"], risk["state"]) == ("money", "Investing", "live")
    assert risk["source"] == "implicit" and risk["expires_at"] is None and "tier" not in risk


# ── The stricter extractor ──────────────────────────────────────────────────────


class _Capture:
    def __init__(self, reply: str = '{"new": [], "updates": [], "remove": []}'):
        self.reply = reply
        self.prompts: list[str] = []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.reply


def _stored(fid: str, content: str, **extra) -> dict:
    return {
        "id": fid,
        "content": content,
        "source": "implicit",
        "expires_at": None,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        **extra,
    }


_CHAT = [
    {"role": "user", "content": "I keep my emergency fund at the credit union, please remember."},
    {"role": "assistant", "content": "Noted."},
]


def test_extractor_prompt_renders_routing_guide_and_instructions(isolated, monkeypatch):
    from backend.services import profile as profile_service

    profile_service.write_profile(
        {**profile_service.read_profile(), "notes": "Always answer in plain English."}
    )
    cap = _Capture()
    monkeypatch.setattr(memory, "_provider", lambda: cap)

    asyncio.run(memory.extract_and_merge(_CHAT))

    prompt = cap.prompts[0]
    assert ROUTING_GUIDE in prompt
    assert "Always answer in plain English." in prompt
    n = _norm(prompt)
    assert "do not store what these instructions already say" in n
    assert "third person" in n and "preferences" in n
    assert "conversation or run state" in n
    assert "full card, account, id or passport number" in n
    assert "sensitive" in n and "true" in n
    assert '"mentioned"' in prompt


def test_extractor_lists_a_saved_fact_whatever_the_setting(isolated, monkeypatch):
    from backend.db.memory_settings_store import MemorySettingsStore

    memory_store.MemoryStore().bulk_insert(
        [
            _stored(
                "m_rent", "Rents a flat in Tampines", state="live", section="money", topic="Housing"
            ),
            _stored(
                "m_health",
                "Has a chronic condition",
                state="live",
                section="you",
                topic="Profile",
            ),
        ]
    )
    cap = _Capture()
    monkeypatch.setattr(memory, "_provider", lambda: cap)

    MemorySettingsStore().update({"sensitive_topics": False})
    asyncio.run(memory.extract_and_merge(_CHAT))
    assert "Tampines" in cap.prompts[0] and "chronic condition" in cap.prompts[0]

    MemorySettingsStore().update({"sensitive_topics": True})
    asyncio.run(memory.extract_and_merge(_CHAT))
    assert "chronic condition" in cap.prompts[1]


def test_extractor_lists_every_fact_in_full_with_its_place(isolated, monkeypatch):
    store = memory_store.MemoryStore()
    long_text = "Z" * 500
    store.bulk_insert(
        [
            _stored(
                "m_live", "Rents a flat in Tampines", state="live", section="money", topic="Housing"
            ),
            _stored("m_open", "Waiting on the tax letter", state="live", section="open_items"),
            _stored("m_old", "Prefers index funds", state="unsorted"),
            _stored("m_legacy", "Banks with Meridian Bank"),
            _stored("m_long", long_text, state="live", section="other", topic="Misc"),
            *[_stored(f"m_{i}", f"Filler note number {i}", state="unsorted") for i in range(150)],
        ]
    )
    cap = _Capture()
    monkeypatch.setattr(memory, "_provider", lambda: cap)

    asyncio.run(memory.extract_and_merge(_CHAT))

    prompt = cap.prompts[0]
    assert "m_live: [Money · Housing] Rents a flat in Tampines" in prompt
    assert "m_open: [Recent] Waiting on the tax letter" in prompt
    assert "m_old: [unsorted] Prefers index funds" in prompt
    assert "m_legacy: [unsorted] Banks with Meridian Bank" in prompt
    assert f"m_long: [Other · Misc] {long_text}" in prompt
    assert "m_149: [unsorted]" in prompt
    assert "Housing" in prompt.split("Topics in use —")[1]


def test_extractor_reports_mentioned_facts(isolated, monkeypatch):
    store = memory_store.MemoryStore()
    store.bulk_insert(
        [
            _stored(
                "m_plan",
                "Plans to move to Lisbon",
                state="live",
                section="plans",
                topic="Move abroad",
                mentioned_at="2026-06-01T00:00:00+00:00",
            )
        ]
    )
    cap = _Capture(
        json.dumps({"new": [], "updates": [], "remove": [], "mentioned": ["m_plan", "ghost"]})
    )
    monkeypatch.setattr(memory, "_provider", lambda: cap)

    asyncio.run(memory.extract_and_merge(_CHAT))

    (fact,) = store.all_facts()
    assert fact["mentioned_at"] > "2026-06-01T00:00:00+00:00"
    assert fact["content"] == "Plans to move to Lisbon"


def test_a_sensitive_fact_is_stored_only_when_the_setting_allows_it(isolated, monkeypatch):
    from backend.db.memory_settings_store import MemorySettingsStore

    reply = json.dumps(
        {
            "new": [
                {
                    "content": "Has type 2 diabetes",
                    "section": "you",
                    "topic": "Profile",
                    "sensitive": True,
                }
            ]
        }
    )
    monkeypatch.setattr(memory, "_provider", lambda: _Capture(reply))

    asyncio.run(memory.extract_and_merge(_CHAT))
    assert memory_store.MemoryStore().all_facts() == []

    MemorySettingsStore().set_sensitive_topics(True)
    asyncio.run(memory.extract_and_merge(_CHAT))
    (fact,) = memory_store.MemoryStore().all_facts()
    assert fact["content"] == "Has type 2 diabetes"
    assert "sensitive" not in fact


def test_no_consolidation_machinery_remains():
    for name in (
        "consolidate",
        "_CONSOLIDATE_SYSTEM",
        "CONSOLIDATE_THRESHOLD",
        "CONSOLIDATE_MIN_GROWTH",
        "CONSOLIDATE_BATCH_SIZE",
        "EXTRACT_EXISTING_CAP",
        "FACT_LISTING_TRUNCATE",
    ):
        assert not hasattr(memory, name), name
