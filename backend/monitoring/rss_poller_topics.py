"""RSS poller — watchlist-topic feed processing, shared by the fast and slow tiers (INT7).

Split out of rss_poller.py to keep that module under the file-size convention (mirrors the
rss_poller_hot.py precedent: a self-contained concern gets its own module rather than pushing
the orchestrator over the ~400-line cap).

``_process_topic`` is the ONE topic-feed-plus-spike-detection routine both tiers call:
  * slow tier (every topic, cache hygiene at ``_SLOW_CACHE_TTL``) — unchanged since INT3.
  * fast tier (``monitor_news`` topics only, at ``_FAST_CACHE_TTL`` — INT7): a watched topic's
    own search feeds were previously slow-tier-only (~30 min cadence), adding up to ~25 min of
    avoidable lag on top of source-indexing time (measured incident: a story published 01:31
    landed in our cache at 01:56).
Sharing one function means the two paths can never drift apart the way two hand-maintained
copies eventually would.

``fetched_urls``, when supplied by the fast tier, lets a topic skip a linked-ticker feed a
fast-tier HOLDING already fetched THIS TICK — rss_poller._process_holding_fast's own
``_augment_with_linked_topics`` call already stamped the topic subject onto that row, so
refetching the identical URL from the topic side a moment later would just be a redundant
EvidenceStore read-modify-write for no new evidence. The slow tier never passes this (topics
and holdings never share a loop there), so it always fetches its own topics unfiltered.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from backend.db.pending_analysis_store import PendingAnalysisStore
from backend.monitoring.collect import gather_rss
from backend.monitoring.spike_detection import (
    _SPIKE_COOLDOWN_HOURS,
    _SPIKE_FACTOR,
    _SPIKE_MAX_PER_POLL,
    _SPIKE_MIN_ABS,
    _is_spike_any_clock,
)
from backend.services.evidence import read_evidence
from backend.services.feeds import resolve_topic_feeds

logger = logging.getLogger(__name__)


def _linked_topic_subjects_for_ticker(ticker: str, topics: list[dict]) -> list[str]:
    """``topic:{id}`` subjects for every topic whose ``linked_tickers`` includes `ticker`
    (case-insensitive) — SF-2 fix (news-ingestion-latency.md §1's linked_tickers feature): a
    ticker that is BOTH held AND a topic's linked ticker resolves to the SAME feed URL from
    two different call sites (this fn's caller, tagged asset:{iid}, vs. resolve_topic_feeds,
    tagged topic:{id}) — whichever races to record it FIRST stamps seen_count=1 and gets
    emitted as a "new arrival" with only ITS OWN subject; the second (later) recording of the
    identical row only bumps seen_count to 2 (never "new" again), so the topic subject would
    otherwise NEVER get an arrival event and news_screen could permanently miss the story
    under the topic. Fix: merge the topic subject onto the SAME feed dict at resolution time,
    so the very first insert — whichever tier wins the race — already carries both subjects."""
    t = (ticker or "").strip().upper()
    if not t:
        return []
    out = []
    for topic in topics or []:
        tid = (topic.get("id") or "").strip()
        if not tid:
            continue
        linked = {(lt or "").strip().upper() for lt in topic.get("linked_tickers") or []}
        if t in linked:
            out.append(f"topic:{tid}")
    return out


def _augment_with_linked_topics(feeds: list[dict], ticker: str, topics: list[dict]) -> None:
    """Mutates `feeds` in place — appends any linked-topic subjects (deduped, order-preserving)
    onto EVERY feed dict resolved for `ticker`. Cheap (a ticker resolves to 1-3 feeds; `topics`
    is the small watchlist topic list, read once per poll tick)."""
    extra = _linked_topic_subjects_for_ticker(ticker, topics)
    if not extra:
        return
    for f in feeds:
        merged = list(f.get("subjects") or [])
        for s in extra:
            if s not in merged:
                merged.append(s)
        f["subjects"] = merged


def _augment_topic_feeds_with_linked_asset_subjects(
    feeds: list[dict], item: dict, ticker_assets: dict[str, str]
) -> None:
    """Mutates `feeds` (as resolved by ``resolve_topic_feeds``) in place — the MIRROR of
    ``_augment_with_linked_topics``: appends ``asset:{iid}`` onto EVERY feed resolved for this
    topic, for each of its ``linked_tickers`` that resolves to a real instrument via
    `ticker_assets` (an upper-ticker -> instrument_id map built once per poll tick from every
    OTHER watchlist ticker item that carries one — news-ingestion-latency.md nit, 2026-07-16
    re-verify follow-up).

    SF-2's original fix only merged in this direction for a HELD ticker (whose own
    resolve_holding_feeds call carries asset:{iid}) racing the topic's linked-ticker feed — the
    fast tier's fetched_urls skip and the slow tier's holding-loop-runs-first ordering cover
    that case. A NON-HELD ticker that's ALSO independently a watchlist ticker item (e.g. a
    formerly-held, now-sold position kept on a watchlist, linked from a topic) was still exposed:
    resolve_topic_feeds's own linked-ticker feed carried ONLY topic:{tid}, so whichever item's
    feed's story got recorded FIRST this tick "won" the arrival — cross-feed article-level dedup
    (record_evidence merges by canonical URL; the news_arrivals emission fires ONLY on that very
    first insert) meant the asset subject could be silently dropped from the story's arrival for
    good whenever the topic's own feed happened to process first. Stamping it here — at
    resolution time, onto every feed sharing the topic's own subjects set, exactly as
    _augment_with_linked_topics does for the ticker side — closes the gap regardless of
    iteration order."""
    extra = [
        f"asset:{iid}"
        for t in item.get("linked_tickers") or []
        if (iid := (ticker_assets or {}).get((t or "").strip().upper()))
    ]
    if not extra:
        return
    for f in feeds:
        merged = list(f.get("subjects") or [])
        for s in extra:
            if s not in merged:
                merged.append(s)
        f["subjects"] = merged


def _spike_enqueue_topic(item: dict, new_count: int, pa_store: PendingAnalysisStore) -> bool:
    """Try to enqueue a news_spike row for one watchlist topic. Returns True if enqueued.

    Mirrors rss_poller._spike_enqueue's shape (subject/signal_id convention, cooldown gate)
    with a topic-appropriate payload — no ticker/instrument_id, since a topic isn't a holding."""
    item_id = (item.get("id") or "").strip()
    if not item_id:
        return False

    subject = f"topic:{item_id}"
    signal_id = f"news-topic-{item_id}"
    since_iso = (datetime.now(timezone.utc) - timedelta(hours=_SPIKE_COOLDOWN_HOURS)).isoformat()

    if pa_store.has_recent(subject, "news_spike", since_iso):
        return False

    # INT14b: coalesces into an existing pending row for this subject instead of always
    # spawning a separate one.
    pa_store.upsert_pending(
        subject=subject,
        ticker="",
        signal_id=signal_id,
        trigger="news_spike",
        payload={
            "label": item.get("label") or item.get("value") or "Topic",
            "note": item.get("note") or "",
            "search_queries": item.get("search_queries") or [],
            "new_count": new_count,
        },
    )
    logger.info(
        "rss_poller: news_spike enqueued for topic %s (%d recently published)",
        item.get("label") or item_id,
        new_count,
    )
    return True


async def _process_topic(
    item: dict,
    pa_store: PendingAnalysisStore,
    enqueue_count: list[int],
    spike_min_abs: int = _SPIKE_MIN_ABS,
    spike_factor: float = _SPIKE_FACTOR,
    *,
    cache_ttl: float,
    fetched_urls: set[str] | None = None,
    ticker_assets: dict[str, str] | None = None,
) -> None:
    """Fetch RSS for one watchlist topic (cache hygiene, at `cache_ttl`) and, for topics with
    monitor_news truthy, run spike detection. Shared verbatim by the slow tier (every topic,
    cache_ttl=_SLOW_CACHE_TTL, fetched_urls=None) and the fast tier (monitor_news topics only,
    cache_ttl=_FAST_CACHE_TTL, INT7) so the two paths use IDENTICAL logic — see module docstring.

    `fetched_urls`, when given, filters out any feed URL already fetched elsewhere this tick
    (a fast-tier holding whose linked topic this is) before calling gather_rss, then records
    this topic's own feeds into it too (so a LATER topic sharing the same linked ticker also
    skips it). URLs are recorded PRE-fetch (the holding/topic INTENDS to fetch them), not
    post-success — harmless even on a failed fetch: monitoring/collect.py's per-URL cache also
    remembers an empty result for the same short TTL, so an independent topic-side refetch of a
    URL that just failed would likewise yield nothing this tick; skipping it outright just saves
    the redundant attempt.

    `ticker_assets` (2026-07-16 nit follow-up) is an upper-ticker -> instrument_id map read once
    per tick from the OTHER watchlist ticker items — see
    ``_augment_topic_feeds_with_linked_asset_subjects``'s docstring for why this topic's own
    linked-ticker feed needs it merged in at resolution time, not just the ticker side's mirror.

    `enqueue_count` is a single-element mutable list used as a per-tier counter across calls,
    mirroring rss_poller._process_holding_fast's own counter (tracked separately per tier — see
    rss_poller's module docstring)."""
    feeds = resolve_topic_feeds(item)
    _augment_topic_feeds_with_linked_asset_subjects(feeds, item, ticker_assets or {})
    if fetched_urls is not None:
        feeds = [f for f in feeds if f.get("url") not in fetched_urls]
        fetched_urls.update(f["url"] for f in feeds if f.get("url"))
    if feeds:
        await gather_rss(feeds, max_age=cache_ttl, read_back=False)

    if not item.get("monitor_news"):
        return

    item_id = (item.get("id") or "").strip()
    if not item_id:
        return

    if enqueue_count[0] >= _SPIKE_MAX_PER_POLL:
        return

    # Off the event loop, same rationale as rss_poller._process_holding_fast (55 MB evidence file).
    evidence_rows = await asyncio.to_thread(
        read_evidence, subjects=[f"topic:{item_id}"], fresh_only=True
    )
    # OR-combined publish+arrival clock (INT5) — see spike_detection._is_spike_any_clock.
    spike, recent_count = _is_spike_any_clock(evidence_rows, spike_min_abs, spike_factor)
    if spike:
        if _spike_enqueue_topic(item, recent_count, pa_store):
            enqueue_count[0] += 1
            if enqueue_count[0] >= _SPIKE_MAX_PER_POLL:
                logger.info(
                    "rss_poller: per-poll spike cap (%d) reached — further topic spikes "
                    "suppressed this tick",
                    _SPIKE_MAX_PER_POLL,
                )


async def _run_fast_tier_topics(
    items: list[dict],
    pa_store: PendingAnalysisStore,
    fetched_urls: set[str],
    spike_min_abs: int,
    spike_factor: float,
    *,
    cache_ttl: float,
    topic_cap: int,
    ticker_assets: dict[str, str] | None = None,
) -> int:
    """The fast tier's full pass over watchlist topics (INT7) — mirrors the shape of
    rss_poller.run_rss_poll's per-holding fast loop (per-item try/except isolation, one shared
    enqueue_count counter for the per-tier spike cap) so the two loops read the same way.

    `items` is the tick's full topic list (already filtered to type=="topic" by the caller);
    this fn does its own monitor_news filtering, then caps the result at `topic_cap` (logged
    ONCE, not per topic, when trimmed) so a user with many topics can't blow up a single tick —
    the excess still gets the slow tier, unchanged. `ticker_assets` is forwarded to
    `_process_topic` unchanged (2026-07-16 nit follow-up — see its docstring). Returns the
    number of topics processed."""
    monitor_topics = [t for t in items if t.get("monitor_news")]
    if len(monitor_topics) > topic_cap:
        logger.info(
            "rss_poller: fast tier topic cap (%d) reached — %d topic(s) deferred to slow tier",
            topic_cap,
            len(monitor_topics) - topic_cap,
        )
        monitor_topics = monitor_topics[:topic_cap]

    enqueue_count = [0]
    count = 0
    for item in monitor_topics:
        try:
            await _process_topic(
                item,
                pa_store,
                enqueue_count,
                spike_min_abs,
                spike_factor,
                cache_ttl=cache_ttl,
                fetched_urls=fetched_urls,
                ticker_assets=ticker_assets,
            )
            count += 1
        except Exception as exc:
            logger.warning(
                "rss_poller: fast tier topic %s failed: %s",
                item.get("label", "?"),
                type(exc).__name__,
            )
    return count
