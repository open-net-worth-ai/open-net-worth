"""RSS poller — HOT tier (news-ingestion-latency.md §2), split out of rss_poller.py to keep
that module under the file-size convention. Per-source health/backoff/politeness STATE lives
in the sibling ``rss_poller_health.py`` (same file-size reason, read that module first — it
owns failure-streak backoff and the INT9 Phase B min_interval politeness this module calls
into via ``_should_skip``/``_due_for_interval``/``_mark_attempted``/``_record_result``).

Runs every 60-120s (scheduler: rss_poll_hot), covering ONLY the near-real-time sources the
fast (5 min) / slow (30 min) tiers are too slow for:

  * the regulatory-disclosure registry (services/feeds_regulatory.py) — one fetch per
    registered market feed (v1: the FN EQS ad-hoc firehose + SEC EDGAR current-8-K), each
    fanned out to every matching watched entity server-side (name/ticker token match — no
    per-entity fetch, no per-ISIN slug discovery);
  * the outlet-headline registry (services/feeds_outlets.py, INT8) — one fetch per curated
    quality outlet feed (FT/CNBC/MarketWatch/WSJ/TechCrunch), same server-side entity matching
    + via-keyed failure backoff as the regulatory registry; closes the "wait for an aggregator
    to rewrite the scoop" latency gap for outlet-originated news. Gated by the watchlist config
    flag `outlet_feeds_enabled` (default True);
  * ONE combined GDELT query covering every monitor_news TOPIC (hard rate-limit requirement —
    GDELT bans an IP after ~2 calls; see services/feeds_hot.py);
  * every monitor_news item's own user-supplied custom_feed_urls (owner directive 2026-07-15 —
    open-source generalization: a market with no registry entry can still get a fast lane).

**Source policy now comes from the store** (INT9 Phase A, .claude/rfcs/news-source-management.md):
the registries above stay the single source of URL/parser/User-Agent truth, but whether each
ENTRY runs this tick is decided by ``services.news_sources.active_sources("hot")`` — a disabled
row (builtin or user-added) stops its fetches next tick without a code change.
`outlet_feeds_enabled` is an ADDITIONAL top-level gate (flag AND row.enabled). Per-item
`custom_feed_urls` are unaffected — item-scoped, not store-backed (see ``_run_custom_feeds``).

**Phase B additions** (RFC §3-4): a user-added **custom firehose** row (``kind="custom"``,
``scope="firehose"`` — POST /api/intel/sources) is fetched by ``_run_custom_sources`` straight
off the STORE (no compiled code-table entry to join back to) — same server-side matching +
failure backoff as an outlet feed; distinct from a per-item ``custom_feed_urls`` entry
(item-scoped, ``_run_custom_feeds``, no server-side matching). Its row universe is
``services.news_sources.active_custom_firehose_sources()`` — every ENABLED custom row
regardless of `cadence` (review fix, 2026-07-16: cadence-gating it like the builtin registries
would leave a freshly POSTed source, default `cadence="slow"`, dormant until a manual PATCH).
Every row's own ``min_interval_sec`` is honored instead (``_due_for_interval``) — TICK-counted,
not wall-clock, so a source is due once enough hot ticks (not seconds) have elapsed since its
last attempt: identical behavior whether ticks are 90s apart in production or back-to-back in a
test. A row at/under the tick interval (every builtin regulatory/outlet row, 60s) is due every
tick — unchanged from pre-Phase-B; a "slow" custom row (1800s default) still fetches on its
first tick, then waits ~20 ticks. The builtin registries (regulatory/outlet/topic_engine) are
UNCHANGED — still gated by ``active_sources("hot")``, since their `cadence` is a deliberate
tier assignment, not an unopted-into default. GDELT's 300s default (tuned 2026-07-16 after live
throttling) is the first builtin row slower than one tick. ``source_health_snapshot()`` exposes
the failure-streak state for the REST/AI read join (api/news_sources.py); that peek happens at
the API layer, not here, since ``services.news_sources`` would cycle back through this module
if it imported it.

No LLM, no web search. One bad source never sinks the tick (per-source try/except) — mirrors
rss_poller.py's existing fast/slow isolation posture. Tracked via
``monitoring.collect.fetch_failed(url)`` (set by ``_fetch_feed`` on its last real attempt) —
zero change to that function's own contract.
"""

from __future__ import annotations

import logging

from backend.core.news import bare_company_name
from backend.monitoring import collect as collect_mod
from backend.monitoring.collect import gather_matched, gather_rss
from backend.monitoring.rss_poller_health import (
    _FAILURE_BACKOFF_BASE_TICKS,  # noqa: F401  (re-exported: hot._FAILURE_BACKOFF_BASE_TICKS)
    _FAILURE_STREAK_THRESHOLD,  # noqa: F401  (re-exported for tests: hot._FAILURE_STREAK_THRESHOLD)
    _due_for_interval,
    _mark_attempted,
    _record_result,
    _should_skip,
    _source_health,  # noqa: F401  (re-exported for tests: hot._source_health)
    _source_state,  # noqa: F401  (re-exported for tests: hot._source_state(...))
    record_tick,
    source_health_snapshot,  # noqa: F401  (re-exported for tests: hot.source_health_snapshot())
)
from backend.services.feeds import bare_ticker as _bare_ticker
from backend.services.feeds import resolve_custom_feeds
from backend.services.feeds_hot import combined_gdelt_query, gdelt_doc_url, parse_gdelt
from backend.services.feeds_outlets import all_outlet_feeds
from backend.services.feeds_regulatory import all_regulatory_feeds
from backend.services.news_sources import active_custom_firehose_sources, active_sources
from backend.services.watchlist import is_topic, read_watchlist

logger = logging.getLogger(__name__)

_HOT_CACHE_TTL = 60.0  # below the ~90s hot cadence so every tick genuinely refetches

# SF-2 (code review): if the source store comes back with ZERO rows for cadence=hot while
# watched entities exist, every regulatory/outlet/GDELT fetch silently goes dark — most likely
# because ensure_builtin_sources() failed at boot (main.py swallows that exception). Logged
# ONCE (not per-tick, same discipline as the failure-backoff outage log in rss_poller_health.py)
# and reset the moment rows reappear, so a recovered seed is silently unremarkable again.
_store_empty_warned = False


def _monitor_news_entities(items: list[dict]) -> list[dict]:
    """`[{subject, terms, tickers}]` for every monitor_news-enabled watchlist item — the universe
    the regulatory registry + GDELT combined query match against (services.feeds_hot's
    match_hits_to_subjects / combined_gdelt_query). `terms` (name-shaped, case-insensitive
    matched) is the item's own `label` plus its bare (corporate-suffix-stripped) form, so a
    journalistic headline that drops "SE"/"Inc." still matches. `tickers` (raw-symbol-shaped,
    EXACT-CASE matched — see `feeds_hot._term_matches_title`'s `is_ticker` branch for why) is
    the item's own `value` plus its bare (exchange-suffix-stripped) form, plus (for a topic) its
    linked_tickers — a linked ticker's own symbol counts as a match term for its topic's
    subject too, and is exact-case for the identical reason its owning asset's symbol is."""
    out: list[dict] = []
    for item in items or []:
        if not item.get("monitor_news", True):
            continue
        label, value = item.get("label"), item.get("value")
        terms = [t for t in (label,) if t]
        tickers = [t for t in (value,) if t]
        if label:
            bare = bare_company_name(label)
            if bare:
                terms.append(bare)
        if value:
            bare = _bare_ticker(value)
            if bare:
                tickers.append(bare)
        if is_topic(item):
            tid = (item.get("id") or "").strip()
            if not tid:
                continue
            tickers += [t for t in (item.get("linked_tickers") or []) if t]
            out.append({"subject": f"topic:{tid}", "terms": terms, "tickers": tickers})
        else:
            iid = (item.get("instrument_id") or "").strip()
            if not iid:
                continue
            out.append({"subject": f"asset:{iid}", "terms": terms, "tickers": tickers})
    return out


async def _run_feed_registry(
    feeds: list, entities: list[dict], label: str, rows_by_via: dict[str, dict]
) -> int:
    """One fetch per registered feed (a MarketFeed or OutletFeed — both share the same
    url_fn/parser/via/user_agent shape) — each fans out server-side to every entity it matches
    via gather_matched. Returns feeds fetched. A source in its failure-backoff window (SF-3), or
    not yet due for its own min_interval_sec (INT9 Phase B), is skipped entirely this tick.
    Shared by the regulatory and outlet registries (`_run_regulatory` / `_run_outlets`) since
    both are "one fetch per registry entry, fanned out server-side"."""
    count = 0
    for feed in feeds:
        key = feed.via
        if _should_skip(key):
            continue
        min_interval = rows_by_via.get(key, {}).get("min_interval_sec", _HOT_CACHE_TTL)
        if not _due_for_interval(key, min_interval):
            continue
        _mark_attempted(key)
        url = feed.url_fn()
        try:
            await gather_matched(
                url,
                entities,
                parser=feed.parser,
                user_agent=feed.user_agent,
                max_age=_HOT_CACHE_TTL,
                via=key,  # SF-1: stamp the registry's OWN via — never trust the parser's default
                read_back=False,
            )
        except Exception as exc:
            logger.warning("rss_poller_hot: %s feed %s failed: %s", label, key, type(exc).__name__)
            _record_result(key, ok=False)
            continue
        failed = collect_mod.fetch_failed(url)
        _record_result(key, ok=not failed)
        if not failed:
            count += 1
    return count


def _enabled_vias(hot_rows: list[dict], kind: str) -> set[str]:
    """The set of `via` keys the source store currently enables for `kind` at cadence=hot —
    the store's own policy read (services.news_sources.active_sources), joined back to the
    code table by `via`."""
    return {r["via"] for r in hot_rows if r.get("kind") == kind}


def _rows_by_via(hot_rows: list[dict]) -> dict[str, dict]:
    """`via` -> row, for reading each source's own `min_interval_sec` (INT9 Phase B)."""
    return {r["via"]: r for r in hot_rows if r.get("via")}


async def _run_regulatory(entities: list[dict], hot_rows: list[dict]) -> int:
    """One fetch per registered market feed CURRENTLY ENABLED in the source store (news-
    ingestion-latency.md's market registry amendment; INT9 Phase A's store-backed policy) —
    each fans out server-side to every entity it matches."""
    enabled = _enabled_vias(hot_rows, "regulatory")
    feeds = [f for f in all_regulatory_feeds() if f.via in enabled]
    return await _run_feed_registry(feeds, entities, "regulatory", _rows_by_via(hot_rows))


async def _run_outlets(entities: list[dict], hot_rows: list[dict]) -> int:
    """One fetch per curated outlet feed (services/feeds_outlets.py, INT8) CURRENTLY ENABLED in
    the source store — same server-side fan-out + backoff as the regulatory registry. Callers
    gate this on `outlet_feeds_enabled` (an ADDITIONAL top-level switch — a fetch runs only
    when the flag is on AND its own row is enabled)."""
    enabled = _enabled_vias(hot_rows, "outlet")
    feeds = [f for f in all_outlet_feeds() if f.via in enabled]
    return await _run_feed_registry(feeds, entities, "outlet", _rows_by_via(hot_rows))


async def _run_gdelt(entities: list[dict], hot_rows: list[dict]) -> int:
    """ONE combined query covering every monitor_news TOPIC — GDELT's aggressive rate limiter
    (verified banned-after-2-calls from one IP) makes a per-topic fan-out unsafe. Skips
    entirely when there are no topics to query, when the store's `topic_engine` row is
    disabled, while GDELT is in its failure-backoff window (SF-3), or when it isn't yet due for
    its own min_interval_sec (INT9 Phase B — tuned to 300s after live throttling) — all three
    are levers against prolonging a rate-limit ban."""
    topic_entities = [e for e in entities if e["subject"].startswith("topic:")]
    if not topic_entities:
        return 0
    gdelt_row = next((r for r in hot_rows if r.get("kind") == "topic_engine"), None)
    if gdelt_row is None:
        return 0
    if _should_skip("api:gdelt"):
        return 0
    if not _due_for_interval("api:gdelt", gdelt_row.get("min_interval_sec", _HOT_CACHE_TTL)):
        return 0
    query = combined_gdelt_query(topic_entities)
    if not query:
        return 0
    _mark_attempted("api:gdelt")
    url = gdelt_doc_url(query)
    try:
        await gather_matched(
            url,
            topic_entities,
            parser=parse_gdelt,
            max_age=_HOT_CACHE_TTL,
            via="api:gdelt",
            read_back=False,
        )
    except Exception as exc:
        logger.warning("rss_poller_hot: GDELT combined query failed: %s", type(exc).__name__)
        _record_result("api:gdelt", ok=False)
        return 0
    failed = collect_mod.fetch_failed(url)
    _record_result("api:gdelt", ok=not failed)
    return 0 if failed else 1


async def _run_custom_sources(entities: list[dict]) -> int:
    """User-added custom FIREHOSE sources (INT9 Phase B, POST /api/intel/sources) — same
    server-side fan-out + failure backoff as the outlet registry, sourced straight from the
    STORE (`kind="custom"`, `scope="firehose"`) since a user-added row has no compiled
    code-table entry to join back to. Distinct from per-item `custom_feed_urls`
    (`_run_custom_feeds` below) — item-scoped, already-known subjects, no server-side matching.

    Reads `active_custom_firehose_sources()` — every ENABLED custom row regardless of `cadence`
    (review fix, 2026-07-16: filtering by `cadence == "hot"` like the builtin registries left a
    freshly POSTed source, default `cadence="slow"`, genuinely dormant until a manual PATCH).
    `_due_for_interval` is what actually paces it: a "slow" row's 1800s default still runs on
    tick 1, then waits ~20 ticks before its next attempt — polite, not dead."""
    count = 0
    for row in active_custom_firehose_sources():
        key = row.get("via")
        url = row.get("url")
        if not key or not url:
            continue
        if _should_skip(key):
            continue
        if not _due_for_interval(key, row.get("min_interval_sec", _HOT_CACHE_TTL)):
            continue
        _mark_attempted(key)
        try:
            await gather_matched(url, entities, max_age=_HOT_CACHE_TTL, via=key, read_back=False)
        except Exception as exc:
            logger.warning("rss_poller_hot: custom source %s failed: %s", key, type(exc).__name__)
            _record_result(key, ok=False)
            continue
        failed = collect_mod.fetch_failed(url)
        _record_result(key, ok=not failed)
        if not failed:
            count += 1
    return count


async def _run_custom_feeds(items: list[dict]) -> int:
    """Every monitor_news item's own custom_feed_urls, all batched through ONE gather_rss call
    (already-known subjects — no server-side matching needed, unlike the shared firehoses)."""
    feeds: list[dict] = []
    for item in items or []:
        if not item.get("monitor_news", True):
            continue
        feeds.extend(resolve_custom_feeds(item))
    if not feeds:
        return 0
    try:
        await gather_rss(feeds, max_age=_HOT_CACHE_TTL, read_back=False)
    except Exception as exc:
        logger.warning("rss_poller_hot: custom feeds failed: %s", type(exc).__name__)
        return 0
    return len(feeds)


def _warn_if_store_looks_unseeded(hot_rows: list[dict], config: dict, entity_count: int) -> None:
    """SF-2: a genuinely empty `active_sources("hot")` with watched entities present usually
    means the source store never got seeded (main.py's boot-ensure failed silently) — every
    regulatory/outlet/GDELT fetch then goes dark with no signal. Distinguish that from the
    user's own `news_freshness=off` switch (active_sources' OTHER reason to return []), which
    is intentional and already logged elsewhere (the scheduler removes the job entirely)."""
    global _store_empty_warned
    freshness_off = config.get("news_freshness", "fast") == "off"
    if hot_rows or freshness_off:
        _store_empty_warned = False
        return
    if not _store_empty_warned:
        logger.warning(
            "rss_poller_hot: active_sources('hot') returned zero rows with %d watched "
            "entities present — the source store may be unseeded (ensure_builtin_sources "
            "may have failed at boot); every regulatory/outlet/GDELT fetch is skipped "
            "this tick",
            entity_count,
        )
        _store_empty_warned = True


async def run_hot_tick() -> int:
    """One hot-tier poll tick. Returns the number of distinct fetches issued (sources ×
    custom feeds) — best-effort per source, never raises."""
    record_tick()
    wl = read_watchlist()
    items = wl.get("items") or []
    entities = _monitor_news_entities(items)
    if not entities:
        return 0
    config = wl.get("config") or {}
    hot_rows = active_sources("hot")
    _warn_if_store_looks_unseeded(hot_rows, config, len(entities))
    count = 0
    count += await _run_regulatory(entities, hot_rows)
    if config.get("outlet_feeds_enabled", True):
        count += await _run_outlets(entities, hot_rows)
    count += await _run_gdelt(entities, hot_rows)
    count += await _run_custom_sources(entities)
    count += await _run_custom_feeds(items)
    return count
