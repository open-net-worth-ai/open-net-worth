"""Regression test: Intel "Scan now" 500'd whenever the store held a ticker-less story.

Macro, sector and geography stories ("US inflation & Fed policy", "Technology", "United
States") store ``ticker`` as ``None``. ``find_story_by_ticker`` did
``story.get("ticker", "").upper()`` — the key EXISTS with value ``None``, so the default never
applied and ``None.upper()`` raised ``AttributeError``. ``_persist_story``'s ticker lookup hit
it on the first ticker story the LLM returned, aborting the scan with nothing saved.

IntelStoriesStore is DB-backed; db_isolated redirects it to a throwaway database. No LLM, no
network.
"""

from __future__ import annotations

import backend.db.intel_stories_store as iss_mod
from backend.monitoring.intel_agent import _persist_story

NOW = "2026-10-27T09:00:00"


def _story(story_id: str, label: str, ticker: str | None, story_type: str) -> dict:
    return {
        "id": story_id,
        "type": story_type,
        "label": label,
        "ticker": ticker,
        "status": "monitoring",
        "current_summary": "old summary",
        "portfolio_impact": "",
        "action_items": [],
        "affected_holdings": [],
        "archived": False,
        "created_at": "2026-10-01T00:00:00",
        "updated_at": "2026-10-01T00:00:00",
    }


def _seeded_store() -> iss_mod.IntelStoriesStore:
    # The ticker-less macro story is inserted FIRST so the scan reaches it before the match.
    store = iss_mod.IntelStoriesStore()
    store.insert(_story("macro-1", "US inflation & Fed policy", None, "macro"))
    store.insert(_story("nvda-1", "NVIDIA", "NVDA", "asset"))
    return store


def test_find_story_by_ticker_skips_a_ticker_less_story(db_isolated):
    store = _seeded_store()

    assert store.find_story_by_ticker("nvda")["id"] == "nvda-1"


def test_find_story_by_ticker_with_no_match_returns_none(db_isolated):
    store = _seeded_store()

    assert store.find_story_by_ticker("MSFT") is None


def test_persist_ticker_story_updates_match_beside_a_ticker_less_story(db_isolated):
    store = _seeded_store()
    incoming = {
        "label": "NVIDIA",
        "ticker": "NVDA",
        "status": "monitoring",
        "summary": "new summary",
    }

    _persist_story(store, incoming, url_to_date={}, now=NOW)

    assert store.find_by_id("nvda-1")["current_summary"] == "new summary"
    assert len(store.all()) == 2


def test_persist_ticker_less_story_updates_by_label_and_keeps_null_ticker(db_isolated):
    store = _seeded_store()
    incoming = {
        "label": "US inflation & Fed policy",
        "ticker": None,
        "type": "macro",
        "status": "monitoring",
        "summary": "new macro summary",
    }

    _persist_story(store, incoming, url_to_date={}, now=NOW)

    row = store.find_by_id("macro-1")
    assert row["current_summary"] == "new macro summary"
    assert row["ticker"] is None
    assert len(store.all()) == 2
