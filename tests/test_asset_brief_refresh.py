"""
Unit tests for backend.monitoring.asset_brief — live refresh logic.

Covers:
- stub provider + LLM: brief mapping (news + key_issues + facts + peers)
- model resolution: uses "intel" task override when set, default when unset
- portfolio_impact: present in brief when held, null when not held
- news_summaries: headline/summary merged onto news items by url
- cost_tracker.record called for paid search provider (source="asset_brief")
- debounce within 15 min → {refreshed: false, reason: "fresh"}
- lock coalesce: double-tap inside the lock gets debounced on re-check
- search-unconfigured → {reason: "search_unconfigured"}, not an exception
- no_ticker → {reason: "no_ticker"}
- synthesis: raw-JSON reply (not a tool-call) maps to key_issues (Fix 1 regression)
- synthesis: fenced JSON block and {"input":{}} wrapper both parse correctly
- multi-provider: two providers with overlapping urls → merged+deduped; snippet in
  synthesis items, absent in persisted news (Fix 4 regression)
- source hostname fallback: no source field → hostname derived from url (Fix 2 regression)
- provider error in one of two providers → the other still yields results (Fix 3 regression)
- Enhancement A: full-text fetch replaces snippet; failures keep original snippet
- Enhancement A: 30-page ceiling; one slow/raising fetch doesn't fail the whole refresh
- Enhancement B: perplexity search_and_answer captures analyst_context in synthesis prompt
- Enhancement B: persisted news still has no snippet
- run_intel_scan characterization stays green (intel_agent imports still resolve)
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.monitoring.asset_brief as _mod
from backend.db.instrument_store import InstrumentStore
from backend.monitoring.asset_brief import _BriefOutput

# ── helpers ───────────────────────────────────────────────────────────────────


def _to_model(data):
    """Mimic ``complete_model``: turn a canned reply (a JSON string or a dict) into a
    validated ``_BriefOutput`` instance, or ``None`` when it can't be parsed/validated
    (the structured layer's failure contract — the flow's empty-synthesis path)."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return None
    try:
        return _BriefOutput.model_validate(data)
    except Exception:
        return None


# _fetch_news_items uses local imports inside the function body, so we must patch
# at the source module — not at backend.monitoring.asset_brief.SearchConfigStore.
_SC_PATH = "backend.db.search_config_store.SearchConfigStore"
_BP_PATH = "backend.ai.search.base.build_provider"
_GSP_PATH = "backend.monitoring.asset_brief.get_search_provider"


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store_path(tmp_path, monkeypatch, db_isolated):
    """AssetBriefStore is DB-backed now (plan 4b task 3) — db_isolated (above) redirects it
    to a per-test sqlite file; `p` is kept only as a return value (unused for reads/writes
    now) so callers that thread it through `_make_store` keep a stable signature."""
    # Also patch the path the monitoring module sees (it imports the store at call time)
    p = tmp_path / "asset_briefs.json"
    monkeypatch.setattr("backend.monitoring.asset_brief.AssetBriefStore", lambda: _make_store(p))
    # _do_refresh now accumulates into the evidence cache — db_isolated (above) covers it.
    return p


def _make_store(path: Path):
    from backend.db.asset_brief_store import AssetBriefStore

    return AssetBriefStore()


@pytest.fixture
def inst_path(db_isolated):
    """InstrumentStore is DB-backed (plan 2, task 5) — db_isolated gives it a fresh
    per-test database. Kept named ``inst_path`` (not renamed): ~20 test functions below
    still declare it as a fixture parameter purely to pull in the isolation, and each
    seeds via ``InstrumentStore().bulk_insert(...)`` directly rather than through this
    fixture's return value."""
    return None


def _days_ago(n: int) -> str:
    """Fixture publish dates must be RELATIVE to the wall clock: the recency gate
    (_is_stale_news, commit 9c8272b9) drops dated news older than ~6 months, so an
    absolute date ages past the cutoff and silently empties every provider stub
    (this file broke on 2026-07-03 when its hardcoded 2026-01-01 dates went stale)."""
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


# 40 genuinely content-DISTINCT headline fragments (no two share enough vocabulary to trip
# `_filter_and_collapse_news`'s cross-publisher clustering, verified directly against
# `cluster_by_content_overlap`) — for fixtures that need N *distinct* fake articles. A
# templated "Cached story {i}" (differing only by the trailing digit) is NOT distinct enough
# once the shared "cached"/"story" tokens dominate a short title's token count.
_DISTINCT_STORY_TITLES = [
    "quarterly profit exceeds forecast",
    "factory recall widens scope",
    "chief executive steps down",
    "shipping delays disrupt deliveries",
    "antitrust regulators open inquiry",
    "board raises cash dividend",
    "buyback program gets expanded",
    "new plant breaks ground",
    "union workers reject contract",
    "rival files patent claim",
    "Moody's lifts credit outlook",
    "Fitch cuts debt rating",
    "merger rumors resurface again",
    "unit spinoff gains approval",
    "hackers breach customer database",
    "ransomware halts production line",
    "finance chief quits abruptly",
    "headcount reduction plan unveiled",
    "thousand jobs cut nationwide",
    "recruiting pause takes effect",
    "flagship device debuts globally",
    "distribution deal inked overseas",
    "venture combines two rivals",
    "watchdog scrutinizes pricing practices",
    "board weighs stock split",
    "trader charged with fraud",
    "regulator levies pollution penalty",
    "brakes prompt vehicle recall",
    "trial data shows promise",
    "regulators clear new therapy",
    "dozens of outlets shutter",
    "footprint grows across Asia",
    "weak yen pressures margins",
    "outlook trimmed for quarter",
    "brokerage lifts price target",
    "hedge fund exits position",
    "investors settle class action",
    "activist wins board seat",
    "fund discloses large stake",
    "notes priced above par",
]


def _make_inst(instrument_id: str = "inst-1", with_ticker: bool = True) -> dict:
    return {
        "id": instrument_id,
        "symbol": "TEST",
        "name": "Test Co",
        "asset_class": "equity",
        "quote_currency": "USD",
        "price_source": "yahoo" if with_ticker else "manual",
        "price_source_id": "TEST.US" if with_ticker else "",
        "identifiers": {},
    }


def _stub_provider(results: list | None = None, fetch_page_text: str = ""):
    """A fake SearchProvider that returns results without any network call.

    ``fetch_page_text`` is returned verbatim from ``fetch_page`` (empty = simulates a
    no-content response, which keeps the original snippet in place).
    """

    if results is None:
        # Deliberately no shared content words with each other OR with `_make_inst()`'s "Test
        # Co" name — "Test headline"/"Another headline" used to share only "headline" once
        # "test" was excluded as the subject's own name token, which is exactly enough overlap
        # to false-positive-cluster under `_filter_and_collapse_news`'s cross-publisher pass.
        results = [
            {
                "title": "Company posts record quarterly revenue",
                "url": "https://example.com/1",
                "published_date": _days_ago(6),
                "source": "TestSrc",
                "content": "Some content about earnings",
            },
            {
                "title": "Analysts raise price target on strong guidance",
                "url": "https://example.com/2",
                "published_date": _days_ago(5),
                "source": "OtherSrc",
                "content": "More content about guidance",
            },
        ]

    _results = results
    _fetch_text = fetch_page_text

    class _FakeProvider:
        async def search(self, query: str, max_results: int = 5) -> list:
            return _results

        async def fetch_page(self, url: str) -> str:
            return _fetch_text

    return _FakeProvider()


def _synthesis_json() -> str:
    """Fake LLM output matching the expected raw JSON schema (no tool-call wrapper)."""
    return json.dumps(
        {
            "description": "Test Co is a fictional company.",
            "key_issues": [
                {
                    "question": "Will earnings beat estimates?",
                    "bullish": {"text": "Strong pipeline.", "sources": ["https://example.com/1"]},
                    "bearish": {"text": "Margin pressure.", "sources": ["https://example.com/2"]},
                }
            ],
            "facts": {
                "ceo": "Jane Doe",
                "employees": 5000,
                "ipo_date": "2010-01-01",
                "industry": "Software",
                "sector": "Tech",
                "exchange": "NASDAQ",
                "country": "US",
            },
            "peers": [
                {"name": "Peer Corp", "ticker": "PEER", "exchange": "NYSE"},
            ],
        }
    )


def _make_sc_mock(provider_ids: list[str], provider_cfgs: dict | None = None):
    """Build a mock SearchConfigStore that returns the given provider_ids from enabled_providers()."""
    mock_sc = MagicMock()
    mock_sc.enabled_providers.return_value = provider_ids
    if provider_cfgs is None:
        provider_cfgs = {pid: {"api_key": "test-key"} for pid in provider_ids}
    mock_sc.provider_runtime_config.side_effect = lambda pid: provider_cfgs.get(pid, {})
    # default_provider() is called by _provider_name() inside _do_refresh; must return a str.
    mock_sc.default_provider.return_value = provider_ids[0] if provider_ids else "unknown"
    # SearchConfigStore() is called as a constructor — the patch replaces the class,
    # so mock_sc IS the instance (patching the class to return mock_sc directly).
    return mock_sc


# ── tests ─────────────────────────────────────────────────────────────────────


def test_no_ticker_returns_no_ticker_reason(inst_path, store_path):
    InstrumentStore().bulk_insert([_make_inst(with_ticker=False)])
    result = asyncio.run(_mod.refresh_asset_brief("inst-1"))
    assert result["refreshed"] is False
    assert result["reason"] == "no_ticker"


def test_unknown_instrument_returns_no_ticker(inst_path, store_path):
    InstrumentStore().bulk_insert([])
    result = asyncio.run(_mod.refresh_asset_brief("does-not-exist"))
    assert result["refreshed"] is False
    assert result["reason"] == "no_ticker"


def test_search_unconfigured_returns_reason(inst_path, store_path):
    InstrumentStore().bulk_insert([_make_inst()])
    with patch(
        "backend.monitoring.asset_brief.get_search_provider", side_effect=ValueError("no provider")
    ):
        result = asyncio.run(_mod.refresh_asset_brief("inst-1"))
    assert result["refreshed"] is False
    assert result["reason"] == "search_unconfigured"


def test_fresh_brief_debounces(inst_path, store_path, tmp_path, monkeypatch):
    """A brief synthesized < 15 min ago should debounce without calling LLM."""
    InstrumentStore().bulk_insert([_make_inst()])
    # Pre-populate a recent brief
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path
    store = AssetBriefStore()
    store.upsert_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [],
            "facts": {},
            "peers": [],
            "description": "Old.",
            "synthesized_at": recent_ts,
            "search_provider": "tavily",
        }
    )

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=_stub_provider()):
        with patch(
            "backend.monitoring.asset_brief.complete_model", new_callable=AsyncMock
        ) as mock_complete:
            result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=False))

    assert result["refreshed"] is False
    assert result["reason"] == "fresh"
    mock_complete.assert_not_called()


def test_force_bypasses_debounce(inst_path, store_path, monkeypatch):
    """force=True refreshes even when brief is 'fresh' (but past the hard floor)."""
    InstrumentStore().bulk_insert([_make_inst()])
    # 5 minutes old: past the 60-second hard floor but within the 15-minute freshness gate
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path
    from backend.db.asset_brief_store import AssetBriefStore

    store = AssetBriefStore()
    store.upsert_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [],
            "facts": {},
            "peers": [],
            "description": None,
            "synthesized_at": recent_ts,
            "search_provider": "x",
        }
    )

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            return_value=_to_model(_synthesis_json()),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True


def test_key_issues_always_carried_forward_from_prior_never_regenerated(
    inst_path, store_path, monkeypatch
):
    """Problem B / ADR 2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation:
    `_do_refresh` no longer runs any key_issues/portfolio_impact synthesis at all — that field
    is now written exclusively by the Analyst pipeline (`asset_brief.apply_analyst_synthesis`).
    A refresh with NO prior brief leaves key_issues `[]` (nothing to carry forward); a refresh
    with a prior brief carries its key_issues/portfolio_impact through BYTE-IDENTICAL, even
    though `complete_model` (patched here to answer EVERY call, including the still-live
    facts_peers call) would happily supply key_issues content if anything still asked for it."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )
    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()
    complete_mock = AsyncMock(return_value=_to_model(_synthesis_json()))

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch("backend.monitoring.asset_brief.complete_model", complete_mock):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    assert result["key_issues_count"] == 0
    assert AssetBriefStore().get("inst-1")["key_issues"] == []

    # Now with a PRIOR brief carrying real key_issues — must survive byte-identical.
    prior_issue = {
        "question": "Prior?",
        "bullish": {"text": "b", "sources": []},
        "bearish": {"text": "x", "sources": []},
    }
    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [prior_issue],
            "portfolio_impact": "Prior impact text.",
            "facts": {"ceo": "Jane"},
            "peers": [],
            "description": "prior desc",
            "synthesized_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "search_provider": "x",
        }
    )
    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch("backend.monitoring.asset_brief.complete_model", complete_mock):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    brief = AssetBriefStore().get("inst-1")
    assert brief["key_issues"] == [prior_issue]
    assert brief["portfolio_impact"] == "Prior impact text."


def test_refresh_does_not_revert_a_fresher_key_issues_written_mid_flight(
    inst_path, store_path, monkeypatch
):
    """R1 fix (security-review 2026-08-29): `_do_refresh` captures `prior` BEFORE the slow
    evidence/search work even starts, and `_land_analysis` used to build `carried_forward` from
    that STALE snapshot at write time — so if the Analyst pipeline's own background precompute
    writes a FRESHER key_issues/portfolio_narrative for the SAME instrument while a manual
    Refresh is mid-flight, the Refresh's own write reverted it back to the pre-refresh snapshot.
    Simulated here: the (slow) facts_peers call's mocked completion writes a fresh key_issues
    update as its OWN side effect — mimicking the Analyst pipeline landing concurrently, during
    the exact await this refresh is blocked on — and the refresh's own subsequent write must
    preserve it, not clobber it back to the stale pre-refresh value."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    stale_issue = {
        "question": "Stale?",
        "bullish": {"text": "old bull", "sources": []},
        "bearish": {"text": "old bear", "sources": []},
    }
    # No facts/peers/description/facts_peers_synthesized_at — _facts_peers_sufficient is False,
    # so _land_analysis's facts_peers call actually runs (the await this test needs).
    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [stale_issue],
            "portfolio_narrative": "Stale narrative.",
        }
    )

    fresh_issue = {
        "question": "Fresh?",
        "bullish": {"text": "new bull", "sources": []},
        "bearish": {"text": "new bear", "sources": []},
    }

    async def _fake_complete_model(
        prompt, output_model, *, system="", model="", max_tokens=2048, source="asset_brief"
    ):
        # The Analyst pipeline's own background precompute, landing a FRESHER
        # key_issues/portfolio_narrative for this SAME instrument WHILE this refresh's own
        # facts_peers call is mid-flight — fires on every complete_model call, so it's
        # guaranteed to happen at least once via the facts_peers path itself, strictly before
        # _land_analysis's own final read.
        current = AssetBriefStore().get("inst-1") or {}
        AssetBriefStore().upsert_brief(
            {
                **current,
                "key_issues": [fresh_issue],
                "portfolio_narrative": "Fresh narrative.",
                "key_issues_synthesized_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return _to_model(_synthesis_json())

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch("backend.monitoring.asset_brief.complete_model", _fake_complete_model):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    brief = AssetBriefStore().get("inst-1")
    assert brief["key_issues"] == [fresh_issue]
    assert brief["portfolio_narrative"] == "Fresh narrative."


def test_refresh_keeps_a_take_rewrite_stamp_and_call_landed_during_its_search(
    inst_path, store_path, monkeypatch
):
    """A Refresh starts the take rewrite beside this news/facts refresh (and the page's poll
    can start a facts refresh beside a rewrite). The rewrite stamps `key_issues_refresh_stage`
    and lands `position_verdict` while this refresh is awaiting its search — its mid-refresh
    "analysing" save used to write its pre-search copy back, wiping the stamp, so the page
    stopped showing the rewrite in progress and missed the new call until its next 60s poll."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )
    AssetBriefStore().upsert_brief({"instrument_id": "inst-1", "portfolio_narrative": "Old."})
    verdict = {"action": "hold", "why": "x", "held": True}
    base = _stub_provider()

    class _RewriteLandsDuringSearch:
        async def search(self, query: str, max_results: int = 5) -> list:
            current = AssetBriefStore().get("inst-1") or {}
            AssetBriefStore().upsert_brief(
                {**current, "key_issues_refresh_stage": "analysing", "position_verdict": verdict}
            )
            return await base.search(query, max_results)

        async def fetch_page(self, url: str) -> str:
            return await base.fetch_page(url)

    stub = _RewriteLandsDuringSearch()
    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            AsyncMock(return_value=_to_model(_synthesis_json())),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=_make_sc_mock(["perplexity"])):
                    with patch(_BP_PATH, return_value=stub):
                        asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    brief = AssetBriefStore().get("inst-1")
    assert brief["key_issues_refresh_stage"] == "analysing"
    assert brief["position_verdict"] == verdict


def test_successful_refresh_populates_brief(inst_path, store_path, monkeypatch):
    """A successful refresh stores news + facts/peers — key_issues stays empty (no prior, and
    no longer this function's to synthesize; see
    test_key_issues_always_carried_forward_from_prior_never_regenerated)."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()

    # Peer enrichment resolves each peer name -> ticker via Yahoo search, then prices it.
    # Stub both so the (otherwise network) resolution is deterministic offline.
    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda q: {"symbol": "PEER", "name": "Peer Co", "exchange": "NASDAQ"},
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {
            "prices": {"PEER": 100.0},
            "prev_prices": {"PEER": 99.0},
            "exchanges": {"PEER": "NASDAQ"},
        },
    )

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            return_value=_to_model(_synthesis_json()),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1"))

    assert result["refreshed"] is True
    assert result["news_count"] >= 1
    assert result["key_issues_count"] == 0
    assert "last_refreshed_at" in result

    from backend.db.asset_brief_store import AssetBriefStore

    brief = AssetBriefStore().get("inst-1")
    assert brief is not None
    assert brief["key_issues"] == []  # no longer this function's to synthesize
    assert brief["facts"]["ceo"] == "Jane Doe"
    assert len(brief["peers"]) == 1
    assert brief["peers"][0]["ticker"] == "PEER"
    assert brief["peers"][0]["price"] == 100.0


def test_enrich_peers_dedups_same_ticker_different_names(monkeypatch):
    """Two LLM peer names that resolve to the SAME ticker collapse to one row.

    The LLM names the same company differently across refreshes ("Amazon.com" vs
    "Amazon.com Inc.", both AMZN), so the name-keyed _merge_peers can't catch them —
    only the resolved ticker is a stable identity. Regression for duplicate peer rows.
    """
    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda q: {"symbol": "AMZN", "name": "Amazon.com Inc.", "exchange": "NASDAQ"},
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {
            "prices": {"AMZN": 244.39},
            "prev_prices": {"AMZN": 237.5},
            "exchanges": {"AMZN": "NASDAQ"},
        },
    )

    peers = [{"name": "Amazon.com Inc."}, {"name": "Amazon.com"}]
    out = _mod._enrich_peers_sync(peers)

    assert [p["ticker"] for p in out] == ["AMZN"]


def test_enrich_peers_attaches_quote_currency(monkeypatch):
    """A resolved peer carries its listing currency from fetch_live's `currencies` map, so the
    Peers card can label a cross-listed quote (e.g. Prosus on Amsterdam is EUR, not the base ccy)."""
    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda q: {"symbol": "PRX.AS", "name": "Prosus", "exchange": "Amsterdam"},
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {
            "prices": {"PRX.AS": 38.24},
            "prev_prices": {"PRX.AS": 38.21},
            "exchanges": {"PRX.AS": "Amsterdam"},
            "currencies": {"PRX.AS": "EUR"},
        },
    )

    out = _mod._enrich_peers_sync([{"name": "Prosus"}])

    assert out[0]["currency"] == "EUR"
    assert out[0]["price"] == 38.24


def test_enrich_peers_omits_currency_when_absent(monkeypatch):
    """An older fetch_live with no `currencies` map (or no entry) leaves the peer without a
    currency key — the card degrades to a bare price rather than mislabeling it."""
    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda q: {"symbol": "DASH", "name": "DoorDash", "exchange": "NASDAQ"},
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {
            "prices": {"DASH": 173.46},
            "prev_prices": {"DASH": 173.46},
            "exchanges": {"DASH": "NASDAQ"},
        },
    )

    out = _mod._enrich_peers_sync([{"name": "DoorDash"}])

    assert "currency" not in out[0]


def test_enrich_peers_crypto_labels_usd(monkeypatch):
    """Crypto peers are quoted in USD (CoinGecko vs_currencies=usd) — tag the currency so the
    number isn't read as the viewer's base currency."""
    monkeypatch.setattr(
        "backend.services.prices.search_coin",
        lambda q: {"id": "ethereum", "symbol": "ETH"},
    )
    monkeypatch.setattr(
        "backend.services.prices._fetch_coingecko_by_id",
        lambda pairs: ({"ethereum": 1746.8}, {"ethereum": 1728.2}, {}),
    )

    out = _mod._enrich_peers_crypto_sync([{"name": "Ethereum"}])

    assert out[0]["currency"] == "USD"
    assert out[0]["price"] == 1746.8


def test_dedupe_resolved_peers_keeps_first_drops_repeat_ticker():
    """_dedupe_resolved_peers keeps the first occurrence of each ticker (case-insensitive)."""
    peers = [
        {"name": "Alphabet Inc.", "ticker": "GOOG"},
        {"name": "NVIDIA", "ticker": "NVDA"},
        {"name": "Alphabet", "ticker": "goog"},
    ]
    out = _mod._dedupe_resolved_peers(peers)

    assert [p["name"] for p in out] == ["Alphabet Inc.", "NVIDIA"]


def test_refresh_keeps_prior_evidence_not_in_todays_search(inst_path, store_path, monkeypatch):
    """News-disappears fix: an article from an earlier refresh that today's live search no longer
    returns still appears in `evidence` — where the live-queried news feed now reads from
    (RFC intel-signals-data-model §5) — accumulated from the evidence cache, not dropped."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.evidence_store import EvidenceStore
    from backend.services import evidence as ev

    _abs.ASSET_BRIEF_JSON = store_path

    # An article surfaced by an earlier scan, still within TTL, tagged for THIS asset.
    ev.record_evidence(
        [
            {
                "url": "https://example.com/yesterday",
                "title": "Yesterday still valid",
                "content": "context",
                "published_date": _days_ago(1),
                "_query": "q",
            }
        ],
        subjects=["asset:inst-1"],
        kind="news",
        store=EvidenceStore(),
    )

    sc_instance = _make_sc_mock(["serper"])
    # Today's live search returns ONLY a different article — yesterday's is gone from live results.
    stub = _stub_provider(
        results=[
            {
                "title": "Today only",
                "url": "https://example.com/today",
                "published_date": _days_ago(0),
                "source": "S",
                "content": "new",
            }
        ]
    )
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            return_value=_to_model(_synthesis_json()),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    from backend.services.evidence import read_evidence

    urls = {r["url"] for r in read_evidence(subjects=["asset:inst-1"], fresh_only=True)}
    assert "https://example.com/today" in urls  # today's fetch
    assert "https://example.com/yesterday" in urls  # survived despite absence from today's search


def test_refresh_retains_more_than_20_cached_articles(inst_path, store_path, monkeypatch):
    """Regression: a hot ticker accumulates many still-fresh cached articles in `evidence` — not
    silently capped at some old hardcoded 20, which would rotate accumulated news out of BOTH
    cards and Latest Developments (the SpaceX report; RFC intel-signals-data-model §5 moved the
    display read to a live `evidence` query, so the guard now lives there)."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.evidence_store import EvidenceStore
    from backend.services import evidence as ev

    _abs.ASSET_BRIEF_JSON = store_path

    # 35 still-fresh cached articles (> the old 20 cap, < the new 50 cap). Dates are relative to
    # NOW (1–15 days old) so the rows stay inside the 90-day publish TTL regardless of wall clock.
    _now = datetime.now(timezone.utc)
    cached = [
        {
            "url": f"https://example.com/cached-{i:02d}",
            # Content-DISTINCT titles (not "Cached story {i}") — a templated title differing
            # only by digit false-positives as a cross-publisher near-duplicate once
            # `_filter_and_collapse_news`'s clustering pass runs on the assembled news list.
            "title": _DISTINCT_STORY_TITLES[i % len(_DISTINCT_STORY_TITLES)],
            "content": "context",
            "published_date": (_now - timedelta(days=1 + (i % 15))).isoformat(),
            "_query": "q",
        }
        for i in range(35)
    ]
    ev.record_evidence(cached, subjects=["asset:inst-1"], kind="news", store=EvidenceStore())

    sc_instance = _make_sc_mock(["serper"])
    stub = _stub_provider(
        results=[
            {
                "title": "Today only",
                "url": "https://example.com/today",
                "published_date": _days_ago(0),
                "source": "S",
                "content": "new",
            }
        ]
    )
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            return_value=_to_model(_synthesis_json()),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    from backend.services.evidence import read_evidence

    rows = read_evidence(subjects=["asset:inst-1"], fresh_only=True, limit=1000)
    # Old behavior capped the cached fold-back at 20 → the old brief would hold ~21. `evidence`
    # itself keeps all 35 cached + today's hit — nothing here truncates to the old 20.
    assert len(rows) > 20
    assert len(rows) >= 35


def test_recency_hours_parses_rfc2822_rss_dates():
    """RSS feeds (Google News / Yahoo) emit RFC-2822 pubDates. They must rank by true recency,
    not score 1e9 (undated → last) — else the RSS bulk never enters the top-12 synthesis window
    fed to the LLM, leaving the analysis to a handful of live-search hits."""
    from email.utils import format_datetime

    now = datetime.now(timezone.utc)
    recent = format_datetime(now - timedelta(hours=5))  # "Wed, 18 Jun 2026 .. +0000"
    old = format_datetime(now - timedelta(days=200))
    assert _mod._recency_hours(recent) < 12  # ~5h, not 1e9
    assert 24 * 150 < _mod._recency_hours(old) < 24 * 250  # ~200 days, parsed
    assert _mod._recency_hours("not a date") >= 1e9  # genuinely unparseable still sinks
    # _to_iso now converts an RFC-2822 date to ISO (so display + fold-back normalization work).
    assert _mod._to_iso(recent, now).startswith("20")


def test_news_sort_ranks_recent_rss_above_undated():
    """A recent RFC-2822 RSS article must sort ahead of an undated quote page."""
    from email.utils import format_datetime

    now = datetime.now(timezone.utc)
    recent_rss = {"url": "https://x/1", "published_at": format_datetime(now - timedelta(hours=2))}
    undated = {"url": "https://x/2", "published_at": ""}
    items = [undated, recent_rss]
    items.sort(key=_mod._news_sort_key)
    assert items[0] is recent_rss


# A regression test proving a Google-News-wrapped item's DISPLAY domain resolves to the real
# publisher (not the "news.google.com" redirect wrapper) now lives in
# tests/test_asset_detail_live_news.py — that's where the domain resolution this used to prove
# actually happens post-RFC-intel-signals-data-model-§5 (`_live_news_for_instrument`, not this
# module's own now-unused-for-display fold-back).


# A real, captured Google News item-link response: fetched WITHOUT a browser, this is
# Google's own interstitial shell (the real publisher redirect only happens via client-side
# JS) — a genuine 200, well over _MIN_ARTICLE_CHARS, no Cloudflare-style marker. Trimmed to
# the parts _looks_blocked actually reads (first 2000 chars) plus enough padding to clear
# the length floor on its own, so this test would have failed before the fix even without
# the length check catching it.
_GOOGLE_NEWS_SHELL_HTML = (
    '<!doctype html><html lang="en-US" dir="ltr"><head>'
    '<base href="https://news.google.com/">'
    '<link rel="preconnect" href="//www.gstatic.com">'
    '<meta name="referrer" content="origin">'
    '<link rel="canonical" href="https://news.google.com/rss/articles/CBMi0wF...">'
    "<title>Google News</title></head><body>"
    + ("<script>/* app bootstrap chunk */</script>" * 30)
    + "</body></html>"
)


def test_looks_blocked_catches_the_google_news_interstitial_shell():
    """A Google News item link fetched without a browser never reaches the real article — it
    resolves to Google's OWN wrapper shell, which _BLOCK_MARKERS didn't cover (it was tuned
    for Cloudflare-style challenge pages). Verified empirically 2026-08-13: this shell is a
    genuine 200, ~580KB, passes the length floor, and carries none of the Cloudflare markers
    — so _looks_blocked returned False and asset_brief overwrote the item's real RSS snippet
    with raw HTML markup on every enrichment pass. Regression for that."""
    assert len(_GOOGLE_NEWS_SHELL_HTML) >= _mod._MIN_ARTICLE_CHARS
    assert _mod._looks_blocked(_GOOGLE_NEWS_SHELL_HTML) is True


def test_looks_blocked_does_not_flag_a_real_article_mentioning_google_news():
    """The new marker must not be so broad it flags a legitimate article that merely
    mentions Google News in passing — only Google's OWN <base href> shell tag matches."""
    real_article = (
        "<html><head><title>A Real Article</title></head><body>"
        "<article><h1>Publishers React To Google News Changes</h1>"
        "<p>" + ("This is a real, substantive paragraph about the topic. " * 20) + "</p>"
        "</article></body></html>"
    )
    assert len(real_article) >= _mod._MIN_ARTICLE_CHARS
    assert _mod._looks_blocked(real_article) is False


def test_search_failure_returns_refresh_failed(inst_path, store_path, monkeypatch):
    """An exception from search is swallowed; result has reason=refresh_failed or no news."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path

    class _BrokenProvider:
        async def search(self, *a, **kw):
            raise RuntimeError("network down")

        async def fetch_page(self, url: str) -> str:
            return ""

    sc_instance = _make_sc_mock(["perplexity"])

    # With broken search: no news → LLM still called with empty news → may still succeed
    with patch(
        "backend.monitoring.asset_brief.get_search_provider", return_value=_BrokenProvider()
    ):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            return_value=_to_model(_synthesis_json()),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=_BrokenProvider()):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1"))

    # Result should be either refreshed=True (empty news but LLM still ran) or reason=refresh_failed
    assert "refreshed" in result


def test_llm_failure_degrades_gracefully_not_a_hard_refresh_failure(
    inst_path, store_path, monkeypatch
):
    """An LLM failure (LLMUnavailable) on every complete_model call no longer fails the WHOLE
    refresh (Problem B / ADR 2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation):
    the one call that used to propagate LLMUnavailable unguarded was the now-removed key_issues
    synthesis. The remaining call (facts_peers) was already wrapped to degrade gracefully on ANY
    exception — a total LLM outage now just means this refresh's evidence still updates (that
    part needs no LLM at all) while facts/peers/description/key_issues carry forward from prior,
    exactly like the existing non-regression path for an empty synthesis."""
    from backend.core.llm_seam import LLMUnavailable

    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            side_effect=LLMUnavailable("no provider"),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1"))

    assert result["refreshed"] is True
    from backend.db.asset_brief_store import AssetBriefStore
    from backend.services.evidence import read_evidence

    brief = AssetBriefStore().get("inst-1")
    assert brief["key_issues"] == []
    assert brief["facts"] == {}
    rows = read_evidence(subjects=["asset:inst-1"], fresh_only=True)
    assert len(rows) >= 1  # the search/fetch itself needed no LLM call at all


def test_refresh_stage_moves_through_real_milestones(inst_path, store_path, monkeypatch):
    """refresh_stage (owner ask 2026-08-11: the UI's 'Searching the web…' caption must reflect
    what's ACTUALLY happening, not a fixed timer loop) must visit "searching" -> "analysing" ->
    "ready" IN ORDER as _do_refresh/_land_analysis actually write it.

    Spies on every store.upsert_brief call (rather than polling on a timer) so the recorded
    sequence is exactly the chronological write order."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    seen_stages: list[str | None] = []
    real_upsert = AssetBriefStore.upsert_brief

    def _spy_upsert(self, brief):
        stage = brief.get("refresh_stage")
        if not seen_stages or seen_stages[-1] != stage:
            seen_stages.append(stage)
        return real_upsert(self, brief)

    with patch.object(AssetBriefStore, "upsert_brief", _spy_upsert):
        with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
            with patch(
                "backend.monitoring.asset_brief.complete_model",
                new_callable=AsyncMock,
                return_value=_to_model(_synthesis_json()),
            ):
                with patch(
                    "backend.services.profile.read_profile",
                    return_value={"base_currency": "SGD"},
                ):
                    with patch(_SC_PATH, return_value=sc_instance):
                        with patch(_BP_PATH, return_value=stub):
                            result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    # "ready" (owner ask 2026-08-11: "I see lucius's take is updated but then the bar is
    # still showing") is the real terminal milestone — key_issues/portfolio_impact landed.
    assert seen_stages == ["searching", "analysing", "ready"]


# ── facts/peers skip when a company profile already exists ──────────────────────────────


def _recent_iso(days: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_facts_peers_sufficient_true_with_full_recent_profile():
    prior = {
        "facts": {"ceo": "Jane Doe", "sector": "Tech"},
        "peers": [{"name": "Peer Corp", "ticker": "PEER"}],
        "description": "A software company.",
        "facts_peers_synthesized_at": _recent_iso(),
    }
    assert _mod._facts_peers_sufficient(prior) is True


def test_facts_peers_sufficient_false_when_no_prior():
    assert _mod._facts_peers_sufficient({}) is False


def test_facts_peers_sufficient_false_when_peers_missing():
    prior = {
        "facts": {"ceo": "Jane Doe"},
        "peers": [],
        "description": "A software company.",
        "facts_peers_synthesized_at": _recent_iso(),
    }
    assert _mod._facts_peers_sufficient(prior) is False


def test_facts_peers_sufficient_false_when_facts_empty():
    prior = {
        "facts": {"ceo": None, "sector": None, "industry": None, "employees": None},
        "peers": [{"name": "Peer Corp"}],
        "description": "A software company.",
        "facts_peers_synthesized_at": _recent_iso(),
    }
    assert _mod._facts_peers_sufficient(prior) is False


def test_facts_peers_sufficient_false_when_no_timestamp_recorded():
    """Data written before facts_peers_synthesized_at existed (or any other reason it's
    missing) is treated as insufficient — best-effort self-healing backfill on next refresh,
    never trusted as permanently fresh."""
    prior = {
        "facts": {"ceo": "Jane Doe"},
        "peers": [{"name": "Peer Corp"}],
        "description": "A software company.",
    }
    assert _mod._facts_peers_sufficient(prior) is False


def test_facts_peers_sufficient_false_when_stale():
    """Owner ask 2026-08-11 (2nd round): 'these should be refreshed at least once per week
    … otherwise it might be pretty bad' — a profile older than _FACTS_PEERS_MAX_AGE_DAYS is
    treated as insufficient again, so it gets regenerated instead of staying frozen forever."""
    prior = {
        "facts": {"ceo": "Jane Doe"},
        "peers": [{"name": "Peer Corp"}],
        "description": "A software company.",
        "facts_peers_synthesized_at": _recent_iso(days=_mod._FACTS_PEERS_MAX_AGE_DAYS + 1),
    }
    assert _mod._facts_peers_sufficient(prior) is False


def test_facts_peers_sufficient_true_just_inside_the_window():
    prior = {
        "facts": {"ceo": "Jane Doe"},
        "peers": [{"name": "Peer Corp"}],
        "description": "A software company.",
        "facts_peers_synthesized_at": _recent_iso(days=_mod._FACTS_PEERS_MAX_AGE_DAYS - 1),
    }
    assert _mod._facts_peers_sufficient(prior) is True


# ── facts_peers_stale (owner ask 2026-08-31): public wrapper analyst.py's held/watched
# background precompute calls, so it never reaches into this module's private staleness gate
# directly — mirrors _facts_peers_sufficient exactly, just store-backed and inverted. ──────────


def test_facts_peers_stale_true_when_no_brief_exists(inst_path, store_path):
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path
    assert _mod.facts_peers_stale("inst-never-refreshed") is True


def test_facts_peers_stale_false_when_recent_profile_exists(inst_path, store_path):
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path
    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-fresh",
            "facts": {"ceo": "Jane Doe", "sector": "Tech"},
            "peers": [{"name": "Peer Corp", "ticker": "PEER"}],
            "description": "A software company.",
            "facts_peers_synthesized_at": _recent_iso(),
        }
    )
    assert _mod.facts_peers_stale("inst-fresh") is False


def test_facts_peers_stale_true_when_profile_older_than_max_age(inst_path, store_path):
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path
    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-old",
            "facts": {"ceo": "Jane Doe", "sector": "Tech"},
            "peers": [{"name": "Peer Corp", "ticker": "PEER"}],
            "description": "A software company.",
            "facts_peers_synthesized_at": _recent_iso(days=_mod._FACTS_PEERS_MAX_AGE_DAYS + 1),
        }
    )
    assert _mod.facts_peers_stale("inst-old") is True


def _facts_peers_tracking_side_effect(key_issues_reply):
    """complete_model side_effect that records which response_model each call used, and
    returns a fixed canned reply per call type — used to prove whether the facts_peers call
    was actually made (or skipped) without depending on asyncio.gather interleaving order."""
    calls_by_model: list[str] = []

    async def _side_effect(prompt, response_model=None, **kw):
        calls_by_model.append(getattr(response_model, "__name__", str(response_model)))
        if response_model is _mod._FactsPeersOutput:
            return _mod._FactsPeersOutput.model_validate(
                {
                    "description": "A NEW description from this refresh.",
                    "facts": {"ceo": "New CEO"},
                    "peers": [{"name": "New Peer", "ticker": "NEWP"}],
                }
            )
        return key_issues_reply

    return _side_effect, calls_by_model


def test_facts_peers_skipped_when_profile_already_sufficient(inst_path, store_path, monkeypatch):
    """Owner ask 2026-08-11: 'do we always need to regenerate peers and key facts… in case it's
    already have data, can we not let it affect the key insights'. A prior brief with a real
    profile must NOT trigger a facts_peers LLM call at all — facts/peers/description are carried
    forward UNCHANGED from prior (key_issues is unconditionally carried forward too now — see
    test_key_issues_always_carried_forward_from_prior_never_regenerated)."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path

    prior_facts = {"ceo": "Prior CEO", "sector": "Tech"}
    prior_peers = [{"name": "Prior Peer", "ticker": "PRIOR", "resolved": True}]
    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [],
            "facts": prior_facts,
            "peers": prior_peers,
            "description": "The prior description.",
            "synthesized_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "facts_peers_synthesized_at": (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat(),
            "search_provider": "x",
        }
    )

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    side_effect, calls_by_model = _facts_peers_tracking_side_effect(_to_model(_synthesis_json()))

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch("backend.monitoring.asset_brief.complete_model", side_effect):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    assert "_FactsPeersOutput" not in calls_by_model  # never called — profile already existed

    brief = AssetBriefStore().get("inst-1")
    assert brief["facts"] == prior_facts  # byte-identical — not touched
    assert brief["peers"] == prior_peers
    assert brief["description"] == "The prior description."
    assert brief["key_issues"] == []  # carried forward from prior (empty), unconditionally


def test_facts_peers_generated_when_profile_missing(inst_path, store_path, monkeypatch):
    """The inverse: a brand-new asset with no facts/peers/description yet DOES get the
    facts_peers call, and the result is persisted."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()
    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda q: {"symbol": "NEWP", "name": "New Peer", "exchange": "NASDAQ"},
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    side_effect, calls_by_model = _facts_peers_tracking_side_effect(_to_model(_synthesis_json()))

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch("backend.monitoring.asset_brief.complete_model", side_effect):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    assert "_FactsPeersOutput" in calls_by_model

    brief = AssetBriefStore().get("inst-1")
    assert brief["facts"]["ceo"] == "New CEO"
    assert brief["description"] == "A NEW description from this refresh."
    assert brief["facts_peers_synthesized_at"] is not None  # stamped — the call actually ran


def test_refresh_stage_ready_before_slow_facts_peers_finishes(inst_path, store_path, monkeypatch):
    """Owner report 2026-08-11: 'I see lucius's take is updated but then the bar is still
    showing'. Proves the fix still holds post-consolidation (Problem B / ADR 2026-08-28-cross-
    subject-fan-out-and-asset-brief-consolidation): refresh_stage flips to 'ready' — carrying
    key_issues/portfolio_impact forward from prior, which needs no LLM call at all any more — and
    is OBSERVABLE while a slow facts_peers call is still blocked, not just correct in the final
    state. Blocks the facts_peers call on a gate and polls the store while it's still blocked."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    async def _run():
        facts_peers_gate = asyncio.Event()
        ready_observed = asyncio.Event()

        async def _side_effect(prompt, response_model=None, **kw):
            if response_model is _mod._FactsPeersOutput:
                await asyncio.wait_for(facts_peers_gate.wait(), timeout=2.0)
                return _mod._FactsPeersOutput()
            return _to_model(_synthesis_json())

        complete_mock = AsyncMock(side_effect=_side_effect)

        async def _watch_and_release():
            for _ in range(200):
                brief = AssetBriefStore().get("inst-1")
                if brief and brief.get("refresh_stage") == "ready":
                    ready_observed.set()
                    break
                await asyncio.sleep(0.01)
            facts_peers_gate.set()

        with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
            with patch("backend.monitoring.asset_brief.complete_model", complete_mock):
                with patch(
                    "backend.services.profile.read_profile",
                    return_value={"base_currency": "SGD"},
                ):
                    with patch(_SC_PATH, return_value=sc_instance):
                        with patch(_BP_PATH, return_value=stub):
                            watcher = asyncio.create_task(_watch_and_release())
                            result = await _mod.refresh_asset_brief("inst-1", force=True)
                            await watcher
                            return result, ready_observed.is_set()

    result, observed_mid_refresh = asyncio.run(_run())

    assert observed_mid_refresh, "'ready' was never visible before facts_peers landed"
    assert result["refreshed"] is True


def test_lock_coalesce_debounces_inner_check(inst_path, store_path, monkeypatch):
    """Simulate a fresh brief being written by a concurrent refresh BEFORE we acquired the lock.
    The inner re-check inside the lock should return 'fresh' without calling LLM."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path

    called = []

    async def _fake_complete(*a, **kw):
        called.append(1)
        return _to_model(_synthesis_json())

    async def _run():
        # Clear any cached lock for this test
        _mod._refresh_locks.pop("inst-1", None)

        lock = _mod._get_lock("inst-1")
        from backend.db.asset_brief_store import AssetBriefStore

        # Simulate: while we wait to acquire the lock, another coroutine writes a fresh brief.
        async with lock:
            # Write a fresh brief while we hold the lock (simulating the first coroutine having completed)
            recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            AssetBriefStore().upsert_brief(
                {
                    "instrument_id": "inst-1",
                    "key_issues": [],
                    "facts": {},
                    "peers": [],
                    "description": None,
                    "synthesized_at": recent_ts,
                    "search_provider": "x",
                }
            )
            # Now the second "coroutine" tries to refresh — it should see the fresh brief
            with patch(
                "backend.monitoring.asset_brief.get_search_provider", return_value=_stub_provider()
            ):
                with patch(
                    "backend.monitoring.asset_brief.complete_model", side_effect=_fake_complete
                ):
                    with patch(
                        "backend.services.profile.read_profile",
                        return_value={"base_currency": "SGD"},
                    ):
                        result = await _mod.refresh_asset_brief("inst-1", force=False)
            return result

    result = asyncio.run(_run())
    # The inner re-check found a fresh brief → debounced
    assert result["refreshed"] is False
    assert result["reason"] == "fresh"
    assert called == []  # LLM was never invoked


def test_intel_scan_module_still_importable():
    """Characterization: run_intel_scan from intel_agent is still importable.
    Importing asset_brief must not break anything in monitoring/."""
    import importlib

    # Ensure intel_agent is importable (was importable before our changes)
    mod = importlib.import_module("backend.monitoring.intel_agent")
    assert hasattr(mod, "run_intel_scan")
    assert callable(mod.run_intel_scan)


# ── hard rate-limit floor ──────────────────────────────────────────────────────


def test_force_is_bounded_by_hard_floor(inst_path, store_path, monkeypatch):
    """force=True is still rejected if a refresh ran within _FORCE_FLOOR_SECONDS."""
    InstrumentStore().bulk_insert([_make_inst()])
    # Seed a brief that is just 5 seconds old — inside the hard floor.
    very_recent_ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    from backend.db import asset_brief_store as _abs
    from backend.db.asset_brief_store import AssetBriefStore

    _abs.ASSET_BRIEF_JSON = store_path
    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [],
            "facts": {},
            "peers": [],
            "description": None,
            "synthesized_at": very_recent_ts,
            "search_provider": "x",
        }
    )

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=_stub_provider()):
        with patch(
            "backend.monitoring.asset_brief.complete_model", new_callable=AsyncMock
        ) as mock_complete:
            # force=True should still be rejected within the hard floor
            result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is False
    assert result["reason"] == "rate_limited"
    mock_complete.assert_not_called()


# ── URL scheme sanitization ────────────────────────────────────────────────────


def test_safe_url_allows_http_https():
    from backend.monitoring.asset_brief import _safe_url

    assert _safe_url("https://example.com") == "https://example.com"
    assert _safe_url("http://example.com/path") == "http://example.com/path"


def test_safe_url_strips_javascript_scheme():
    from backend.monitoring.asset_brief import _safe_url

    assert _safe_url("javascript:alert(1)") is None
    assert _safe_url("data:text/html,<h1>x</h1>") is None
    assert _safe_url("ftp://example.com") is None
    assert _safe_url("") is None


# ── recency gate: drop news older than ~6 months before fetch + synthesis ───────


def test_is_stale_news_drops_old_dated_articles():
    # Datable to older than 6 months → stale (dropped before page-fetch + LLM).
    assert _mod._is_stale_news({"url": "https://etoro.com/x", "published_at": "9 months ago"})
    assert _mod._is_stale_news(
        {"url": "https://rendementbourse.com/a", "published_at": "1 year ago"}
    )
    assert _mod._is_stale_news({"url": "https://news.com/a", "published_at": "2024-01-01"})


def test_is_stale_news_keeps_recent_articles():
    assert not _mod._is_stale_news({"url": "https://news.com/a", "published_at": "2 weeks ago"})
    assert not _mod._is_stale_news({"url": "https://news.com/a", "published_at": "3 months ago"})
    assert not _mod._is_stale_news({"url": "https://news.com/a", "published_at": "today"})


def test_is_stale_news_keeps_undated_pages():
    # A quote/reference page with no date can't be proven old → kept (sort already lowers it),
    # so providers that omit dates don't get their whole feed nuked.
    assert not _mod._is_stale_news(
        {"url": "https://finance.yahoo.com/quote/ONWAI.DE", "published_at": ""}
    )


def test_is_stale_news_exempts_evergreen_reference_hosts():
    # Wikipedia reads as "7y ago" but is evergreen reference for the description/facts → kept.
    assert not _mod._is_stale_news(
        {"url": "https://fr.wikipedia.org/wiki/Kestrel_Dash", "published_at": "7 years ago"}
    )
    assert not _mod._is_stale_news(
        {"url": "https://en.wikipedia.org/wiki/Uber", "published_at": "2018-01-01"}
    )


# ── news-list assembly: drop undated filler, collapse near-duplicate stories ────


def test_has_known_publish_date_drops_undated_keeps_dated():
    assert not _mod._has_known_publish_date({"url": "https://x/a", "published_at": ""})
    assert not _mod._has_known_publish_date({"url": "https://x/a"})  # field absent entirely
    assert _mod._has_known_publish_date({"url": "https://x/a", "published_at": "2 hours ago"})
    assert _mod._has_known_publish_date(
        {"url": "https://x/a", "published_at": "2026-08-20T10:00:00+00:00"}
    )


def test_filter_and_collapse_news_drops_generic_filler_cards():
    # Real, confirmed shape: evergreen company-profile/quote pages carry no publish date at
    # all (Fix 2) — dropped even though they'd otherwise sort below real news, not filtered
    # by title text ("Company Overview" is never pattern-matched).
    items = [
        {
            "title": "Kestrel Dash Company Overview",
            "url": "https://finance.yahoo.com/quote/KSTL.DE/profile",
            "source": "Yahoo Finance",
            "domain": "finance.yahoo.com",
            "published_at": "",
        },
        {
            "title": "Kestrel Dash Stock Price and Performance Data",
            "url": "https://marketscreener.com/quote/stock/KESTREL-DASH-SE",
            "source": "marketscreener.com",
            "domain": "marketscreener.com",
            "published_at": "",
        },
        {
            "title": "Kestrel Dash Reports Q2 Earnings Beat, Raises Guidance",
            "url": "https://reuters.com/article/kstl-q2",
            "source": "Reuters",
            "domain": "reuters.com",
            "published_at": "2 hours ago",
        },
    ]
    result = _mod._filter_and_collapse_news(items, "Kestrel Dash")
    assert [i["url"] for i in result] == ["https://reuters.com/article/kstl-q2"]


# ── second, narrower filter: generic reference-page title endings ──────────────
#
# Confirmed live 2026-08-29: the date-only check above is NOT sufficient on its own — a live
# refresh showed "Kestrel Dash Company Overview" (reuters.com) STILL on the page, now WITH a
# real-looking "19h ago" timestamp attached (evidently some evergreen reference pages DO get a
# timestamp — likely a page-fetch/crawl time from the search provider, not a true
# article-publish date), which defeats `_has_known_publish_date` alone. These are the exact
# real title strings observed this session.


def test_is_generic_reference_title_drops_confirmed_real_examples():
    assert _mod._is_generic_reference_title("Kestrel Dash Company Overview")
    assert _mod._is_generic_reference_title("Kestrel Dash Stock Price and Performance Data")
    assert _mod._is_generic_reference_title("Kestrel Dash SE Stock Profile and Overview")
    # Yahoo Finance's fixed quote-page title template — borderline because it also says "News"
    # mid-title, but the whole phrase is a static page-layout label (every Yahoo Finance quote
    # page reads this way, news or no news), not prose about one event. Kept ON the list — see
    # the ADR correction for the full reasoning.
    assert _mod._is_generic_reference_title(
        "Kestrel Dash SE (KSTL.DE) Stock Price, News, Quote & History - Yahoo Finance"
    )


def test_is_generic_reference_title_keeps_genuine_headlines():
    # Real genuine news headlines seen live in the owner's own screenshots this session — must
    # all survive: none of them happens to END with one of the generic template phrases.
    genuine = [
        "Uber Formally Launches €41.50 Takeover Offer for Kestrel Dash",
        "Kestrel Dash Major Shareholder Reduces Voting Rights Position",
        "Berenberg Reaffirms Buy Rating on Kestrel Dash",
        "Kestrel Dash Raises Full-Year 2026 Guidance",
        "Kestrel Dash Stock Faces Valuation Headwinds as Losses Widen",
    ]
    for title in genuine:
        assert not _mod._is_generic_reference_title(title), title


def test_is_generic_reference_title_catches_marketscreener_quote_venue_template():
    # Owner-reproduced live, 2026-08-31: marketscreener.com's own quote-page template appends
    # "Quote <ExchangeName>" — a DIFFERENT venue per ticker/exchange, so this must be a pattern
    # match (any venue word), never a literal "quote xetra" string (this codebase's own standing
    # rule: no per-ticker/per-instance hardcoding). Exact real titles pulled from the live
    # evidence table for this exact incident — including the asymmetric-spacing one
    # ("Xetra- MarketScreener", no space before that dash) that was the actual live bug: the
    # shared trailing-source-suffix stripper required a space on BOTH sides and silently never
    # fired for this shape, hiding the generic ending from every check.
    real_titles = [
        "Kestrel Dash SE Stock (KSTL) - Quote Xetra- MarketScreener",
        "Kestrel Dash SE Stock (KSTL) - Quote Tradegate- MarketScreener",
        "Kestrel Dash SE Stock (KSTL) - Quote Xetra - Stock Market",
    ]
    for title in real_titles:
        assert _mod._is_generic_reference_title(title), title
    # A DIFFERENT exchange name (never seen live) must ALSO be caught — proves this is a
    # generic pattern match, not a hardcoded "xetra"/"tradegate" literal.
    assert _mod._is_generic_reference_title(
        "Apple Inc Stock (AAPL) - Quote Nasdaq - MarketScreener"
    )


def test_trailing_source_suffix_strip_handles_asymmetric_spacing():
    # Owner-reproduced live, 2026-08-31: a dash with a space on only ONE side (no space before,
    # one after — "Xetra- MarketScreener") must still strip as a real separator; a genuine
    # compound-word hyphen (space on NEITHER side, e.g. "self-driving") must NOT be treated as a
    # separator and must survive untouched.
    assert _mod._is_generic_reference_title("X Stock (X) - Quote Nasdaq- SomeSource")
    assert _mod._is_generic_reference_title("X Stock (X) - Quote Nasdaq -SomeSource")
    assert not _mod._is_generic_reference_title(
        "Self-driving trucks post record deliveries - TechCrunch"
    )
    assert not _mod._is_generic_reference_title(
        "Co-founder steps down amid restructuring - Reuters"
    )


def test_is_generic_reference_title_is_suffix_only_not_substring_anywhere():
    # A real headline that merely MENTIONS "overview"/"quote" mid-title (not at the end) must
    # survive — this is the false-positive risk a substring-anywhere match would have created.
    assert not _mod._is_generic_reference_title(
        "Analyst Overview Sees Kestrel Dash Raising Full-Year Guidance"
    )
    assert not _mod._is_generic_reference_title(
        "Kestrel Dash Gets a Rare Buy Rating Quote From Berenberg Today"
    )


def test_is_generic_reference_title_survives_a_single_greedy_strip():
    # Owner report 2026-08-31, confirmed live: CNBC's "X: Company Name - Stock Price, Quote and
    # News" template rendered BOTH with a trailing " - CNBC" source tail (one dash before the
    # ending phrase, one after it) AND without one (a single dash right before the ending
    # phrase, nothing after it). The old single-strip stripped whatever came after the LAST
    # dash unconditionally — for the no-trailing-source shape that IS the ending phrase itself,
    # so it got stripped away too, leaving a truncated string that matched nothing. Checking the
    # title as-is before stripping (and stripping repeatedly, not just once) fixes both shapes.
    assert _mod._is_generic_reference_title("MSFT: Microsoft Corp - Stock Price, Quote and News")
    assert _mod._is_generic_reference_title(
        "AMD: Advanced Micro Devices Inc - Stock Price, Quote and News"
    )
    assert _mod._is_generic_reference_title(
        "Meta Platforms Inc - Stock Price, Quote and News - CNBC"
    )
    assert _mod._is_generic_reference_title(
        "NOW: ServiceNow Inc - Stock Price, Quote and News - CNBC"
    )
    # A chained TWO-segment trailing tail (source-of-source, e.g. an aggregator crediting the
    # original site) must also strip down to the real ending underneath both segments.
    assert _mod._is_generic_reference_title(
        "XNDU Stock Price Quote & News - Xanadu Quantum - Robinhood"
    )
    # Guard: a genuine headline ending in a real (non-template) trailing clause, with a dash
    # before its own source attribution, must still survive.
    assert not _mod._is_generic_reference_title(
        "Tesla Wins Over Another Skeptic: JPMorgan Raises TSLA Stock Price Target By More Than "
        "200% - Yahoo Finance"
    )


# ── third filter: filename-shaped titles (a camera/screenshot filename used as a headline) ──
#
# Confirmed live 2026-08-29: a Stories & Analysis card showed a raw uploaded-screenshot filename
# ("Screenshot <date> <time> - <publisher>") as its headline — a genuinely fetched, dated,
# non-garbled, non-generic-reference item, so none of the three existing filters catch it. The
# fixtures below use a fictional small-cap and a fictional publisher rather than the real
# company/site involved in the incident.


def test_is_filename_shaped_title_drops_known_camera_screenshot_conventions():
    assert _mod._is_filename_shaped_title("Screenshot 2026-08-24 093626")
    assert _mod._is_filename_shaped_title("Screen Shot 2020-01-01 at 10.00.00 AM")
    assert _mod._is_filename_shaped_title("IMG_1234")
    assert _mod._is_filename_shaped_title("IMG-20260824-WA0001")
    assert _mod._is_filename_shaped_title("DSC_04521")
    assert _mod._is_filename_shaped_title("Photo_20260824_093626")
    assert _mod._is_filename_shaped_title("VID_20260824_093626")


def test_is_filename_shaped_title_drops_pure_date_time_digit_strings():
    # No known camera prefix at all, but still "mostly digits/separators, no real words" — the
    # general shape, not just the known-prefix list.
    assert _mod._is_filename_shaped_title("20260824_093626")
    assert _mod._is_filename_shaped_title("2026-08-24-093626")


def test_is_filename_shaped_title_strips_trailing_source_suffix_first():
    # The exact confirmed live shape: "Screenshot <date> <time> - <publisher>". The trailing
    # " - PublisherName" must not hide the filename shape from the check.
    assert _mod._is_filename_shaped_title("Screenshot 2026-08-24 093626 - Examplenews.com")


def test_is_filename_shaped_title_keeps_genuine_headlines():
    genuine = [
        "Acme Metals Reports Record Rare Earth Output in Q2",
        "Acme Metals Shares Jump 12% on Supply Deal With Regional Automaker",
        "IMG Worldwide Signs New Talent Representation Deal",  # "IMG" as a real word, no digits
        "Fed Cuts Rates 25 Basis Points, Acme Metals Rallies",
        "3M Reports Strong Quarterly Earnings",
    ]
    for title in genuine:
        assert not _mod._is_filename_shaped_title(title), title


def test_filter_and_collapse_news_drops_filename_shaped_titled_card():
    items = [
        {
            "title": "Screenshot 2026-08-24 093626 - Examplenews.com",
            "url": "https://examplenews.com/uploads/screenshot-1",
            "source": "examplenews.com",
            "domain": "examplenews.com",
            "published_at": "3d ago",
        },
        {
            "title": "Acme Metals Reports Record Rare Earth Output in Q2",
            "url": "https://examplenews.com/article/acme-q2-output",
            "source": "examplenews.com",
            "domain": "examplenews.com",
            "published_at": "2 hours ago",
        },
    ]
    # Confirms the gap: the filename-titled item passes both existing filters on its own.
    assert _mod._has_known_publish_date(items[0])
    assert not _mod._is_generic_reference_title(items[0]["title"])
    result = _mod._filter_and_collapse_news(items, "Acme Metals")
    assert [i["url"] for i in result] == ["https://examplenews.com/article/acme-q2-output"]


def test_filter_and_collapse_news_drops_generic_titled_card_even_with_a_known_date():
    # The exact confirmed live-repro shape: "Kestrel Dash Company Overview" (reuters.com) with
    # a real-looking relative date attached, so `_has_known_publish_date` alone would KEEP it —
    # the second filter stage must still drop it.
    items = [
        {
            "title": "Kestrel Dash Company Overview",
            "url": "https://reuters.com/companies/KSTL.DE",
            "source": "Reuters",
            "domain": "reuters.com",
            "published_at": "19h ago",
        },
        {
            "title": "Kestrel Dash Reports Q2 Earnings Beat, Raises Guidance",
            "url": "https://reuters.com/article/kstl-q2",
            "source": "Reuters",
            "domain": "reuters.com",
            "published_at": "2 hours ago",
        },
    ]
    # Confirms the gap: the dated filler item passes the date-only check on its own.
    assert _mod._has_known_publish_date(items[0])
    result = _mod._filter_and_collapse_news(items, "Kestrel Dash")
    assert [i["url"] for i in result] == ["https://reuters.com/article/kstl-q2"]


# The Dr. Bruder insider-sale shape from the owner's live screenshot: one real story, the
# exact same core fact (2,728 shares @ EUR 37.07456763), reported under 3 near-identical but
# STRING-DISTINCT source labels. `dedupe_stories` is same-publisher-only by design (its own
# module docstring), and these three labels never share a casefolded source/domain string —
# so it structurally cannot collapse any of them (asserted below), which is exactly the "3
# copies of one insider sale" bug.
_BRUDER_TITLE_A = "Kestrel Dash SE: Dr. Johannes Bruder Sells 2,728 Shares At EUR 37.07456763"
_BRUDER_TITLE_B = (
    "Kestrel Dash (KSTL.DE) Insider Dr. Johannes Bruder Sold 2,728 Shares At EUR 37.07456763"
)
_BRUDER_TITLE_C = (
    "Insider Sale: Dr. Johannes Bruder Disposes Of 2,728 Kestrel Dash Shares At EUR 37.07456763"
)


def _bruder_items() -> list[dict]:
    return [
        {
            "title": _BRUDER_TITLE_A,
            "url": "https://marketscreener.com/quote/stock/KSTL/news/bruder-sale-a",
            "source": "marketscreener.com",
            "domain": "marketscreener.com",
            "published_at": "2 hours ago",
        },
        {
            "title": _BRUDER_TITLE_B,
            "url": "https://tradingview.com/news/kstl-insider-bruder-b",
            "source": "tradingview.com",
            "domain": "tradingview.com",
            "published_at": "3 hours ago",
        },
        {
            "title": _BRUDER_TITLE_C,
            "url": "https://www.tradingview.com/symbols/XETR-KSTL/news/bruder-sale-c",
            "source": "TradingView",
            "domain": "",
            "published_at": "1 hour ago",
        },
    ]


def test_dedupe_stories_alone_cannot_collapse_the_bruder_shape():
    # Documents WHY Fix 1 is needed: the existing same-publisher-only dedup leaves all 3
    # copies standing, because "marketscreener.com" / "tradingview.com" / "TradingView" never
    # match as publisher keys even though two of them are literally the same outlet.
    result = _mod._dedupe_stories(_bruder_items())
    assert len(result) == 3


def test_filter_and_collapse_news_collapses_bruder_duplicate_to_one_card():
    result = _mod._filter_and_collapse_news(_bruder_items(), "Kestrel Dash")
    assert len(result) == 1


def test_filter_and_collapse_news_keeps_genuinely_distinct_stories():
    items = [
        {
            "title": _BRUDER_TITLE_A,
            "url": "https://marketscreener.com/quote/stock/KSTL/news/bruder-sale-a",
            "source": "marketscreener.com",
            "domain": "marketscreener.com",
            "published_at": "2 hours ago",
        },
        {
            "title": "Kestrel Dash Announces $500M Share Buyback Programme",
            "url": "https://reuters.com/article/kstl-buyback",
            "source": "Reuters",
            "domain": "reuters.com",
            "published_at": "1 day ago",
        },
    ]
    result = _mod._filter_and_collapse_news(items, "Kestrel Dash")
    assert len(result) == 2


def test_sanitize_key_issues_sources_drops_javascript():
    from backend.monitoring.asset_brief import _sanitize_key_issues_sources

    issues = [
        {
            "question": "Will earnings beat?",
            "bullish": {
                "text": "yes",
                "sources": ["https://good.com", "javascript:evil()", "ftp://bad.com"],
            },
            "bearish": {
                "text": "no",
                "sources": ["http://ok.com"],
            },
        }
    ]
    result = _sanitize_key_issues_sources(issues)
    # Sources are now coerced to {url, quote} dicts; unsafe schemes are dropped.
    bull_sources = result[0]["bullish"]["sources"]
    bear_sources = result[0]["bearish"]["sources"]
    assert len(bull_sources) == 1
    assert bull_sources[0]["url"] == "https://good.com"
    assert len(bear_sources) == 1
    assert bear_sources[0]["url"] == "http://ok.com"


# ── _parse_tool_result regression (fenced JSON) ───────────────────────────────


@pytest.mark.skip(
    reason="_parse_tool_result removed — tolerant fence/wrapper parsing now lives in the "
    "structured-output layer (ai.structured / core.llm_seam), covered by its own tests."
)
def test_parse_tool_result_fenced_json():
    """Regression: a fenced ```json block with key_issues parses correctly."""
    from backend.monitoring.asset_brief import _parse_tool_result

    raw = '```json\n{"key_issues":[{"question":"Q","bullish":{"text":"b","sources":[]},"bearish":{"text":"be","sources":[]}}]}\n```'
    result = _parse_tool_result(raw)
    assert result is not None
    assert "key_issues" in result
    assert len(result["key_issues"]) == 1
    assert result["key_issues"][0]["question"] == "Q"


@pytest.mark.skip(
    reason="_parse_tool_result removed — tolerant JSON parsing now lives in the "
    "structured-output layer (ai.structured / core.llm_seam), covered by its own tests."
)
def test_parse_tool_result_plain_json():
    """Plain JSON object is parsed directly."""
    from backend.monitoring.asset_brief import _parse_tool_result

    raw = '{"key_issues":[],"description":"test"}'
    result = _parse_tool_result(raw)
    assert result is not None
    assert result["description"] == "test"


# ── Fix 1 regression: synthesis maps raw-JSON reply (not tool-call) to key_issues ──


@pytest.mark.skip(
    reason="Fenced-block parsing moved to the structured-output layer (ai.structured / "
    "core.llm_seam); _synthesize_asset now receives an already-validated model, so this "
    "seam-level parsing case is covered by the layer's own tests."
)
def test_synthesize_fenced_json_parses():
    """Fix 1: a fenced ```json block response also parses correctly."""
    inner = json.dumps(
        {
            "key_issues": [
                {
                    "question": "Q fenced",
                    "bullish": {"text": "b", "sources": []},
                    "bearish": {"text": "be", "sources": []},
                }
            ]
        }
    )
    raw_reply = f"```json\n{inner}\n```"

    async def _run():
        with patch(
            "backend.monitoring.asset_brief.complete",
            new_callable=AsyncMock,
            return_value=raw_reply,
        ):
            return await _mod._synthesize_asset(
                {"name": "Test", "price_source_id": "T", "asset_class": "equity"},
                [],
                "SGD",
            )

    result = asyncio.run(_run())
    assert "key_issues" in result
    assert len(result["key_issues"]) == 1
    assert result["key_issues"][0]["question"] == "Q fenced"


@pytest.mark.skip(
    reason='Tool-use {"input": {...}} unwrapping moved to the structured-output layer; '
    "_synthesize_asset now receives an already-validated model, so this seam-level case is "
    "covered by the layer's own tests."
)
def test_synthesize_input_wrapper_parses():
    """Fix 1: a {"input": {...}} tool-use wrapper is unwrapped correctly."""
    inner = {
        "key_issues": [
            {
                "question": "Q wrapped",
                "bullish": {"text": "b", "sources": []},
                "bearish": {"text": "be", "sources": []},
            }
        ]
    }
    raw_reply = json.dumps({"input": inner})

    async def _run():
        with patch(
            "backend.monitoring.asset_brief.complete",
            new_callable=AsyncMock,
            return_value=raw_reply,
        ):
            return await _mod._synthesize_asset(
                {"name": "Test", "price_source_id": "T", "asset_class": "equity"},
                [],
                "SGD",
            )

    result = asyncio.run(_run())
    assert "key_issues" in result
    assert len(result["key_issues"]) == 1
    assert result["key_issues"][0]["question"] == "Q wrapped"


# ── Fix 2: source hostname fallback ──────────────────────────────────────────


def test_hostname_helper():
    """_hostname() strips www. and handles malformed urls gracefully."""
    from backend.monitoring.asset_brief import _hostname

    assert _hostname("https://www.simplywall.st/stocks/test") == "simplywall.st"
    assert _hostname("https://reuters.com/article") == "reuters.com"
    assert _hostname("") == ""
    assert _hostname("not-a-url") == ""


def test_source_hostname_fallback():
    """Fix 2 regression: a result with no 'source' field yields hostname as source."""
    results_no_source = [
        {
            "title": "Article",
            "url": "https://www.simplywall.st/test",
            "published_date": _days_ago(6),
            "content": "snippet text",
            # deliberately no 'source' key
        }
    ]

    class _ProviderNoSource:
        async def search(self, *a, **kw):
            return results_no_source

        async def fetch_page(self, url: str) -> str:
            return ""

    sc_instance = _make_sc_mock(["perplexity"])

    async def _run():
        with patch(_GSP_PATH, return_value=_ProviderNoSource()):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=_ProviderNoSource()):
                    return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) == 1
    assert items[0]["source"] == "simplywall.st"


# ── Fix 3: provider error resilience ─────────────────────────────────────────


def test_one_provider_failure_does_not_kill_other():
    """Fix 3 regression: one failing provider → the other still contributes results."""

    class _GoodProvider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Good result",
                    "url": "https://good.com/1",
                    "published_date": _days_ago(6),
                    "content": "good content",
                    "source": "Good",
                }
            ]

        async def fetch_page(self, url: str) -> str:
            return ""

    class _BadProvider:
        async def search(self, *a, **kw):
            raise RuntimeError("network down")

        async def fetch_page(self, url: str) -> str:
            return ""

    sc_instance = _make_sc_mock(["tavily", "perplexity"])

    call_count = 0

    def _build(pid, cfg):
        nonlocal call_count
        call_count += 1
        if pid == "tavily":
            return _BadProvider()
        return _GoodProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=_GoodProvider()):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, side_effect=_build):
                    return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert call_count == 2  # both providers were attempted
    assert len(items) == 1
    assert items[0]["url"] == "https://good.com/1"


def test_providers_are_queried_concurrently():
    """Two enabled providers must be queried CONCURRENTLY, not one-after-another — regression
    for the old `for pid in provider_ids: await news_fn(...)` loop where one provider's
    latency fully blocked the next from even starting. Proven via a barrier: each provider
    only returns once BOTH have started, which is only reachable under real concurrency —
    a sequential loop would hang here, so the wait is bounded and fails loudly instead."""
    started: list[str] = []
    barrier = asyncio.Event()

    class _BarrierProvider:
        def __init__(self, name: str):
            self.name = name

        async def search(self, *a, **kw):
            started.append(self.name)
            if len(started) < 2:
                try:
                    await asyncio.wait_for(barrier.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pytest.fail("providers were not queried concurrently (sequential regression)")
            else:
                barrier.set()
            return []

        async def fetch_page(self, url: str) -> str:
            return ""

    sc_instance = _make_sc_mock(["serper", "tavily"])

    async def _run():
        with patch(_GSP_PATH, return_value=_BarrierProvider("gsp")):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, side_effect=lambda pid, cfg: _BarrierProvider(pid)):
                    return await _mod._fetch_news_items("test query")

    asyncio.run(_run())
    assert set(started) == {"serper", "tavily"}


# ── Fix 4: multi-provider merge + dedup + snippet strip ──────────────────────


def test_multi_provider_dedup_by_url():
    """Fix 4 regression: two providers returning overlapping urls → merged+deduped."""
    shared_url = "https://shared.com/article"
    provider_a_results = [
        {
            "title": "Shared article",
            "url": shared_url,
            "published_date": _days_ago(6),
            "content": "content A",
            "source": "SourceA",
        },
        {
            "title": "Unique to A",
            "url": "https://a.com/only",
            "published_date": _days_ago(5),
            "content": "content only A",
            "source": "SourceA",
        },
    ]
    provider_b_results = [
        {
            "title": "Shared article (B copy)",
            "url": shared_url,  # same url — should be deduped
            "published_date": _days_ago(6),
            "content": "content B dupe",
            "source": "SourceB",
        },
        {
            "title": "Unique to B",
            "url": "https://b.com/only",
            "published_date": _days_ago(4),
            "content": "content only B",
            "source": "SourceB",
        },
    ]

    sc_instance = _make_sc_mock(["tavily", "perplexity"])

    def _build(pid, cfg):
        if pid == "tavily":
            return _stub_provider(provider_a_results)
        return _stub_provider(provider_b_results)

    async def _run():
        with patch(_GSP_PATH, return_value=_stub_provider()):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, side_effect=_build):
                    return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    urls = [i["url"] for i in items]
    # shared_url appears exactly once (first-occurrence wins)
    assert urls.count(shared_url) == 1
    # Total: 3 unique urls
    assert len(items) == 3


def test_snippet_present_in_items_for_synthesis():
    """Fix 4: snippet field is present in items returned by _fetch_news_items."""
    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()

    async def _run():
        with patch(_GSP_PATH, return_value=stub):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=stub):
                    return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) > 0
    # Each item must carry a snippet field
    for item in items:
        assert "snippet" in item


def test_snippet_reaches_facts_peers_synthesis(inst_path, store_path, monkeypatch):
    """Fix 4 regression: the full-text-fetched snippet reaches synthesis. Observed via
    `_synthesize_facts_peers` (the now-removed `_synthesize_asset` was the original observation
    point — Problem B / ADR 2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation moved
    key_issues synthesis out of this module entirely; facts_peers is still fed the exact same
    news_items, so it proves the same property). The old second half of this test — "persisted
    news has no snippet" — no longer applies: `asset_brief` doesn't persist a news list at all
    any more (RFC intel-signals-data-model §5); `evidence.snippet` is independently bounded to
    500 chars by `services.evidence._record_from_hit`, covered by that module's own tests."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path

    synthesis_items_seen: list[list[dict]] = []

    async def _fake_synthesize(asset_row, news_items, base):
        synthesis_items_seen.append(list(news_items))
        return json.loads(_synthesis_json())

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(_SC_PATH, return_value=sc_instance):
            with patch(_BP_PATH, return_value=stub):
                with patch(
                    "backend.monitoring.asset_brief._synthesize_facts_peers",
                    side_effect=_fake_synthesize,
                ):
                    with patch(
                        "backend.services.profile.read_profile",
                        return_value={"base_currency": "SGD"},
                    ):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1"))

    assert result["refreshed"] is True

    # synthesis received items WITH snippet
    assert len(synthesis_items_seen) == 1
    synth_items = synthesis_items_seen[0]
    assert len(synth_items) > 0
    assert "snippet" in synth_items[0]


# ── Enhancement A: full-text page fetch ──────────────────────────────────────


def test_full_text_fetch_replaces_snippet():
    """Enhancement A: a successful fetch_page replaces the item's snippet with full text."""

    full_text = "This is the full article text. " * 50  # 1600 chars

    class _FullTextProvider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Article",
                    "url": "https://news.example.com/article",
                    "published_date": _days_ago(6),
                    "content": "short snippet",
                    "source": "NewsSource",
                }
            ]

        async def fetch_page(self, url: str) -> str:
            return full_text

    sc_instance = _make_sc_mock(["tavily"])
    provider = _FullTextProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) == 1
    # snippet should now be the full text (trimmed to _SNIPPET_FULL_MAX = 3000)
    assert items[0]["snippet"] == full_text.strip()[: _mod._SNIPPET_FULL_MAX]
    assert len(items[0]["snippet"]) > len("short snippet")


def test_failing_fetch_keeps_original_snippet():
    """Enhancement A: a fetch_page failure keeps the original search snippet; item not dropped."""
    original_snippet = "original search snippet"

    class _FailingFetchProvider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Article",
                    "url": "https://news.example.com/article",
                    "published_date": _days_ago(6),
                    "content": original_snippet,
                    "source": "NewsSource",
                }
            ]

        async def fetch_page(self, url: str) -> str:
            raise RuntimeError("connection refused")

    sc_instance = _make_sc_mock(["tavily"])
    provider = _FailingFetchProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) == 1  # item is not dropped
    assert items[0]["snippet"] == original_snippet


def test_unsafe_url_keeps_original_snippet():
    """Enhancement A: an UnsafeUrlError from assert_public_url keeps the original snippet."""
    from backend.core.net_guard import UnsafeUrlError

    original_snippet = "the original snippet"

    class _Provider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Article",
                    "url": "https://example.com/article",
                    "published_date": _days_ago(6),
                    "content": original_snippet,
                    "source": "Src",
                }
            ]

        async def fetch_page(self, url: str) -> str:
            return "should not be called"

    sc_instance = _make_sc_mock(["tavily"])
    provider = _Provider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url",
                        side_effect=UnsafeUrlError("blocked"),
                    ):
                        return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) == 1
    assert items[0]["snippet"] == original_snippet


def test_ssrf_guard_runs_off_the_event_loop():
    """The SSRF guard is still called for every URL — just not on the loop thread.

    assert_public_url ends in a blocking socket.getaddrinfo, so a slow or hanging DNS answer
    for ONE article URL stalled the entire app. It was the most frequent blocker in the
    2026-08-11 core.loop_watch reports (18:02, 22:07, 22:24). The fix moves the CALL to a
    worker thread and changes nothing about the check — test_unsafe_url_keeps_original_snippet
    above still pins that a blocked URL is refused. This pins the other half, which no
    behavioural test can see: that the refusal is decided off the loop.
    """
    threads: list[str] = []

    class _Provider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Article",
                    "url": "https://example.com/article",
                    "published_date": _days_ago(6),
                    "content": "snippet",
                    "source": "Src",
                }
            ]

        async def fetch_page(self, url: str) -> str:
            return "full article text that is long enough to replace the snippet entirely"

    def _spy_guard(url: str) -> str:
        threads.append(threading.current_thread().name)
        return url

    sc_instance = _make_sc_mock(["tavily"])
    provider = _Provider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url",
                        side_effect=_spy_guard,
                    ):
                        return await _mod._fetch_news_items("test query")

    asyncio.run(_run())

    assert threads, "SSRF guard was not called at all — the check must not be skippable"
    # to_thread dispatches to the default executor ("asyncio_N"); inline would be MainThread.
    assert all(t.startswith("asyncio_") for t in threads), (
        f"SSRF guard ran on the event loop: {threads}"
    )


def test_page_ceiling_caps_fetches():
    """Enhancement A: only _PAGE_FETCH_CEILING pages are fetched even when more are available."""
    fetch_calls: list[str] = []
    ceiling = _mod._PAGE_FETCH_CEILING  # 30

    # Build 35 items — 5 more than the ceiling
    many_results = [
        {
            "title": f"Article {i}",
            "url": f"https://news.example.com/article/{i}",
            "published_date": _days_ago(6),
            "content": f"snippet {i}",
            "source": "Src",
        }
        for i in range(ceiling + 5)
    ]

    class _CountingProvider:
        async def search(self, *a, **kw):
            return many_results

        async def fetch_page(self, url: str) -> str:
            fetch_calls.append(url)
            return "full text"

    sc_instance = _make_sc_mock(["tavily"])
    provider = _CountingProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        return await _mod._fetch_news_items("test query")

    asyncio.run(_run())
    assert len(fetch_calls) == ceiling


def test_one_slow_fetch_does_not_block_others():
    """Enhancement A: a timeout on one fetch doesn't prevent the others from succeeding."""
    fetch_calls: list[str] = []

    class _SlowOnceProvider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Fast article",
                    "url": "https://fast.example.com/article",
                    "published_date": _days_ago(6),
                    "content": "fast snippet",
                    "source": "Fast",
                },
                {
                    "title": "Slow article",
                    "url": "https://slow.example.com/article",
                    "published_date": _days_ago(6),
                    "content": "slow snippet",
                    "source": "Slow",
                },
            ]

        async def fetch_page(self, url: str) -> str:
            fetch_calls.append(url)
            if "slow" in url:
                raise asyncio.TimeoutError()
            # Realistic article length so it isn't flagged as a thin/blocked page.
            return "Fast article body. " * 40

    sc_instance = _make_sc_mock(["tavily"])
    provider = _SlowOnceProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) == 2
    fast_item = next(i for i in items if "fast" in i["url"])
    slow_item = next(i for i in items if "slow" in i["url"])
    assert fast_item["snippet"].startswith("Fast article body.")
    assert slow_item["snippet"] == "slow snippet"  # original kept on timeout


def test_cloudflare_challenge_page_keeps_original_snippet():
    """A 200 that's actually a bot-wall/challenge page must NOT overwrite the news snippet."""

    class _BlockedProvider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Real headline",
                    "url": "https://walled.example.com/article",
                    "published_date": _days_ago(6),
                    "content": "real news snippet from the search API",
                    "source": "Walled",
                },
            ]

        async def fetch_page(self, url: str) -> str:
            return "Just a moment...\nEnable JavaScript and cookies to continue. cf-browser-verification"

    sc_instance = _make_sc_mock(["tavily"])
    provider = _BlockedProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) == 1
    # Challenge-page text rejected → the real search snippet is preserved.
    assert items[0]["snippet"] == "real news snippet from the search API"


# ── Failed-extraction pre-synthesis filter (owner-confirmed live, 2026-08-29) ─────────────
#
# A brand-new ticker's first-ever refresh (no prior evidence, forced onto the live-search
# fallback) can return quote/overview-page URLs whose full-text fetch never lands real
# content — a bot-wall, a network failure, or (the confirmed live case) a response that's
# long enough and marker-free but is actually garbled/binary garbage. Left in, these reached
# the news_summaries synthesis prompt with nothing to say and the model narrated the
# extraction failure itself as if it were the story, e.g. "the fetched content was unreadable
# binary data with no extractable facts" for a Yahoo Finance quote page.


def test_looks_garbled_flags_replacement_char_flood():
    """A mis-decoded binary body (plain_get_fetch decodes with errors="replace") floods the
    text with U+FFFD — the clearest "this isn't an article" signal."""
    assert _mod._looks_garbled("�" * 500) is True


def test_looks_garbled_flags_markup_script_soup_but_not_real_prose():
    """A JS-only page's raw-HTML/script fallback passes _looks_blocked's length/marker gate
    (real bytes, real length, no known bot-wall phrase) but isn't real article prose — high
    structural/code-punctuation density is the second, independent signal. A real article,
    even one with unusually heavy parenthetical/bracket punctuation, must not trip it."""
    html_js_soup = (
        "<html><head><script>function foo(){var x={a:1,b:2};}</script></head><body></body></html>"
    ) * 5
    assert _mod._looks_garbled(html_js_soup) is True

    real_prose = (
        "Kestrel Dash SE (ETR: KSTL) reported Q2 revenue of $2.1B (+12% YoY), with "
        "adjusted EBITDA of $180M (vs. $150M est.). The company said margins improved "
        "amid cost cuts and raised full-year guidance."
    ) * 4
    assert _mod._looks_garbled(real_prose) is False


def test_looks_garbled_flags_numeric_data_dump_low_prose_ratio():
    """A wall of quote-widget numbers/symbols with almost no letters is caught by the
    prose-ratio signal (independent of the structural-punctuation and replacement-char
    checks above)."""
    numeric_dump = (
        "52.31 -0.42 (-0.80%) Vol: 1.2M P/E 18.4 Mkt Cap 12.3B Div 1.20 (2.1%) "
        "52wk range 45.00-58.20 Open 52.10 Prev Close 52.73 Beta 1.15 EPS 2.84 "
    ) * 5
    assert _mod._looks_garbled(numeric_dump) is True


def test_looks_garbled_does_not_flag_real_article_text():
    assert _mod._looks_garbled("This is the full article text. " * 50) is False


def test_mark_extraction_failed_if_thin_flags_only_genuinely_empty_snippet():
    """Emptiness-only, not a length heuristic: a short-but-real snippet (this codebase's own
    fixtures routinely use a few-word placeholder like "context"/"new") is left untouched and
    degrades gracefully exactly as before — only a snippet with NOTHING at all is flagged."""
    empty_item = {"snippet": ""}
    _mod._mark_extraction_failed_if_thin(empty_item)
    assert empty_item.get("extraction_failed") is True

    whitespace_only_item = {"snippet": "   "}
    _mod._mark_extraction_failed_if_thin(whitespace_only_item)
    assert whitespace_only_item.get("extraction_failed") is True

    short_but_real_item = {"snippet": "new"}
    _mod._mark_extraction_failed_if_thin(short_but_real_item)
    assert "extraction_failed" not in short_but_real_item


def test_drop_failed_extractions_removes_flagged_keeps_others():
    items = [
        {"url": "https://a.example.com", "extraction_failed": True},
        {"url": "https://b.example.com"},
        {"url": "https://c.example.com", "extraction_failed": False},
    ]
    kept = _mod._drop_failed_extractions(items)
    assert [i["url"] for i in kept] == ["https://b.example.com", "https://c.example.com"]


def test_garbled_fetch_flags_extraction_failed_when_original_snippet_is_empty():
    """The Yahoo Finance shape: the search result gave no content field AND the fetch came
    back as long, marker-free garbage. _fetch_news_items itself still returns the item
    (dropping is _do_refresh's job via _drop_failed_extractions) — but it never overwrites
    the snippet with the garbage, and flags it for the pre-synthesis drop."""

    class _GarbledProvider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Kestrel Dash SE Quote Page on Yahoo Finance",
                    "url": "https://finance.yahoo.com/quote/KSTL.DE",
                    "published_date": _days_ago(0),
                    "source": "Yahoo Finance",
                    "content": "",  # the search engine gave no description for this widget
                },
            ]

        async def fetch_page(self, url: str) -> str:
            return "�" * 500  # unreadable binary/mis-decoded response

    sc_instance = _make_sc_mock(["tavily"])
    provider = _GarbledProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) == 1
    assert items[0]["snippet"] == ""  # garbage never overwrote the (empty) original snippet
    assert items[0]["extraction_failed"] is True


def test_garbled_fetch_keeps_item_unflagged_when_original_snippet_is_real():
    """A garbled/blocked fetch never overwrites a substantive original snippet — and that
    substantive snippet is enough to summarize from, so the item is NOT flagged for drop."""

    class _GarbledFetchRealSnippetProvider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Some Overview Page",
                    "url": "https://example.com/page",
                    "published_date": _days_ago(0),
                    "source": "Src",
                    "content": "A real one-line description the search API supplied.",
                },
            ]

        async def fetch_page(self, url: str) -> str:
            return "�" * 500

    sc_instance = _make_sc_mock(["tavily"])
    provider = _GarbledFetchRealSnippetProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        return await _mod._fetch_news_items("test query")

    items, _, _ = asyncio.run(_run())
    assert len(items) == 1
    assert items[0]["snippet"] == "A real one-line description the search API supplied."
    assert "extraction_failed" not in items[0]


def test_failed_extraction_item_never_reaches_synthesis_prompt(inst_path, store_path, monkeypatch):
    """End-to-end regression: a brand-new ticker's first-ever refresh (no prior evidence, so
    it's forced onto the live-search fallback — exactly the confirmed live shape) returns one
    quote-page URL with nothing usable (empty search content + garbled fetch) alongside one
    genuine article. The failed item must be dropped BEFORE the facts_peers synthesis prompt
    (`_render_news_block`) is built — never narrated as "unreadable"/"no content" by the model.

    (The old second half of this test — "never reaches persisted news" — no longer applies:
    `_looks_garbled`/`_drop_failed_extractions` are deliberately NOT part of the live-query
    display path — RFC intel-signals-data-model §0.3/§8 — since that path never fetches full
    text at all; `evidence` stores whatever the raw search hit carried, same as any other row.)"""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path

    sc_instance = _make_sc_mock(["tavily"])

    class _MixedProvider:
        async def search(self, *a, **kw):
            return [
                {
                    "title": "Kestrel Dash SE Quote Page on Yahoo Finance",
                    "url": "https://finance.yahoo.com/quote/KSTL.DE",
                    "published_date": _days_ago(0),
                    "source": "Yahoo Finance",
                    "content": "",
                },
                {
                    "title": "Company posts record quarterly revenue",
                    "url": "https://example.com/real-article",
                    "published_date": _days_ago(0),
                    "source": "RealSrc",
                    "content": "Some content about earnings",
                },
            ]

        async def fetch_page(self, url: str) -> str:
            if "yahoo" in url:
                return "�" * 500
            return "Full article body about the earnings beat. " * 20

    provider = _MixedProvider()
    prompts_by_kind: dict[str, list[str]] = {"facts_peers": []}

    async def _fake_complete_model(prompt, model_cls, **kw):
        if model_cls is _mod._FactsPeersOutput:
            prompts_by_kind["facts_peers"].append(prompt)
            return _mod._FactsPeersOutput(description="Test Co is a company.", peers=[])
        raise AssertionError(f"unexpected model_cls {model_cls}")

    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    with patch(_GSP_PATH, return_value=provider):
        with patch(
            "backend.monitoring.asset_brief.complete_model", side_effect=_fake_complete_model
        ):
            with patch("backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u):
                with patch(
                    "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
                ):
                    with patch(_SC_PATH, return_value=sc_instance):
                        with patch(_BP_PATH, return_value=provider):
                            result = asyncio.run(_mod.refresh_asset_brief("inst-1"))

    assert result["refreshed"] is True

    # Never reached the facts_peers synthesis prompt (_render_news_block).
    assert prompts_by_kind["facts_peers"], "facts_peers call was never made"
    for prompt in prompts_by_kind["facts_peers"]:
        assert "finance.yahoo.com" not in prompt
        assert "Quote Page on Yahoo Finance" not in prompt


# ── Enhancement B: Perplexity Sonar answer passthrough ───────────────────────


def test_perplexity_special_case_captures_analyst_context():
    """Enhancement B: when perplexity is enabled, search_and_answer is called (duck-typed)
    and the Sonar answer lands in analyst_context (returned as 3rd tuple element)."""

    class _FakePerplexityProvider:
        """Has search_and_answer → triggers the Perplexity branch."""

        async def search_and_answer(self, query: str, max_results: int = 8):
            return (
                [
                    {
                        "title": "Sonar result",
                        "url": "https://sonar.example.com/1",
                        "published_date": _days_ago(6),
                        "content": "sonar snippet",
                        "source": "Sonar",
                    }
                ],
                "This is the Sonar synthesized answer.",
            )

        async def fetch_page(self, url: str) -> str:
            return ""

    sc_instance = _make_sc_mock(["perplexity"])
    provider = _FakePerplexityProvider()

    async def _run():
        with patch(_GSP_PATH, return_value=provider):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=provider):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        return await _mod._fetch_news_items("test query")

    items, _, analyst_context = asyncio.run(_run())
    assert len(analyst_context) == 1
    assert "Sonar synthesized answer" in analyst_context[0]


# NOTE (Problem B / ADR 2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation): the
# two "Enhancement B" tests that used to live here (Sonar's analyst_context feeding the
# key_issues synthesis prompt) tested `_synthesize_asset`, which no longer exists — key_issues
# synthesis moved entirely to `backend.monitoring.analyst`, which has no equivalent Sonar-
# forwarding path today. `_fetch_news_items` itself (tested above) still captures the Sonar
# answer; nothing in this codebase currently threads it into a prompt. A genuine, minor,
# knowingly-accepted capability loss from this consolidation — not silently dropped.


# ── PerplexityProvider.search_and_answer unit ─────────────────────────────────


def test_perplexity_search_and_answer_returns_rows_and_answer():
    """Unit: search_and_answer returns (rows, answer_text) from a mocked httpx response."""
    fake_response_data = {
        "choices": [{"message": {"content": "Sonar's synthesized answer."}}],
        "search_results": [
            {
                "url": "https://example.com/1",
                "title": "Result 1",
                "snippet": "some snippet",
                "date": "2026-01-01",
            }
        ],
    }

    async def _run():
        from backend.ai.search.perplexity_provider import PerplexityProvider

        provider = PerplexityProvider(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = fake_response_data

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            rows, answer = await provider.search_and_answer("test query", max_results=5)

        return rows, answer

    rows, answer = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/1"
    assert answer == "Sonar's synthesized answer."


def test_perplexity_search_still_works_via_search_and_answer():
    """Regression: PerplexityProvider.search() still returns rows (no behavior change)."""
    fake_response_data = {
        "choices": [{"message": {"content": "Some answer."}}],
        "search_results": [
            {
                "url": "https://example.com/2",
                "title": "Result 2",
                "snippet": "another snippet",
                "date": "2026-01-02",
            }
        ],
    }

    async def _run():
        from backend.ai.search.perplexity_provider import PerplexityProvider

        provider = PerplexityProvider(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = fake_response_data

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            return await provider.search("test query", max_results=5)

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/2"


def test_perplexity_search_falls_back_to_answer_when_no_sources():
    """Regression: when search_results is empty, search() surfaces the answer text."""
    fake_response_data = {
        "choices": [{"message": {"content": "Fallback answer text."}}],
        "search_results": [],
    }

    async def _run():
        from backend.ai.search.perplexity_provider import PerplexityProvider

        provider = PerplexityProvider(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = fake_response_data

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            return await provider.search("test query")

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["url"] == ""
    assert rows[0]["title"] == "Perplexity answer"
    assert rows[0]["content"] == "Fallback answer text."


# ── Task 1: model resolution ──────────────────────────────────────────────────


def test_brief_model_returns_empty_when_no_override(monkeypatch):
    """_brief_model() returns "" (App default) when no intel task override is set —
    the LLM seam then resolves the app's configured default model. No id is hardcoded."""
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setattr(ModelConfigStore, "task_model_override", lambda self, task: None)
    result = _mod._brief_model()
    assert result == ""


def test_brief_model_returns_override_when_set(monkeypatch):
    """_brief_model() returns the compound override string when intel task model is configured."""
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setattr(
        ModelConfigStore, "task_model_override", lambda self, task: "anthropic:claude-opus-4-6"
    )
    result = _mod._brief_model()
    assert result == "anthropic:claude-opus-4-6"


def test_brief_model_falls_back_to_empty_on_store_error(monkeypatch):
    """_brief_model() returns "" (App default) when the store raises (never crashes)."""
    from backend.db.model_config_store import ModelConfigStore

    def _raise(self, task):
        raise RuntimeError("disk error")

    monkeypatch.setattr(ModelConfigStore, "task_model_override", _raise)
    result = _mod._brief_model()
    assert result == ""


# ── Task 2: portfolio_impact ──────────────────────────────────────────────────
#
# NOTE (Problem B / ADR 2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation): the
# two tests that used to live here (`test_portfolio_impact_present_when_held` /
# `..._null_when_not_held`) exercised `_do_refresh`'s OWN LLM-authored portfolio_impact, which
# no longer exists — that field is now COMPOSED at request time (a live position lead line +
# the cached, numbers-free `portfolio_narrative` the Analyst pipeline writes) — see
# `services.asset_detail._compose_portfolio_impact` and its own tests in
# test_asset_detail_service.py.


def test_portfolio_impact_literal_null_string_coerced_to_real_none(
    inst_path, store_path, monkeypatch
):
    """Regression: a model asked to leave portfolio_impact null when unheld sometimes emits
    the LITERAL STRING "null" instead of JSON's null token. It must never persist as a
    truthy string — AssetDetailView's "Lucius's take on your position" card rendered the
    literal word "null" when this happened."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()

    synthesis_string_null = json.dumps(
        {
            "description": "Test Co is a fictional company.",
            "portfolio_impact": "null",  # the LITERAL STRING, not JSON null
            "key_issues": [
                {
                    "question": "Earnings?",
                    "bullish": {"text": "Bull.", "sources": []},
                    "bearish": {"text": "Bear.", "sources": []},
                }
            ],
            "facts": {},
            "peers": [],
        }
    )

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            return_value=_to_model(synthesis_string_null),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1"))

    assert result["refreshed"] is True

    from backend.db.asset_brief_store import AssetBriefStore

    brief = AssetBriefStore().get("inst-1")
    assert brief is not None
    assert brief.get("portfolio_impact") is None  # coerced, never the string "null"


# Task 3 (news_summaries merged onto news items by url) was removed entirely — RFC
# intel-signals-data-model §5 deletes the per-item LLM rewrite (`_land_news_summaries`/
# `_BriefNewsSummary`) that produced `headline`/`summary`; the live-query display path never
# rewrites a title (tests/test_asset_detail_live_news.py covers the replacement behavior).


# ── Task 4: cost tracking for search providers ────────────────────────────────


def test_cost_tracker_called_for_paid_search_provider():
    """cost_tracker.record is called with source='asset_brief' for a paid search provider."""
    recorded: list[dict] = []

    def _fake_record(source, model, input_tokens, output_tokens, cost_usd=None, **kwargs):
        recorded.append({"source": source, "model": model, "cost_usd": cost_usd})
        return cost_usd or 0.0

    sc_instance = _make_sc_mock(["tavily"])
    stub = _stub_provider()

    async def _run():
        with patch(_GSP_PATH, return_value=stub):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=stub):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        with patch("backend.ai.cost_tracker.record", side_effect=_fake_record):
                            return await _mod._fetch_news_items("test query")

    asyncio.run(_run())

    search_records = [r for r in recorded if r["model"] == "search:tavily"]
    assert len(search_records) >= 1
    assert search_records[0]["source"] == "asset_brief"
    assert search_records[0]["cost_usd"] == _mod._SEARCH_COST_USD["tavily"]


def test_cost_tracker_zero_for_free_provider():
    """Free providers (duckduckgo) incur cost_usd=0.0 in the cost tracker."""
    recorded: list[dict] = []

    def _fake_record(source, model, input_tokens, output_tokens, cost_usd=None, **kwargs):
        recorded.append({"source": source, "model": model, "cost_usd": cost_usd})
        return cost_usd or 0.0

    sc_instance = _make_sc_mock(["duckduckgo"])
    stub = _stub_provider()

    async def _run():
        with patch(_GSP_PATH, return_value=stub):
            with patch(_SC_PATH, return_value=sc_instance):
                with patch(_BP_PATH, return_value=stub):
                    with patch(
                        "backend.monitoring.asset_brief.assert_public_url", side_effect=lambda u: u
                    ):
                        with patch("backend.ai.cost_tracker.record", side_effect=_fake_record):
                            return await _mod._fetch_news_items("test query")

    asyncio.run(_run())

    search_records = [r for r in recorded if r["model"] == "search:duckduckgo"]
    assert len(search_records) >= 1
    assert search_records[0]["cost_usd"] == 0.0


# ── crypto peers resolve via CoinGecko, not Yahoo equity search ─────────────────


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return json.dumps(self._p).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_search_coin_prefers_ranked_match(monkeypatch):
    import backend.services.prices as _prices

    payload = {
        "coins": [
            {
                "id": "ethereum-pow",
                "symbol": "ETHW",
                "name": "EthereumPoW",
                "market_cap_rank": None,
            },
            {"id": "ethereum", "symbol": "eth", "name": "Ethereum", "market_cap_rank": 2},
        ]
    }
    monkeypatch.setattr(_prices.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload))
    _prices._coin_search_cache.clear()
    assert _prices.search_coin("ethereum") == {
        "id": "ethereum",
        "symbol": "ETH",
        "name": "Ethereum",
    }


def test_enrich_peers_crypto_uses_coingecko(monkeypatch):
    import backend.services.prices as _prices

    coins = {
        "ethereum": {"id": "ethereum", "symbol": "ETH", "name": "Ethereum"},
        "xrp": {"id": "ripple", "symbol": "XRP", "name": "XRP"},
    }
    monkeypatch.setattr(_prices, "search_coin", lambda q: coins.get((q or "").lower(), {}))
    monkeypatch.setattr(
        _prices,
        "_fetch_coingecko_by_id",
        lambda pairs: (
            {"ethereum": 3000.0, "ripple": 0.5},
            {"ethereum": 3030.0, "ripple": 0.49},
            {},
        ),
    )
    out = _mod._enrich_peers_crypto_sync([{"name": "Ethereum"}, {"name": "XRP"}, {"name": "Nope"}])
    # Unresolvable coin dropped; resolved coins carry CoinGecko symbol + price + "Crypto" venue.
    assert [p["ticker"] for p in out] == ["ETH", "XRP"]
    assert all(p["exchange"] == "Crypto" for p in out)
    assert out[0]["price"] == 3000.0
    assert out[0]["change_pct"] == round((3000.0 - 3030.0) / 3030.0 * 100, 2)


def test_enrich_peers_equity_still_uses_yahoo(monkeypatch):
    # Non-crypto asset_class must still route through the Yahoo equity resolver.
    called = {"crypto": False, "equity": False}
    monkeypatch.setattr(
        _mod, "_enrich_peers_crypto_sync", lambda p: called.update(crypto=True) or p
    )
    monkeypatch.setattr(_mod, "_enrich_peers_sync", lambda p: called.update(equity=True) or p)
    asyncio.run(_mod._enrich_peers([{"name": "DoorDash"}], "equity"))
    assert called == {"crypto": False, "equity": True}


# ── Bug 2 fix: _BriefFacts sanity-bounds an implausible employees/ipo_date value ────────────
#
# Confirmed live 2026-08-29: the Key Facts panel for a real small-cap company showed an
# obviously-hallucinated "Employees: 61,213,498" — larger than most countries' population. The
# fixtures below use a different, synthetic implausible number (never the real repro figure) to
# avoid reproducing the incident's exact scraped-data shape.


def test_brief_facts_nulls_out_implausible_employee_counts():
    assert _mod._BriefFacts(employees=92_400_501).employees is None  # absurdly large
    assert _mod._BriefFacts(employees=0).employees is None  # non-positive
    assert _mod._BriefFacts(employees=-5).employees is None  # negative
    assert _mod._BriefFacts(employees="not a number").employees is None  # non-numeric


def test_brief_facts_keeps_plausible_employee_counts():
    assert _mod._BriefFacts(employees=250).employees == 250
    assert _mod._BriefFacts(employees=None).employees is None
    # boundary: exactly at the ceiling is kept, one past it is not.
    ceiling = _mod._MAX_PLAUSIBLE_EMPLOYEES
    assert _mod._BriefFacts(employees=ceiling).employees == ceiling
    assert _mod._BriefFacts(employees=ceiling + 1).employees is None


def test_brief_facts_rejects_future_ipo_date_but_keeps_past_and_unparseable():
    assert _mod._BriefFacts(ipo_date="2099-01-01").ipo_date is None  # impossible: in the future
    assert _mod._BriefFacts(ipo_date="2010-01-01").ipo_date == "2010-01-01"  # real past date
    # Non-date strings are left for the existing none_if_null_string/caller handling, unchanged.
    assert _mod._BriefFacts(ipo_date="unknown").ipo_date == "unknown"
    assert _mod._BriefFacts(ipo_date=None).ipo_date is None


def test_facts_peers_output_nulls_implausible_employees_end_to_end():
    """The same shape `_synthesize_facts_peers` receives back from `complete_model` — a nested
    LLM-authored `facts` dict — must come out with the hallucinated employee count nulled, not
    a raw pass-through, matching what `_synthesize_facts_peers` then merges/persists."""
    from backend.monitoring.asset_brief import _FactsPeersOutput

    raw = {
        "description": "Acme Metals is a fictional small-cap rare-earth miner.",
        "facts": {
            "ceo": "Jane Doe",
            "employees": 92_400_501,
            "ipo_date": None,
            "industry": "Mining",
            "sector": "Materials",
            "exchange": "NASDAQ",
            "country": "US",
        },
        "peers": [],
    }
    out = _FactsPeersOutput.model_validate(raw)
    assert out.facts.employees is None
    assert out.facts.ceo == "Jane Doe"  # untouched fields still pass through normally


# ── facts/peers build-up: non-regression merge + cached peer resolution (cost saver) ──


def test_merge_facts_prefers_new_but_keeps_prior_on_null():
    from backend.monitoring.asset_brief import _merge_facts

    prior = {"ceo": "Jane", "sector": "Tech", "employees": 5000}
    new = {"ceo": "John", "sector": None, "industry": "Software"}
    m = _merge_facts(prior, new)
    assert m["ceo"] == "John"  # new wins when present
    assert m["sector"] == "Tech"  # new was null → prior preserved (no regression)
    assert m["employees"] == 5000  # untouched
    assert m["industry"] == "Software"  # new field added


def test_merge_peers_unions_keeps_resolution_and_counts():
    from backend.monitoring.asset_brief import _merge_peers

    prior = [{"name": "Uber Technologies", "ticker": "UBER", "resolved": True, "seen_count": 2}]
    new = [{"name": "Uber Technologies", "ticker": "llm-guess"}, {"name": "DoorDash"}]
    m = _merge_peers(prior, new)
    assert {p["name"] for p in m} == {"Uber Technologies", "DoorDash"}
    uber = next(p for p in m if p["name"] == "Uber Technologies")
    assert uber["ticker"] == "UBER" and uber["resolved"] is True  # cached resolution kept
    assert uber["seen_count"] == 3


def test_merge_peers_keeps_prior_peer_dropped_by_new_synthesis():
    from backend.monitoring.asset_brief import _merge_peers

    prior = [{"name": "Prosus", "ticker": "PRX.AS", "resolved": True, "seen_count": 1}]
    assert [p["name"] for p in _merge_peers(prior, [])] == ["Prosus"]  # not lost on a thin run


def test_enrich_peers_reuses_cached_ticker_skips_search(monkeypatch):
    from backend.monitoring import asset_brief as ab

    calls: list = []
    monkeypatch.setattr(
        "backend.services.prices.search_symbol", lambda q: calls.append(q) or {"symbol": "X"}
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {"UBER": 70.0}, "prev_prices": {"UBER": 72.0}, "exchanges": {}},
    )
    out = ab._enrich_peers_sync([{"name": "Uber Technologies", "ticker": "UBER", "resolved": True}])
    assert calls == []  # cached resolution → search_symbol NOT called (the cost saver)
    assert out[0]["ticker"] == "UBER" and out[0]["price"] == 70.0


def test_enrich_peers_resolves_new_peer_and_caches_it(monkeypatch):
    from backend.monitoring import asset_brief as ab

    calls: list = []
    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda q: calls.append(q) or {"symbol": "DASH", "exchange": "NASDAQ"},
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {
            "prices": {"DASH": 165.0},
            "prev_prices": {"DASH": 169.0},
            "exchanges": {},
        },
    )
    out = ab._enrich_peers_sync([{"name": "DoorDash"}])  # unresolved → must search
    assert calls == ["DoorDash"]
    assert out[0]["ticker"] == "DASH" and out[0]["resolved"] is True  # now cached for next refresh


# ── Evidence-first refresh: skip live search when the RSS/evidence cache is enough ──────


def _seed_fresh_evidence(instrument_id: str, count: int, hours_ago_start: int = 1) -> None:
    from backend.db.evidence_store import EvidenceStore
    from backend.services import evidence as ev

    _now = datetime.now(timezone.utc)
    rows = [
        {
            "url": f"https://example.com/rss-{i}",
            # Content-DISTINCT titles (not "RSS story {i}") — a templated title differing only
            # by digit false-positives as a cross-publisher near-duplicate once
            # `_filter_and_collapse_news`'s clustering pass runs on the assembled news list.
            "title": _DISTINCT_STORY_TITLES[i % len(_DISTINCT_STORY_TITLES)],
            "content": "context from the continuously-running RSS/evidence pipeline",
            "published_date": (_now - timedelta(hours=hours_ago_start + i)).isoformat(),
            "_query": "q",
        }
        for i in range(count)
    ]
    ev.record_evidence(
        rows, subjects=[f"asset:{instrument_id}"], kind="news", store=EvidenceStore()
    )


def test_evidence_sufficient_returns_true_above_threshold():
    now = datetime.now(timezone.utc)
    rows = [
        {"published_at": (now - timedelta(hours=h)).isoformat()}
        for h in range(_mod._EVIDENCE_SUFFICIENT_COUNT)
    ]
    assert _mod._evidence_sufficient(rows) is True


def test_evidence_sufficient_returns_false_below_threshold():
    now = datetime.now(timezone.utc)
    rows = [
        {"published_at": (now - timedelta(hours=h)).isoformat()}
        for h in range(_mod._EVIDENCE_SUFFICIENT_COUNT - 1)
    ]
    assert _mod._evidence_sufficient(rows) is False


def test_evidence_sufficient_ignores_stale_rows():
    """A cache full of OLD (but not yet TTL-expired) rows must not count as sufficient —
    only rows inside _EVIDENCE_FRESH_WINDOW_HOURS count toward the threshold."""
    now = datetime.now(timezone.utc)
    stale = [
        {"published_at": (now - timedelta(days=30)).isoformat()}
        for _ in range(_mod._EVIDENCE_SUFFICIENT_COUNT + 5)
    ]
    assert _mod._evidence_sufficient(stale) is False


def test_evidence_first_skips_live_search_when_cache_is_fresh(inst_path, store_path, monkeypatch):
    """A held/watched asset with enough recent evidence already cached (fed continuously by
    the RSS tiers, independent of this endpoint) synthesizes straight from that cache and
    never calls the live multi-provider search."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path
    _seed_fresh_evidence("inst-1", _mod._EVIDENCE_SUFFICIENT_COUNT + 1)

    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    fake_live_search = AsyncMock()
    stub = _stub_provider(fetch_page_text="x" * 500)  # long enough to pass the block-page check

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch("backend.monitoring.asset_brief._fetch_news_items", fake_live_search):
            with patch(
                "backend.monitoring.asset_brief.complete_model",
                new_callable=AsyncMock,
                return_value=_to_model(_synthesis_json()),
            ):
                with patch(
                    "backend.services.profile.read_profile",
                    return_value={"base_currency": "SGD"},
                ):
                    result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=False))

    assert result["refreshed"] is True
    assert result["source"] == "evidence_cache"
    fake_live_search.assert_not_called()

    from backend.services.evidence import read_evidence

    rows = read_evidence(subjects=["asset:inst-1"], fresh_only=True)
    assert len(rows) == _mod._EVIDENCE_SUFFICIENT_COUNT + 1
    assert all(r["url"].startswith("https://example.com/rss-") for r in rows)


def test_evidence_first_still_fetches_full_article_text(inst_path, store_path, monkeypatch):
    """The evidence-first path skips the live SEARCH call but still fetches full page text for
    the already-known URLs, so synthesis quality doesn't regress vs. a short RSS snippet.
    Observed via `_synthesize_facts_peers` (the now-removed `_synthesize_asset` was the
    original observation point — see test_snippet_absent_in_persisted_news's own note)."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path
    _seed_fresh_evidence("inst-1", _mod._EVIDENCE_SUFFICIENT_COUNT)

    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    synthesis_items_seen: list[list[dict]] = []

    async def _fake_synthesize(asset_row, news_items, base):
        synthesis_items_seen.append(list(news_items))
        return json.loads(_synthesis_json())

    full_text = "FULL ARTICLE TEXT " * 50  # well past _MIN_ARTICLE_CHARS
    stub = _stub_provider(fetch_page_text=full_text)

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch("backend.monitoring.asset_brief._fetch_news_items", AsyncMock()):
            with patch(
                "backend.monitoring.asset_brief._synthesize_facts_peers",
                side_effect=_fake_synthesize,
            ):
                with patch(
                    "backend.services.profile.read_profile",
                    return_value={"base_currency": "SGD"},
                ):
                    result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=False))

    assert result["refreshed"] is True
    assert len(synthesis_items_seen) == 1
    assert all(full_text.strip() in i["snippet"] for i in synthesis_items_seen[0])


def test_evidence_thin_falls_back_to_live_search(inst_path, store_path, monkeypatch):
    """Below the sufficiency threshold (or no evidence at all), the refresh falls back to the
    existing live multi-provider search path — unaffected by the evidence-first change."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path
    _seed_fresh_evidence("inst-1", _mod._EVIDENCE_SUFFICIENT_COUNT - 1)  # below threshold

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            return_value=_to_model(_synthesis_json()),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=False))

    assert result["refreshed"] is True
    assert result["source"] == "live_search"


def test_force_refresh_goes_live_even_with_abundant_fresh_evidence(
    inst_path, store_path, monkeypatch
):
    """force=True (the explicit Refresh tap) always goes live, even when the evidence cache
    alone would have been sufficient — an explicit ask for the latest must not be satisfied by
    a possibly-stale cache."""
    InstrumentStore().bulk_insert([_make_inst()])
    from backend.db import asset_brief_store as _abs

    _abs.ASSET_BRIEF_JSON = store_path
    _seed_fresh_evidence("inst-1", _mod._EVIDENCE_SUFFICIENT_COUNT + 3)

    sc_instance = _make_sc_mock(["perplexity"])
    stub = _stub_provider()
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda q: {})
    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {"prices": {}, "prev_prices": {}, "exchanges": {}},
    )

    with patch("backend.monitoring.asset_brief.get_search_provider", return_value=stub):
        with patch(
            "backend.monitoring.asset_brief.complete_model",
            new_callable=AsyncMock,
            return_value=_to_model(_synthesis_json()),
        ):
            with patch(
                "backend.services.profile.read_profile", return_value={"base_currency": "SGD"}
            ):
                with patch(_SC_PATH, return_value=sc_instance):
                    with patch(_BP_PATH, return_value=stub):
                        result = asyncio.run(_mod.refresh_asset_brief("inst-1", force=True))

    assert result["refreshed"] is True
    assert result["source"] == "live_search"


# ── apply_analyst_synthesis (Problem B point 1, ADR 2026-08-28-cross-subject-fan-out-and-
# asset-brief-consolidation): the ONE writer of key_issues/portfolio_narrative going forward —
# called by backend.monitoring.analyst._process_row for a held asset subject, from the SAME
# synthesis call that already updated that instrument's Intel signal. ─────────────────────────


def test_apply_analyst_synthesis_writes_key_issues_and_narrative(db_isolated):
    from backend.db.asset_brief_store import AssetBriefStore

    _mod.apply_analyst_synthesis(
        "inst-1",
        [
            {
                "question": "Will margins hold?",
                "bullish": "Pricing power intact.",
                "bearish": "Input costs rising.",
            }
        ],
        "This matters because of your concentrated position here.",
        [{"url": "https://example.com/a", "title": "A", "domain": "example.com"}],
        "2026-08-28T00:00:00+00:00",
    )

    brief = AssetBriefStore().get("inst-1")
    assert brief is not None
    assert brief["instrument_id"] == "inst-1"
    assert len(brief["key_issues"]) == 1
    issue = brief["key_issues"][0]
    assert issue["question"] == "Will margins hold?"
    assert issue["bullish"]["text"] == "Pricing power intact."
    assert issue["bearish"]["text"] == "Input costs rising."
    assert (
        brief["portfolio_narrative"] == "This matters because of your concentrated position here."
    )
    assert brief["key_issues_synthesized_at"] == "2026-08-28T00:00:00+00:00"


def test_apply_analyst_synthesis_attaches_same_sources_to_both_sides(db_isolated):
    from backend.db.asset_brief_store import AssetBriefStore

    sources = [
        {"url": "https://example.com/a", "title": "A", "domain": "example.com"},
        {"url": "https://example.com/b", "title": "B", "domain": "example.com"},
    ]
    _mod.apply_analyst_synthesis(
        "inst-1",
        [{"question": "Q?", "bullish": "bull", "bearish": "bear"}],
        "narrative",
        sources,
        "2026-08-28T00:00:00+00:00",
    )

    issue = AssetBriefStore().get("inst-1")["key_issues"][0]
    assert issue["bullish"]["sources"] == sources
    assert issue["bearish"]["sources"] == sources


def test_apply_analyst_synthesis_merges_non_destructively_with_prior_news_and_facts(db_isolated):
    """Writing key_issues must never blank out news/facts/peers/description asset_brief.py's
    own refresh cadence already populated — a non-destructive merge, same as every other write
    in this module."""
    from backend.db.asset_brief_store import AssetBriefStore

    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-1",
            "news": [{"title": "Existing news", "url": "https://example.com/n", "source": "s"}],
            "key_issues": [],
            "facts": {"ceo": "Jane Doe"},
            "peers": [{"name": "Peer Co", "ticker": "PEER"}],
            "description": "An existing description.",
            "synthesized_at": "2026-08-27T00:00:00+00:00",
            "search_provider": "tavily",
        }
    )

    _mod.apply_analyst_synthesis(
        "inst-1",
        [{"question": "Q?", "bullish": "bull", "bearish": "bear"}],
        "narrative",
        [],
        "2026-08-28T00:00:00+00:00",
    )

    brief = AssetBriefStore().get("inst-1")
    assert brief["news"] == [
        {"title": "Existing news", "url": "https://example.com/n", "source": "s"}
    ]
    assert brief["facts"] == {"ceo": "Jane Doe"}
    assert brief["peers"] == [{"name": "Peer Co", "ticker": "PEER"}]
    assert brief["description"] == "An existing description."
    assert len(brief["key_issues"]) == 1  # the new write DID land


def test_apply_analyst_synthesis_empty_key_issues_is_a_no_op(db_isolated):
    """A genuinely-empty synthesis is not a 'delete the prior brief's take' signal — matches
    _land_analysis's own long-standing non-regression stance for this exact field."""
    from backend.db.asset_brief_store import AssetBriefStore

    _mod.apply_analyst_synthesis("inst-new", [], "narrative", [], "2026-08-28T00:00:00+00:00")

    assert AssetBriefStore().get("inst-new") is None  # never even created a row


def test_apply_analyst_synthesis_sanitizes_unsafe_source_urls(db_isolated):
    """Defense-in-depth: reuses _sanitize_key_issues_sources, so a non-http(s) URL slipping in
    (should never happen — sources are server-built from real evidence, never model-chosen) is
    still stripped, same as every other key_issues write in this module."""
    from backend.db.asset_brief_store import AssetBriefStore

    unsafe_sources = [
        {"url": "javascript:alert(1)", "title": "bad"},
        {"url": "https://example.com/good", "title": "good"},
    ]
    _mod.apply_analyst_synthesis(
        "inst-1",
        [{"question": "Q?", "bullish": "bull", "bearish": "bear"}],
        "narrative",
        unsafe_sources,
        "2026-08-28T00:00:00+00:00",
    )

    issue = AssetBriefStore().get("inst-1")["key_issues"][0]
    urls = {s["url"] for s in issue["bullish"]["sources"]}
    assert urls == {"https://example.com/good"}
