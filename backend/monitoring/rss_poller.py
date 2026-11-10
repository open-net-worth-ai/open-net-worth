"""RSS poller — Phase 3a: free, fast, tiered feed collection + news-volume-spike trigger.

Runs independently of the heavy 12h intel scan. Two tiers:

  fast (every 5 min): top _TIER_A_CAP held assets by base value → resolve feeds → fetch with a
    short cache TTL so the tight poll actually refetches → spike detection per asset. ALSO
    (INT7) up to _TOPIC_FAST_CAP monitor_news watchlist topics → their own search feeds at the
    SAME fast cache TTL + the same spike detection — closing a measured incident gap where a
    watched topic's feeds were slow-tier-only (~30 min cadence) even though held assets already
    polled every 5 min; up to 25 min of that lag was our own poll cadence, not source indexing.

  slow (every 30 min): remaining held assets + EVERY watchlist topic + non-held watchlist
    tickers → fetch with the default cache TTL (cache hygiene) — PLUS spike detection for every
    topic with monitor_news truthy (INT3). Topics are STILL fetched here too even after INT7 —
    harmless (the cache TTL dedupes, and the spike cooldown in PendingAnalysisStore.has_recent
    prevents a double-enqueue across tiers for the same subject within the cooldown window).

No LLM, no web search. One bad asset/feed/topic never sinks the poll (per-item try/except).
Spike detection enqueues a PendingAnalysisRow (trigger="news_spike") so the Phase 2 Analyst
wakes on NEWS as well as price crashes.

Topic feed processing (resolve + fetch + spike-check) is shared verbatim by both tiers via
rss_poller_topics._process_topic (split into its own module to keep this file under the
file-size convention — mirrors the rss_poller_hot.py precedent).

Spike trigger design (PUBLISH-BASED, BASELINE-RELATIVE; shared math lives in
spike_detection.py — subject-agnostic, so the fast/asset and slow/topic paths use IDENTICAL
logic on whatever evidence rows they hand it):
  A spike fires when the recent-window published count is BOTH:
    (a) >= _SPIKE_MIN_ABS (absolute floor, prevents noise on tiny feeds), AND
    (b) >= _SPIKE_FACTOR × the subject's own trailing baseline (mean articles per same-size
        window over the trailing _SPIKE_BASELINE_DAYS days from cached evidence).
  Cold-start guard: if the evidence cache covers < _SPIKE_MIN_HISTORY_DAYS days of data for a
  subject, no spike fires (insufficient baseline).
  Per-tier cap: at most _SPIKE_MAX_PER_POLL enqueues per tier per poll tick — fast (assets) and
  slow (topics) each track their OWN counter, so a busy news day for topics can never crowd out
  an asset spike or vice versa.
  Window size: _SPIKE_WINDOW_SEC (90 min) is reused UNCHANGED for the topic path even though the
  slow tier only runs every ~30 min — 90 min still comfortably spans 2-3 slow ticks, so a spike
  that starts between polls is never missed, and the baseline math is window-size-agnostic (it
  measures rate, not a fixed count) so topics need no separate tuning from assets.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from backend.core.fx import base_currency, to_base
from backend.db.pending_analysis_store import PendingAnalysisStore
from backend.monitoring.collect import gather_rss
from backend.monitoring.rss_poller_topics import (
    _augment_with_linked_topics,
    _process_topic,
    _run_fast_tier_topics,
)
from backend.monitoring.spike_detection import (  # noqa: F401 — re-exported for tests
    _SPIKE_BASELINE_DAYS,
    _SPIKE_COOLDOWN_HOURS,
    _SPIKE_FACTOR,
    _SPIKE_MAX_PER_POLL,
    _SPIKE_MIN_ABS,
    _SPIKE_MIN_HISTORY_DAYS,
    _SPIKE_WINDOW_SEC,
    _is_spike,
    _is_spike_any_clock,
)
from backend.services.evidence import read_evidence
from backend.services.feeds import resolve_holding_feeds
from backend.services.fx_cache import load_fx_cache
from backend.services.positions import hydrated as read_rows
from backend.services.watchlist import is_topic, read_watchlist

logger = logging.getLogger(__name__)

_TIER_A_CAP = 25
_FAST_CACHE_TTL = 240.0
_SLOW_CACHE_TTL = 900.0
_TOPIC_FAST_CAP = 10  # cost guard (INT7): at most this many monitor_news topics get the fast
# cadence per tick — a user with more just has the excess wait for the slow tier (harmless,
# same cache-TTL-dedupe/cooldown safety net as any topic already has there); logged once, not
# per-topic, when the cap actually trims the list.


def _holding_value(row: dict, base: str, rates: dict) -> float:
    qty = float(row.get("quantity") or 0)
    price = float(row.get("price") or 0)
    qpu = float(row.get("quote_per_unit") or 1.0)
    ccy = (row.get("quote_currency") or row.get("currency") or base).upper()
    return to_base(qty * price * qpu, ccy, base, rates=rates)


def _rank_holdings(rows: list[dict]) -> list[dict]:
    """Return holdings that have a ticker and instrument_id, sorted by base value descending."""
    base = base_currency()
    rates = load_fx_cache()
    eligible = [
        r
        for r in rows
        if (r.get("ticker") or "").strip() and (r.get("instrument_id") or "").strip()
    ]
    return sorted(eligible, key=lambda r: _holding_value(r, base, rates), reverse=True)


def _held_instrument_ids(rows: list[dict]) -> set[str]:
    return {r["instrument_id"] for r in rows if (r.get("instrument_id") or "").strip()}


def _ticker_asset_map(items: list[dict]) -> dict[str, str]:
    """Upper-ticker -> instrument_id for every watchlist ``ticker`` item that carries one
    (2026-07-16 nit follow-up) — read once per tick, mirroring `topics`, so
    rss_poller_topics._augment_topic_feeds_with_linked_asset_subjects can merge a topic's
    linked ticker's OWN asset subject onto its feed at resolution time (the topic side never
    otherwise sees the rest of the watchlist)."""
    return {
        (i.get("value") or "").strip().upper(): (i.get("instrument_id") or "").strip()
        for i in items
        if i.get("type") == "ticker"
        and (i.get("value") or "").strip()
        and (i.get("instrument_id") or "").strip()
    }


def _spike_enqueue(row: dict, new_count: int, pa_store: PendingAnalysisStore) -> bool:
    """Try to enqueue a news_spike row for one holding. Returns True if enqueued."""
    iid = (row.get("instrument_id") or "").strip()
    ticker = (row.get("ticker") or "").strip().upper()
    if not iid or not ticker:
        return False

    subject = f"asset:{iid}"
    signal_id = f"news-{iid}"
    since_iso = (datetime.now(timezone.utc) - timedelta(hours=_SPIKE_COOLDOWN_HOURS)).isoformat()

    if pa_store.has_recent(subject, "news_spike", since_iso):
        return False

    # INT14b: coalesces into an existing pending row for this subject (e.g. a still-open
    # material_news row) instead of always spawning a separate one.
    pa_store.upsert_pending(
        subject=subject,
        ticker=ticker,
        signal_id=signal_id,
        trigger="news_spike",
        payload={
            "label": row.get("label") or ticker,
            "asset_class": row.get("asset_class") or "equity",
            "new_count": new_count,
        },
    )
    logger.info("rss_poller: news_spike enqueued for %s (%d recently published)", ticker, new_count)
    return True


async def _process_holding_fast(
    row: dict,
    pa_store: PendingAnalysisStore,
    enqueue_count: list[int],
    spike_min_abs: int = _SPIKE_MIN_ABS,
    spike_factor: float = _SPIKE_FACTOR,
    topics: list[dict] | None = None,
    fetched_urls: set[str] | None = None,
) -> None:
    """Fetch RSS for one holding (fast tier) and run spike detection.

    `enqueue_count` is a single-element mutable list used as a counter across calls so the
    per-poll cap (_SPIKE_MAX_PER_POLL) can be checked without passing state through return values.

    `fetched_urls`, when given, records every URL resolved for this holding (INT7) so the fast
    tier's topic pass (rss_poller_topics._process_topic) can skip a linked-ticker feed this
    holding already covers this tick — see rss_poller_topics' module docstring.
    """
    iid = (row.get("instrument_id") or "").strip()
    feeds = resolve_holding_feeds(row)
    _augment_with_linked_topics(
        feeds, row.get("price_source_id") or row.get("ticker") or "", topics or []
    )
    if fetched_urls is not None:
        fetched_urls.update(f["url"] for f in feeds if f.get("url"))
    if feeds:
        await gather_rss(feeds, max_age=_FAST_CACHE_TTL, read_back=False)

    if not iid:
        return

    if enqueue_count[0] >= _SPIKE_MAX_PER_POLL:
        return

    # Off the event loop — this parses the whole evidence file (55 MB on a real book) and was
    # stalling in-flight HTTP requests once per holding per tick (2026-07-06 Plan-tab stall fix).
    evidence_rows = await asyncio.to_thread(
        read_evidence, subjects=[f"asset:{iid}"], fresh_only=True
    )
    # OR-combined publish+arrival clock (INT5) — see spike_detection._is_spike_any_clock.
    spike, recent_count = _is_spike_any_clock(evidence_rows, spike_min_abs, spike_factor)
    if spike:
        if _spike_enqueue(row, recent_count, pa_store):
            enqueue_count[0] += 1
            if enqueue_count[0] >= _SPIKE_MAX_PER_POLL:
                logger.info(
                    "rss_poller: per-poll spike cap (%d) reached — further asset spikes "
                    "suppressed this tick",
                    _SPIKE_MAX_PER_POLL,
                )


async def _process_holding_slow(row: dict, topics: list[dict] | None = None) -> None:
    """Fetch RSS for one holding (slow tier, no spike detection)."""
    feeds = resolve_holding_feeds(row)
    _augment_with_linked_topics(
        feeds, row.get("price_source_id") or row.get("ticker") or "", topics or []
    )
    if feeds:
        await gather_rss(feeds, max_age=_SLOW_CACHE_TTL, read_back=False)


async def run_rss_poll(tier: str) -> int:
    """Run one RSS poll tick for the given tier ('fast', 'slow', or 'hot').

    Returns the number of assets whose feeds were fetched. Best-effort: an exception on
    any single asset is caught and logged (never propagated) so one bad asset/feed/resolver
    never aborts the whole poll. Reads spike_min_abs and spike_factor from watchlist config
    (falling back to module-level constants) so user-configured thresholds are respected."""
    if tier == "hot":
        # Split into its own module (news-ingestion-latency.md §2) to keep this file under the
        # file-size convention — the regulatory registry, GDELT combined query, and custom-feed
        # fan-out share nothing with the fast/slow spike-detection machinery below.
        from backend.monitoring.rss_poller_hot import run_hot_tick

        return await run_hot_tick()

    wl_all = read_watchlist()
    wl_cfg = wl_all.get("config") or {}
    spike_min_abs: int = int(wl_cfg.get("spike_min_abs") or _SPIKE_MIN_ABS)
    spike_factor: float = float(wl_cfg.get("spike_factor") or _SPIKE_FACTOR)
    # Read once per tick — SF-2 cross-subject fix needs every held/watchlist ticker's feed
    # resolution to know which topics link to it (see _augment_with_linked_topics).
    wl_items = wl_all.get("items") or []
    topics = [i for i in wl_items if is_topic(i)]
    # Read once per tick — the mirror direction (2026-07-16 nit follow-up): a topic's OWN
    # linked-ticker feed needs to know which of those tickers resolve to a real instrument via
    # some OTHER watchlist ticker item (see _augment_topic_feeds_with_linked_asset_subjects).
    ticker_assets = _ticker_asset_map(wl_items)

    pa_store = PendingAnalysisStore()
    all_rows = read_rows()
    ranked = _rank_holdings(all_rows)

    if tier == "fast":
        tier_a = ranked[:_TIER_A_CAP]
        enqueue_count = [0]
        fetched_urls: set[str] = set()
        count = 0
        for row in tier_a:
            try:
                await _process_holding_fast(
                    row, pa_store, enqueue_count, spike_min_abs, spike_factor, topics, fetched_urls
                )
                count += 1
            except Exception as exc:
                logger.warning(
                    "rss_poller: fast tier asset %s failed: %s",
                    row.get("ticker", "?"),
                    type(exc).__name__,
                )

        # INT7: monitor_news topics ALSO get the fast cadence (capped — see _run_fast_tier_topics);
        # the rest still get the slow tier, unchanged.
        count += await _run_fast_tier_topics(
            topics,
            pa_store,
            fetched_urls,
            spike_min_abs,
            spike_factor,
            cache_ttl=_FAST_CACHE_TTL,
            topic_cap=_TOPIC_FAST_CAP,
            ticker_assets=ticker_assets,
        )
        return count

    if tier == "slow":
        held_ids = _held_instrument_ids(all_rows)

        count = 0

        # Remaining held assets (beyond the fast-tier cap)
        for row in ranked[_TIER_A_CAP:]:
            try:
                await _process_holding_slow(row, topics)
                count += 1
            except Exception as exc:
                logger.warning(
                    "rss_poller: slow tier holding %s failed: %s",
                    row.get("ticker", "?"),
                    type(exc).__name__,
                )

        # Watchlist items not already held (non-held tickers + topics)
        topic_enqueue_count = [0]

        for item in wl_items:
            try:
                item_type = item.get("type") or ""
                if is_topic(item):
                    await _process_topic(
                        item,
                        pa_store,
                        topic_enqueue_count,
                        spike_min_abs,
                        spike_factor,
                        cache_ttl=_SLOW_CACHE_TTL,
                        ticker_assets=ticker_assets,
                    )
                    count += 1
                elif item_type == "ticker":
                    iid = (item.get("instrument_id") or "").strip()
                    if iid and iid in held_ids:
                        continue
                    feeds = resolve_holding_feeds(item)
                    _augment_with_linked_topics(feeds, item.get("value") or "", topics)
                    if feeds:
                        await gather_rss(feeds, max_age=_SLOW_CACHE_TTL, read_back=False)
                    count += 1
            except Exception as exc:
                logger.warning(
                    "rss_poller: slow tier watchlist item %s failed: %s",
                    item.get("value", "?"),
                    type(exc).__name__,
                )

        return count

    logger.warning("rss_poller: unknown tier %r", tier)
    return 0
