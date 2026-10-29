"""Regression test: the Intel scan filed one company's news on another company's story.

`_persist_story` matched an existing story by the model-supplied id, then ticker, then label,
and on a match overwrote the story's `label`/`ticker` with the model's values — never checking
them against the instrument the story belongs to. A model that echoed the wrong story id turned
a nuclear-startup story into "Search Giant (SRCH)" and appended Search Giant's news to its
timeline; the corrupted label was then fed back to the model on the next scan.

Fix: a match is accepted only when the incoming ticker agrees with the story's own subject
(its instrument's symbol, else its stored ticker); a disagreeing match is discarded and the
lookup moves on. An accepted match keeps the story's ticker, and keeps its label when the model
gave no ticker to vouch for a new one.

Synthetic tickers only. IntelStoriesStore / InstrumentStore are DB-backed; db_isolated redirects
them to a throwaway database. No LLM, no network.
"""

from __future__ import annotations

from backend.db.instrument_store import InstrumentStore
from backend.db.intel_stories_store import IntelStoriesStore
from backend.monitoring.intel_agent import _persist_story

NOW = "2026-10-27T09:00:00"


def _story(story_id: str, ticker: str, label: str, instrument_id: str | None = None) -> dict:
    return {
        "id": story_id,
        "type": "asset",
        "label": label,
        "ticker": ticker,
        "instrument_id": instrument_id,
        "status": "monitoring",
        "current_summary": "old summary",
        "portfolio_impact": "",
        "action_items": [],
        "affected_holdings": [],
        "archived": False,
        "created_at": "2026-10-01T00:00:00",
        "updated_at": "2026-10-01T00:00:00",
    }


def _incoming(ticker: str | None, label: str, story_id: str | None = None) -> dict:
    s = {
        "label": label,
        "status": "monitoring",
        "summary": "new summary",
        "new_timeline_event": {"date": "2026-10-27", "title": f"{label} news", "summary": "x"},
    }
    if ticker is not None:
        s["ticker"] = ticker
    if story_id:
        s["id"] = story_id
    return s


def _nuclear_story(store: IntelStoriesStore) -> dict:
    inst = InstrumentStore().create(
        {"symbol": "NUCL", "asset_class": "equity", "quote_currency": "USD"}
    )
    story = _story("news-nucl", "NUCL", "Nuclear Co — reactor permit filed", inst["id"])
    store.insert(story)
    return story


def test_wrong_story_id_does_not_rename_or_extend_the_story(db_isolated):
    store = IntelStoriesStore()
    _nuclear_story(store)

    _persist_story(
        store, _incoming("SRCH", "Search Giant — signs reactor deal", "news-nucl"), {}, NOW
    )

    kept = store.find_by_id("news-nucl")
    assert kept["ticker"] == "NUCL"
    assert kept["label"] == "Nuclear Co — reactor permit filed"
    assert kept["current_summary"] == "old summary"
    assert store.entries_for_signal("news-nucl") == []
    filed = [s for s in store.all() if s.get("ticker") == "SRCH"]
    assert len(filed) == 1 and filed[0]["label"] == "Search Giant — signs reactor deal"
    assert len(store.entries_for_signal(filed[0]["id"])) == 1


def test_instrument_wins_over_a_corrupted_stored_ticker(db_isolated):
    """A story whose stored ticker was already corrupted to SRCH still belongs to its NUCL
    instrument — a SRCH ticker lookup must not land on it."""
    store = IntelStoriesStore()
    story = _nuclear_story(store)
    store.upsert({**story, "ticker": "SRCH", "label": "Search Giant — old wrong label"})

    _persist_story(store, _incoming("SRCH", "Search Giant — cloud deal"), {}, NOW)

    assert store.entries_for_signal("news-nucl") == []
    assert store.find_by_id("news-nucl")["label"] == "Search Giant — old wrong label"


def test_matching_ticker_still_updates_the_story(db_isolated):
    store = IntelStoriesStore()
    _nuclear_story(store)

    _persist_story(
        store, _incoming("NUCL", "Nuclear Co — first reactor approved", "news-nucl"), {}, NOW
    )

    updated = store.find_by_id("news-nucl")
    assert updated["label"] == "Nuclear Co — first reactor approved"
    assert updated["current_summary"] == "new summary"
    assert len(store.entries_for_signal("news-nucl")) == 1


def test_no_ticker_from_the_model_keeps_the_story_label(db_isolated):
    store = IntelStoriesStore()
    _nuclear_story(store)

    _persist_story(store, _incoming(None, "Something else entirely", "news-nucl"), {}, NOW)

    kept = store.find_by_id("news-nucl")
    assert kept["label"] == "Nuclear Co — reactor permit filed"
    assert kept["ticker"] == "NUCL"


def test_listing_and_pair_suffixes_are_the_same_subject(db_isolated):
    store = IntelStoriesStore()
    store.insert(_story("coin", "COIN", "Coin — network upgrade"))
    store.insert(_story("rr", "RR.L", "Engines plc — order book"))

    _persist_story(store, _incoming("COIN-USD", "Coin — upgrade ships", "coin"), {}, NOW)
    _persist_story(store, _incoming("RR", "Engines plc — new order", "rr"), {}, NOW)

    assert store.find_by_id("coin")["ticker"] == "COIN"
    assert len(store.entries_for_signal("coin")) == 1
    assert store.find_by_id("rr")["ticker"] == "RR.L"
    assert len(store.entries_for_signal("rr")) == 1
