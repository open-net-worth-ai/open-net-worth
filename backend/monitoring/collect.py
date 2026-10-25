"""Collectors — the source seams that feed the EvidenceStore. Two today:

  * ``gather_evidence`` — live WEB SEARCH (the optional "comprehensive" layer).
  * ``gather_rss``      — RSS/Atom feeds (the FREE, default news source).

Both persist hits into the EvidenceStore (dedup-merged by canonical URL) and return the UNION of
today's hits with prior unexpired cached rows for ``subjects`` — in the provider-hit shape the
analyzers/prompts already consume. Multiple collectors → one store: a story from RSS and the same
story from search merge into one row (unioned subjects/queries).

Lives in monitoring/ because it touches ai.search + the network; the cache policy it calls lives in
services/evidence and the feed resolver/parser in services/feeds (the layer boundary).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.error
from urllib.parse import urlsplit, urlunsplit

from backend.ai.search import get_search_provider
from backend.core.news_arrivals import emit as _emit_arrivals
from backend.services.evidence import read_evidence, record_evidence

logger = logging.getLogger(__name__)

_URL_IN_ERROR_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_LOG_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX_RSS_LOG_VALUE = 512


def _safe_rss_log_text(value: str) -> str:
    return _LOG_CONTROL_CHARS.sub("", value)[:_MAX_RSS_LOG_VALUE]


def _rss_log_url(url: str) -> str:
    """Return a feed identifier that cannot expose URL credentials or query parameters."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return _safe_rss_log_text(urlunsplit((parts.scheme, netloc, parts.path, "", "")))


def _rss_error_detail(exc: Exception) -> str:
    """Keep an RSS failure actionable without allowing remote text to forge or leak a log line."""
    if isinstance(exc, urllib.error.HTTPError):
        detail = f"http_status={exc.code}"
    else:
        detail = str(getattr(exc, "reason", None) or exc)
    detail = _URL_IN_ERROR_RE.sub("<url>", detail)
    return " ".join(_safe_rss_log_text(detail).split())[:200] or "no detail"


def _to_provider_shape(row: dict) -> dict:
    """Map a stored evidence row back to the provider-hit shape (url/title/content/published_date/
    _query) so callers that formatted raw search hits keep working unchanged. ``source`` is carried
    through so prompts/UI can attribute the outlet."""
    queries = row.get("queries") or []
    return {
        "url": row.get("url", ""),
        "title": row.get("title", ""),
        "content": row.get("snippet", ""),
        "published_date": row.get("published_at", ""),
        "image": row.get("image"),
        "source": row.get("source", ""),
        "source_url": row.get("source_url", ""),  # publisher homepage → real domain/favicon
        "_query": queries[0] if queries else "",
    }


async def gather_evidence(
    queries: list[str],
    subjects: list[str],
    *,
    kind: str = "news",
    max_results: int = 5,
    prefer_cache: bool = False,
    limit: int | None = None,
    store=None,
) -> list[dict]:
    """Collect + accumulate evidence for `subjects`. Returns the fresh union (provider shape).

    prefer_cache=True skips the live search entirely when fresh cache already covers the subjects
    (used by the briefing so it rides a recent scan's results instead of re-searching).
    A per-query search failure is swallowed (logged by class) — the cache still returns prior rows.
    """
    from backend.db.evidence_store import EvidenceStore

    store = store or EvidenceStore()
    queries = [q for q in (queries or []) if q]

    # Same reason as the read at the end of this function: synchronous SQL must not run on
    # the event loop thread.
    cached = (
        await asyncio.to_thread(read_evidence, subjects=subjects, store=store)
        if prefer_cache
        else []
    )
    if queries and not (prefer_cache and cached):
        provider = get_search_provider()

        async def _one(q: str) -> list[dict]:
            try:
                rows = await provider.search(q, max_results=max_results)
                for r in rows:
                    r["_query"] = q
                    r["via"] = "search"
                return rows
            except Exception as exc:
                logger.warning("collect: search failed for %r (%s)", q, type(exc).__name__)
                return []

        nested = await asyncio.gather(*[_one(q) for q in queries])
        hits = [r for batch in nested for r in batch]
        if hits:
            record_evidence(hits, subjects=subjects, queries=queries, kind=kind, store=store)

    # OFF THE LOOP. read_evidence → EvidenceStore.for_subjects runs a synchronous SQL query
    # and materialises every row; called directly from this async function it held the event
    # loop for the whole thing, so nothing else in the process could run. Captured live by
    # core.loop_watch on 2026-08-11 as the top blocking frame
    # (evidence_store.py:139 `[r.payload for r in conn.execute(q)]`), reached from
    # api/briefing.py's news fetch. 76 stalls logged in one session.
    rows = await asyncio.to_thread(
        read_evidence, subjects=subjects, fresh_only=True, limit=limit, store=store
    )
    return [_to_provider_shape(r) for r in rows]


# ── RSS collector (free, default news source) ────────────────────────────────────

_RSS_UA = "Mozilla/5.0 (compatible; open-net-worth/1.0; +https://github.com/open-net-worth)"
_RSS_CACHE_TTL = 900.0  # 15 min — honor feed cache hints; don't refetch a feed within the window
_RSS_MAX_BYTES = 4_000_000  # cap the response body so a hostile/huge feed can't exhaust memory
_rss_cache: dict[str, tuple[float, list]] = {}  # feed_url → (fetched_at_monotonic, hits)
# feed_url → {"etag":..., "last_modified":...} — conditional-GET state (INT5: the
# finanznachrichten.de firehose is polled every hot tick, so honoring ETag/Last-Modified when
# the server sends one avoids re-downloading/re-parsing an unchanged firehose every 60-120s).
_conditional_cache: dict[str, dict[str, str]] = {}
# feed_url → True if the LAST real fetch attempt for that url failed (never touched by a pure
# cache hit — this reflects the LAST attempt, not "is currently stale"). Read via
# fetch_failed(); the hot tier's per-source backoff (rss_poller_hot.py) uses this to detect a
# persistently failing source WITHOUT changing _fetch_feed's swallow-and-return-[] contract for
# every other caller.
_fetch_failed: dict[str, bool] = {}

# SF-6 (code review): both caches are keyed by URL and never evicted — over a long-running
# process, a changing watchlist (feeds added/removed/re-linked over months) leaks entries
# indefinitely. Bounded, simple eviction rather than a full LRU/TTL sweep.
_MAX_CACHE_ENTRIES = 512


def _evict_oldest_by_timestamp(cache: dict[str, tuple], max_entries: int) -> None:
    """Drop the entries with the OLDEST `value[0]` (a fetched_at_monotonic) once `cache`
    exceeds `max_entries`. Used for `_rss_cache`, whose values carry a real timestamp."""
    overflow = len(cache) - max_entries
    if overflow <= 0:
        return
    oldest_keys = sorted(cache, key=lambda k: cache[k][0])[:overflow]
    for k in oldest_keys:
        cache.pop(k, None)


def _evict_oldest_fifo(cache: dict, max_entries: int) -> None:
    """Drop the OLDEST-INSERTED entries (dict insertion order) once `cache` exceeds
    `max_entries`. Used for `_conditional_cache`/`_fetch_failed`, whose values carry no
    timestamp of their own."""
    overflow = len(cache) - max_entries
    for _ in range(max(0, overflow)):
        oldest_key = next(iter(cache), None)
        if oldest_key is None:
            break
        cache.pop(oldest_key, None)


def _fetch_feed(
    url: str,
    max_age: float | None = None,
    user_agent: str | None = None,
    parser=None,
) -> list[dict]:
    """SSRF-guarded feed fetch → parsed hits, cached per feed-url. [] on any failure
    (one bad feed must never sink a scan). Redirects are re-validated per hop; the body is size-capped.
    Blocking — call via asyncio.to_thread.

    max_age overrides the module-level _RSS_CACHE_TTL for this call; None → default TTL.
    Callers with a shorter poll window (fast RSS poller, hot tier) pass their own TTL so the
    cache is correctly bypassed even when a prior fetch populated it recently.

    user_agent overrides the default RSS UA — some sources (SEC EDGAR) mandate their own
    declared identity or drop the request. parser overrides parse_rss for a non-XML source
    (GDELT's JSON) — the cache is keyed purely by URL, so a caller must never reuse the same
    URL with two different parsers."""
    ttl = max_age if max_age is not None else _RSS_CACHE_TTL
    now = time.monotonic()
    cached = _rss_cache.get(url)
    if cached and now - cached[0] < ttl:
        return cached[1]
    hits: list[dict] = []
    failed = False
    try:
        from backend.core.pinned_http import pinned_urlopen
        from backend.services.feeds import parse_rss

        headers = {"User-Agent": user_agent or _RSS_UA}
        cond = _conditional_cache.get(url)
        if cond:
            if cond.get("etag"):
                headers["If-None-Match"] = cond["etag"]
            if cond.get("last_modified"):
                headers["If-Modified-Since"] = cond["last_modified"]
        try:
            # DNS-rebind-safe: resolves + PINS the connection to the validated IP, and
            # re-validates+re-pins every redirect hop (ADR 2026-07-16-dns-rebind-pinned-fetch)
            # — a plain urlopen() would re-resolve DNS at connect time, after the guard already
            # ran, letting a low-TTL attacker domain rebind to a private/metadata address.
            with pinned_urlopen(url, headers=headers, timeout=10) as resp:
                raw = resp.read(_RSS_MAX_BYTES)
                # getattr-guarded: a plain fake response (test doubles) may not carry .headers.
                resp_headers = getattr(resp, "headers", None)
                etag = resp_headers.get("ETag") if resp_headers else None
                last_modified = resp_headers.get("Last-Modified") if resp_headers else None
                if etag or last_modified:
                    _conditional_cache[url] = {
                        "etag": etag or "",
                        "last_modified": last_modified or "",
                    }
            hits = (parser or parse_rss)(raw)
        except urllib.error.HTTPError as http_exc:
            if http_exc.code == 304:
                # Server confirms nothing changed since our last conditional GET — keep
                # whatever we had (the prior TTL-expired cache entry, if any), no re-parse.
                hits = cached[1] if cached else []
            else:
                raise
    except Exception as exc:
        logger.warning(
            "collect: rss fetch failed url=%s error=%s detail=%s",
            _rss_log_url(url),
            type(exc).__name__,
            _rss_error_detail(exc),
        )
        failed = True
    _rss_cache[url] = (now, hits)
    _fetch_failed[url] = failed
    _evict_oldest_by_timestamp(_rss_cache, _MAX_CACHE_ENTRIES)
    _evict_oldest_fifo(_conditional_cache, _MAX_CACHE_ENTRIES)
    _evict_oldest_fifo(_fetch_failed, _MAX_CACHE_ENTRIES)
    return hits


def fetch_failed(url: str) -> bool:
    """Whether the LAST real fetch attempt for `url` failed (False if never fetched, or the
    last attempt was a cache hit / succeeded). Lets a caller (the hot tier's per-source
    backoff) detect a persistently failing source without _fetch_feed raising for anyone
    else — every other caller keeps its existing swallow-and-return-[] contract."""
    return _fetch_failed.get(url, False)


async def gather_rss(
    feeds: list[dict],
    *,
    store=None,
    limit: int | None = None,
    max_concurrency: int = 5,
    max_age: float | None = None,
    read_back: bool = True,
) -> list[dict]:
    """Fetch resolved RSS feeds → record into the EvidenceStore tagged with each feed's subjects →
    return the fresh union for all subjects (provider shape).

    ``feeds`` items are ``{url, subjects, kind, via}`` from ``services.feeds.resolve_*``. Fetches run
    with bounded concurrency (politeness to Google News/Yahoo — they share one host each) and per-feed
    caching; a single feed's failure is swallowed so one bad feed never sinks the scan.

    max_age is forwarded to _fetch_feed to override the default cache TTL.  Callers with a shorter
    polling window (the fast RSS poller) pass their own TTL; None preserves prior behaviour.

    ``read_back=False`` records and emits arrivals but skips reading the subjects' evidence back
    (returns []): that read decodes every matching row — tens of MB of JSON on a real book — and
    a poller that only freshens the store discards it, holding the GIL against every request."""
    from backend.db.evidence_store import EvidenceStore

    store = store or EvidenceStore()
    feeds = [f for f in (feeds or []) if f.get("url")]
    if not feeds:
        return []
    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _one(f: dict):
        url = f["url"]
        async with sem:
            return f, await asyncio.to_thread(_fetch_feed, url, max_age)

    results = await asyncio.gather(*[_one(f) for f in feeds], return_exceptions=True)
    all_subjects: set[str] = set()
    # One store write per (subjects, kind) — NOT per feed. Every write is a full
    # read-modify-write of the whole evidence file (55 MB on a real book), so per-feed writes
    # amplified into seconds of blocking I/O per poll tick (2026-07-06 Plan-tab stall root
    # cause). record_evidence unions same-scan duplicate URLs itself, so cross-feed repeats
    # (Google News + Yahoo carrying the same article) still merge to one row.
    batches: dict[tuple[tuple[str, ...], str], list[dict]] = {}
    for res in results:
        if isinstance(res, BaseException):
            continue
        f, hits = res
        subs = f.get("subjects") or []
        all_subjects.update(subs)
        # The RESOLVER's own `via` (rss:google_news / rss:yahoo / rss:custom / …) always wins
        # over whatever the parser stamped — parse_rss's `default_via` is "rss" unless a caller
        # threads its real via through, so a bare setdefault here was a no-op (SF-1, INT8 review:
        # every RSS-resolved hit was landing as the literal "rss", never its actual source).
        for h in hits:
            h["via"] = f.get("via") or h.get("via") or "rss"
        if hits and subs:
            batches.setdefault((tuple(subs), f.get("kind") or "news"), []).extend(hits)
    if not all_subjects:
        return []

    def _record_and_read() -> tuple[list[dict], list[dict]]:
        new_rows: list[dict] = []
        for (subs, kind), hits in batches.items():
            merged = record_evidence(hits, subjects=list(subs), kind=kind, store=store)
            # A row's seen_count is 1 iff this call inserted it for the FIRST time ever (a
            # re-merge of an existing row always increments it) — the cheapest possible
            # "newly arrived" signal, no separate before/after diff needed (news-ingestion-
            # latency.md §3).
            new_rows.extend(r for r in merged if r.get("seen_count") == 1)
        if not read_back:
            return [], new_rows
        fresh = read_evidence(
            subjects=sorted(all_subjects), fresh_only=True, limit=limit, store=store
        )
        return fresh, new_rows

    # Off the event loop: parsing + re-serializing the evidence file is hundreds of ms of
    # sync work that was freezing every in-flight HTTP request (GIL aside, the loop must not
    # run it inline).
    rows, new_rows = await asyncio.to_thread(_record_and_read)
    if new_rows:
        await _emit_arrivals(new_rows)
    return [_to_provider_shape(r) for r in rows]


async def gather_matched(
    url: str,
    entities: list[dict],
    *,
    parser=None,
    user_agent: str | None = None,
    store=None,
    max_age: float | None = None,
    via: str | None = None,
    read_back: bool = True,
) -> list[dict]:
    """Fetch ONE shared feed (a regulatory firehose or a combined topic-engine query) and fan
    its hits out to whichever watched `entities` they match, instead of gather_rss's one-feed-
    one-subjects-list model — the shape a SHARED source needs since a single fetch must route
    to N different subjects (news-ingestion-latency.md §1-2). Matching is
    services.feeds_hot.match_hits_to_subjects (name/ticker token, no LLM). `entities` is
    `[{subject, terms}]`. [] on any failure or empty match — one bad firehose must never sink
    the hot tick. Emits newly-inserted rows to core.news_arrivals exactly like gather_rss.

    `via`, when given, is stamped onto every hit from this fetch — the CALLER's own registry
    entry (a MarketFeed/OutletFeed's `via`) always wins over whatever the parser stamped
    (SF-1, INT8 review: parse_rss's bare `default_via="rss"` meant every regulatory/outlet hit
    was landing as the literal "rss", never its real source — dead per-source attribution)."""
    from backend.db.evidence_store import EvidenceStore
    from backend.services.feeds_hot import match_hits_to_subjects

    store = store or EvidenceStore()
    if not entities:
        return []
    hits = await asyncio.to_thread(_fetch_feed, url, max_age, user_agent, parser)
    matched = match_hits_to_subjects(hits, entities)
    if not matched:
        return []

    batches: dict[tuple[str, ...], list[dict]] = {}
    all_subjects: set[str] = set()
    for h in matched:
        subs = tuple(sorted(set(h.pop("_subjects"))))
        all_subjects.update(subs)
        h["via"] = via or h.get("via") or "rss"
        batches.setdefault(subs, []).append(h)

    def _record_and_read() -> tuple[list[dict], list[dict]]:
        new_rows: list[dict] = []
        for subs, hh in batches.items():
            merged = record_evidence(hh, subjects=list(subs), kind="news", store=store)
            new_rows.extend(r for r in merged if r.get("seen_count") == 1)
        if not read_back:
            return [], new_rows
        fresh = read_evidence(subjects=sorted(all_subjects), fresh_only=True, store=store)
        return fresh, new_rows

    rows, new_rows = await asyncio.to_thread(_record_and_read)
    if new_rows:
        await _emit_arrivals(new_rows)
    return [_to_provider_shape(r) for r in rows]
