"""Intel story stamps are aware UTC — on disk and on every read.

Owner-reported (2026-11-01, demo recording): a Topics & macro card read "Historical analysis ·
-46755s ago". ``intel_agent`` and ``risk_signals`` stamped ``created_at``/``updated_at`` with a
NAIVE ``datetime.now()`` — server-local, no zone. A browser parses a zoneless ISO string as ITS
OWN local time, so a server in Asia/Singapore and a browser in America/New_York put the story
~13h in the future. Pinned here:

- a story write stores aware UTC — checked on the RAW row, since the store's read normalisation
  would mask a naive write;
- a legacy naive row reads back aware UTC by ``core.timestamps``' naive → server-local rule,
  through the store and the Intel feed, and the next write stores it healed.

db_isolated isolates the database; fixed_local_tz pins the server zone to UTC+12. No LLM, no
network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select

from backend.core.llm_seam import BatchResult
from backend.db.engine import get_engine
from backend.db.intel_stories_store import IntelStoriesStore
from backend.db.tables import intel_stories

_EVIDENCE_URL = "https://example.test/rates"
_NAIVE_LOCAL = "2026-11-01T15:35:43.410043"
# fixed_local_tz puts the server at UTC+12, so 15:35 local is 03:35 UTC.
_AS_UTC = "2026-11-01T03:35:43.410043+00:00"


def _raw_stamps(story_id: str) -> list[str]:
    """Both stamps as STORED — promoted columns and payload — bypassing the store's reads."""
    with get_engine().connect() as conn:
        row = conn.execute(select(intel_stories).where(intel_stories.c.id == story_id)).one()
    return [row.created_at, row.updated_at, row.payload["created_at"], row.payload["updated_at"]]


def _is_aware_utc(ts: str) -> bool:
    return datetime.fromisoformat(ts).utcoffset() == timedelta(0)


def _legacy_story() -> dict:
    return {
        "id": "story-legacy",
        "type": "macro",
        "label": "Rate path",
        "status": "monitoring",
        "current_summary": "Policy rate on hold.",
        "created_at": _NAIVE_LOCAL,
        "updated_at": _NAIVE_LOCAL,
    }


def test_a_scanned_story_is_stored_with_aware_utc_stamps(db_isolated):
    from backend.monitoring.intel_agent import apply_intel_batch_results

    payload = {
        "stories": [
            {
                "type": "macro",
                "label": "Rate path",
                "status": "monitoring",
                "summary": "Policy rate on hold.",
                "new_timeline_event": {
                    "date": "2026-11-01",
                    "title": "Rate path",
                    "summary": "Held.",
                    "sources": [{"url": _EVIDENCE_URL, "title": "Rate path"}],
                },
            }
        ]
    }
    results = [BatchResult(custom_id="intel-utc", text=json.dumps(payload))]
    ledger_row = {
        "custom_ids": ["intel-utc"],
        "metadata": {"url_to_date": {_EVIDENCE_URL: "2026-11-01"}},
    }

    assert apply_intel_batch_results(results, "handle-utc", ledger_row) == 1

    (story,) = IntelStoriesStore().all()
    stamps = _raw_stamps(story["id"])
    assert all(_is_aware_utc(ts) for ts in stamps), stamps


def test_a_risk_story_is_stored_with_aware_utc_stamps(db_isolated, monkeypatch):
    from backend.services.risk_signals import emit_risk_signals

    rows = [
        {
            "instrument_id": f"inst-{i}",
            "label": f"Acme {i}",
            "ticker": f"ACM{i}",
            "quantity": qty,
            "price": 100.0,
            "quote_per_unit": 1.0,
            "quote_currency": "USD",
            "currency": "USD",
            "asset_class": "equity",
            "sector": "",
            "bucket": "liquid",
            "cost_basis": None,
            "prev_price": None,
            "day_change": None,
            "day_change_pct": None,
            "tags": [],
        }
        for i, qty in enumerate((80.0, 20.0))
    ]
    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", lambda: rows)
    monkeypatch.setattr(
        "backend.services.risk_signals.load_fx_cache", lambda: {"SGD": 1.0, "USD": 1.0}
    )
    monkeypatch.setattr(
        "backend.services.risk_signals.read_profile",
        lambda: {"base_currency": "SGD", "risk_tolerance": "moderate"},
    )

    assert emit_risk_signals() >= 1

    risk = [s for s in IntelStoriesStore().all() if s.get("type") == "risk"]
    assert risk
    for story in risk:
        stamps = _raw_stamps(story["id"])
        assert all(_is_aware_utc(ts) for ts in stamps), stamps


def test_a_legacy_naive_story_reads_back_as_aware_utc(db_isolated, fixed_local_tz):
    from backend.services.intel_stories import list_signals

    store = IntelStoriesStore()
    store.insert(_legacy_story())

    found = store.find_by_id("story-legacy")
    assert (found["created_at"], found["updated_at"]) == (_AS_UTC, _AS_UTC)
    assert store.all()[0]["updated_at"] == _AS_UTC
    (wire,) = list_signals()
    assert wire["updated_at"] == _AS_UTC


def test_writing_a_legacy_story_stores_its_stamps_healed(db_isolated, fixed_local_tz):
    store = IntelStoriesStore()
    store.insert(_legacy_story())

    store.update_by_id("story-legacy", {"read": True})

    assert _raw_stamps("story-legacy") == [_AS_UTC] * 4
