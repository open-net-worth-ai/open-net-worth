"""
AssetBriefStore — per-instrument synthesized brief cache.

Stores the LLM-synthesized analysis for one instrument (key_issues, facts, peers,
description). This is a derived cache (checkpoint-EXCLUDED, like price_history/evidence),
rebuilt by POST /api/intel/assets/{instrument_id}/refresh.

The "Stories & Analysis"/"Latest Developments" news feed is NOT stored here — it is a live
query over the shared `evidence` table (`services.asset_detail._live_news_for_instrument`,
RFC intel-signals-data-model §5), read at request time with no independent fetch, cache, or
per-item LLM rewrite. This store used to carry `news`/`news_checked_at` fields for that
purpose (an independently-fetched, independently-cached, LLM-rewritten copy of the same
`evidence` data); both were removed by migration `202608301645_drop_asset_brief_news_fields`.

Brief schema:
    instrument_id  : str       FK → instruments.json
    key_issues     : list[dict]  [{question, bullish:{text,sources[]}, bearish:{text,sources[]}}]
    facts          : dict        {ceo?, employees?, ipo_date?, industry?, exchange?, country?}
    peers          : list[dict]  [{name, ticker?, exchange?}]
    description    : str|None    <=3 sentences, grounded in search results
    synthesized_at : ISO         when facts/peers/description last substantively changed
                                  (bumped by asset_brief._do_refresh — see _content_hash)
    search_provider: str         name of the search provider used
    portfolio_narrative    : str|None  REASONING ONLY, no baked-in numbers (e.g. "this matters
                                  because of your concentrated position here") — written by
                                  backend.monitoring.analyst's own synthesis for a HELD asset
                                  subject via apply_analyst_synthesis, never by _do_refresh.
                                  Composed with a live-computed lead line (weight %, P&L %) into
                                  the API's `portfolio_impact` field at request time — see
                                  services.asset_detail._compose_portfolio_impact.
    position_verdict : dict|None  the take's action line from the same synthesis —
                                  {action, why, held, price_at, at, stop?/trim?/entry?}; shaped by
                                  services.position_verdict (normalize on write, compose on read,
                                  prior_block for the next take's prompt).
    key_issues_synthesized_at : ISO|None  when key_issues/portfolio_narrative last landed, OR
                                  the last time a COMPLETED synthesis pass checked and found
                                  nothing to show (H1 fix, security-review 2026-08-29 —
                                  `asset_brief.stamp_key_issues_checked`, called for a
                                  crypto/commodity instrument that can never get real key_issues
                                  at all) — a SEPARATE clock from `synthesized_at` above
                                  (Problem B point 2's steady-cadence precompute check reads
                                  THIS field, via analyst._key_issues_stale, not the
                                  news/facts/peers one). The clock means "we checked", not
                                  "we found something to show".
    portfolio_impact : str|None  LEGACY — the pre-2026-08-28 LLM-authored field (numbers baked
                                  in). No longer written by anything; a brief persisted before
                                  the ADR 2026-08-28-cross-subject-fan-out-and-asset-brief-
                                  consolidation migration may still carry a stale copy, which the
                                  API now ignores entirely in favor of the composed field above.
    technicals_bundle_hash : str|None  fingerprint of the Analysis-tab Technicals widget's six
                                  indicator scores (rounded) at the time `technicals_narrative`
                                  below was written — see services.asset_technicals._bundle_hash.
                                  A later read whose freshly-computed hash still matches this one
                                  reuses the cached paragraph instead of calling the LLM again;
                                  a mismatch (or no row yet) means the tape moved and it's stale.
    technicals_narrative : str|None  the Technicals widget's own short AI paragraph — narrates
                                  ONLY the six mechanical rows, never a combined Buy/Sell (see
                                  services.asset_technicals._synthesize's prompt). Written by
                                  services.asset_technicals.ensure_technicals_narrative, keyed to
                                  `technicals_bundle_hash` above — unlike key_issues/
                                  portfolio_narrative, there is no separate "last checked" clock:
                                  validity is entirely hash-gated, not time-gated.
    fundamentals_bundle_hash : str|None  fingerprint of the Analysis-tab Fundamentals widget's
                                  valuation+growth rows (ticker + sector low/mean/high, rounded)
                                  at the time `fundamentals_narrative` below was written — see
                                  services.asset_fundamentals._bundle_hash. Same hash-gated
                                  reuse rule as technicals_bundle_hash.
    fundamentals_narrative : str|None  the Fundamentals widget's own short AI paragraph — narrates
                                  ONLY the valuation/growth rows vs sector peers, never a Buy/Sell
                                  (see services.asset_fundamentals._synthesize). Written by
                                  services.asset_fundamentals.ensure_fundamentals_narrative.

PLAN 4B TASK 3: DB-backed (Tier-2 cache — table ``asset_brief``, backend/db/tables.py). A
plain ``PayloadTableStore`` over the generic (seq, id, payload) shape, keyed by
``instrument_id`` (mapped onto the table's generic ``id`` column via ``id_field="instrument_id"``) — the
same one-liner-subclass pattern ``InstrumentStore``/``EvidenceStore`` established (Tier-2,
NO ``book_id``: this is world/derived data, not a book-scoped money fact). Constructed with
the default ``checkpointed=True`` (the write-hook fires unconditionally, mirroring
``evidence``) — ``"asset_brief"`` is registered in ``ai.checkpoint._EXCLUDED_TABLES``, which
is what actually makes the hook a structural no-op: a ``/rewind`` must never resurrect or
revert a derived LLM/search cache, exactly like ``evidence``/``price_points``. Replaces the
file-level ``ASSET_BRIEF_JSON`` exclusion in ``ai.checkpoint._EXCLUDED_PATHS`` (kept there
for the legacy file).
"""

from __future__ import annotations

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import asset_brief


class AssetBriefStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(asset_brief, id_field="instrument_id", engine=engine)

    def get(self, instrument_id: str) -> dict | None:
        return self.find_by_id(instrument_id)

    def upsert_brief(self, brief: dict) -> dict:
        return self.upsert(brief)
