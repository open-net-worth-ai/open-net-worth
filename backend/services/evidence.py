"""Evidence service — the policy layer over EvidenceStore (canonical key, TTL, freshness, sort).

This is the system of record for raw news/web evidence behind Intel scans. The bug it fixes: every
scan used to fetch news live and keep only THAT run's hits, so an article that dropped out of today's
live search vanished even while still valid. Here a scan calls ``record_evidence`` (dedup-merge into
the store) then ``read_evidence`` (the accumulated, still-fresh union) — evidence persists across runs
and ages out only on real TTL expiry.

Layer: services → db → core/models only (no api/monitoring/ai imports). The collector that does the
live search lives in monitoring/collect.py (it may import ai.search); this module never searches.
"""

from __future__ import annotations

import email.utils
import hashlib
import re
from datetime import datetime, timedelta, timezone

from backend.core.urls import canonical_url, hostname

# TTL by kind — the RE-SEARCH / persistence gate for scan-context / time-sensitive kinds.
# news + asset use PUBLISH-based retention (_PUBLISH_RETENTION_DAYS), not this map.
_TTL_BY_KIND: dict[str, int] = {
    "opportunity": 12,
    "macro": 48,
    "filing": 168,
}
_DEFAULT_TTL_HOURS = 24

# Kinds that retain by ARTICLE PUBLISH DATE rather than scan/collection time.
# A 2-day-old article stays fresh for ~88 more days regardless of re-scan cadence.
_PUBLISH_KINDS: frozenset[str] = frozenset({"news", "asset"})
_PUBLISH_RETENTION_DAYS = 90

# Evergreen reference sources (encyclopaedia/definitions) — never GC'd even when expired.
REFERENCE_DOMAINS: frozenset[str] = frozenset({"wikipedia.org", "investopedia.com"})

# Compact article-age parser (relative strings + ISO). NOTE: asset_brief.py has its own copy of
# this freshness logic; RFC step 3 folds it onto this shared home. Kept self-contained for now so
# the first slice touches no existing collector.
_REL_UNIT_RE = re.compile(
    r"(\d+)\s*(years?|yrs?|months?|mo|weeks?|wks?|days?|hours?|hrs?|minutes?|mins?|[ymwdh])\b"
)


def _now_iso(now: str | None = None) -> str:
    return now or datetime.now(timezone.utc).isoformat()


def ticker_subject(ticker: str) -> str:
    """The evidence subject for news from one ticker's own feed when it has no instrument."""
    return f"ticker:{ticker.strip().upper()}"


def canonical_key(url: str) -> str:
    """Stable dedup id for a URL: ``ev_`` + sha1(canonical_url)[:16]. Same article (tracking
    params / fragment / www / casing aside) → same id across every scan."""
    return "ev_" + hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def ttl_hours(kind: str | None) -> int:
    """Scan-time TTL in hours for opportunity/macro/filing kinds. news+asset use publish retention."""
    return _TTL_BY_KIND.get((kind or "").lower(), _DEFAULT_TTL_HOURS)


def _parse_published(s: str | None) -> datetime | None:
    """Parse an article publish timestamp into a timezone-aware UTC datetime.

    Handles ISO-8601 (with/without Z / offset), RFC-2822 / email-header format, and common feed
    variants. Returns None for None, empty strings, garbage, or implausibly future dates (> now+2d).
    Never raises.
    """
    if not s or not s.strip():
        return None
    raw = s.strip()
    now_utc = datetime.now(timezone.utc)
    dt: datetime | None = None

    # ISO-8601 path (most feeds): "2026-06-19T10:00:00Z", "...+05:30", "2026-06-19 10:00:00"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pass

    # RFC-2822 / email-header path: "Wed, 27 May 2026 09:00:00 GMT"
    if dt is None:
        try:
            dt = email.utils.parsedate_to_datetime(raw)
        except Exception:
            pass

    if dt is None:
        return None

    # Normalise to UTC-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Reject implausible future dates — feed clock-skew / bad data. Gated against wall-clock now
    # (not any injected record-time `now`): a publish date in the real future is bad data regardless.
    if dt > now_utc + timedelta(days=2):
        return None

    return dt


def recency_hours(published_at: str) -> float:
    """Approximate article age in HOURS (smaller = newer). Undated/unparseable → 1e9 (sorts last).

    Bug fix (2026-08-30): the absolute-date branch used to be a bare `datetime.fromisoformat`,
    which can't read RFC-2822 pubDates ("Sun, 30 Aug 2026 11:50:08 GMT") — the format Google
    News/RSS feeds emit for the BULK of this store's rows. Every RSS-sourced row therefore
    scored the 1e9 "unparseable" sentinel and sank to the very bottom of `_sort_key`'s ordering,
    so `read_evidence`'s newest-first sort + `limit` cutoff was in practice built almost
    entirely from the small minority of ISO-dated (non-RSS) rows — silently starving the feed
    (surfaced live on the Asset Detail page, `services.asset_detail._live_news_for_instrument`)
    of everything but a handful of days-old items, no matter how much fresher content had
    actually landed. `_parse_published` (above, already used by `_compute_expires`) already
    handles both formats correctly — reusing it here, rather than re-deriving a second parser,
    is the fix."""
    s = (published_at or "").strip().lower()
    if not s:
        return 1e9
    if "just now" in s or "today" in s:
        return 0.0
    if "yesterday" in s:
        return 24.0
    m = _REL_UNIT_RE.search(s)
    if m:
        n, u = float(m.group(1)), m.group(2)
        if u.startswith(("year", "yr", "y")):
            return n * 8760.0
        if u.startswith(("month", "mo")):
            return n * 730.0
        if u.startswith(("week", "wk", "w")):
            return n * 168.0
        if u.startswith(("day", "d")):
            return n * 24.0
        if u.startswith(("hour", "hr", "h")):
            return n * 1.0
        return n / 60.0  # minutes
    dt = _parse_published(published_at)
    if dt is None:
        return 1e9
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def _sort_key(row: dict) -> float:
    return recency_hours(row.get("published_at") or "")


def _compute_expires(kind: str, published_raw: str | None, now_dt: datetime) -> str:
    """Return an ISO-UTC expires_at string for one evidence row.

    news/asset: keyed to ARTICLE PUBLISH DATE + 90 days so retention doesn't depend on re-scan
    cadence.  base = parsed publish date, or now (first_seen proxy) if unparseable / absent.
    opportunity/macro/filing + unknowns: classic scan-time TTL (hours from _TTL_BY_KIND).
    """
    if kind in _PUBLISH_KINDS:
        base = _parse_published(published_raw) or now_dt
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        return (base + timedelta(days=_PUBLISH_RETENTION_DAYS)).isoformat()
    return (now_dt + timedelta(hours=ttl_hours(kind))).isoformat()


def _record_from_hit(hit: dict, subjects: list[str], kind: str, now: str, expires: str) -> dict:
    """Build one canonical evidence row from a raw provider hit (provider shape: url/title/content/
    published_date/image/_query). Queries come from the hit's own _query/_queries or the call's set."""
    url = (hit.get("url") or "").strip()
    queries = hit.get("_queries") or ([hit["_query"]] if hit.get("_query") else [])
    return {
        "id": canonical_key(url),
        "url": canonical_url(url),
        "domain": hostname(url),
        "title": hit.get("title") or "",
        "snippet": (hit.get("content") or hit.get("snippet") or "")[:500],
        "published_at": hit.get("published_date") or hit.get("published_at") or "",
        "image": hit.get("image"),
        "provider": hit.get("provider") or "",
        "source": hit.get("source") or hostname(url),  # outlet display name (RSS <source>)
        "source_url": (
            hit.get("source_url") or ""
        ).strip(),  # publisher homepage (GN <source url=>)
        "via": hit.get("via") or "",  # channel provenance: rss:google_news / search / …
        "guid": hit.get("guid") or "",  # stable feed id (dedup fallback for redirect links)
        "author": hit.get("author") or "",
        "kind": kind,
        "subjects": sorted(set(subjects or [])),
        "queries": sorted(set(queries)),
        "first_seen": now,
        "last_seen": now,
        "seen_count": 1,
        "expires_at": expires,
    }


def record_evidence(
    items: list[dict],
    *,
    subjects: list[str],
    queries: list[str] | None = None,
    kind: str = "news",
    now: str | None = None,
    store=None,
) -> list[dict]:
    """Dedup-merge raw provider hits into the store. Hits without a URL are dropped; same-scan
    duplicates (same canonical URL) are unioned before the merge so seen_count counts scans, not
    in-scan repeats. Returns the merged rows."""
    from backend.db.evidence_store import EvidenceStore

    store = store or EvidenceStore()
    now_str = _now_iso(now)
    now_dt = datetime.fromisoformat(now_str)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    subj = sorted(set(subjects or []))
    by_id: dict[str, dict] = {}
    for it in items or []:
        if not (it.get("url") or "").strip():
            continue
        published_raw = it.get("published_date") or it.get("published_at") or None
        expires = _compute_expires(kind, published_raw, now_dt)
        if expires <= now_str:
            # news/asset retention is keyed to the ARTICLE'S OWN publish date (90 days from
            # it, not from when we happened to first see it) — an article already older than
            # that the moment it's first scraped would be written and then immediately be
            # due for GC on the very next prune, never read as fresh even once (1.5K rows
            # churning every ~4h on the owner's box). Skipping it here is a strict no-op for
            # every reader: `read_evidence`'s fresh_only gate would have hidden it from the
            # instant it landed anyway.
            continue
        rec = _record_from_hit(it, subj, kind, now_str, expires)
        if not rec["queries"] and queries:  # hit carried no _query → fall back to the call's set
            rec["queries"] = sorted(set(queries))
        existing = by_id.get(rec["id"])
        if existing:
            existing["queries"] = sorted(set(existing["queries"]) | set(rec["queries"]))
        else:
            by_id[rec["id"]] = rec
    return store.merge_hits(list(by_id.values()))


def attach_evidence_image(url: str, image: str, *, store=None) -> bool:
    """Record an article's og:image (harvested by the brief's full-text pass) onto its
    evidence row, so every later read of the row carries it — not a scan, so seen_count and
    last_seen are untouched. False when no row matches the URL."""
    from backend.db.evidence_store import EvidenceStore

    store = store or EvidenceStore()
    return store.set_image(canonical_key(url), image)


def read_evidence(
    *,
    subjects: list[str],
    fresh_only: bool = True,
    limit: int | None = None,
    since: str | None = None,
    now: str | None = None,
    store=None,
    rows: list[dict] | None = None,
) -> list[dict]:
    """The accumulated evidence for `subjects`, newest-first. fresh_only hides TTL-expired rows.

    `since` (INT14b — the per-subject WATERMARK read), when given, additionally drops any row
    whose `first_seen` (the arrival time into OUR cache — the natural "have we already looked at
    this?" marker, not the article's own possibly-inconsistent `published_at`) is NOT strictly
    newer than `since` — typically a prior Intel signal's `updated_at`, i.e. "everything that
    landed since we last analyzed this subject." A row missing `first_seen` falls back to
    `published_at` for this comparison. Default None preserves the pre-existing blind
    freshest-N behavior for every other caller — this is purely additive.

    `rows`, when given, is passed straight through to ``for_subjects`` to skip a redundant
    full-store reparse (see its docstring)."""
    from backend.db.evidence_store import EvidenceStore

    store = store or EvidenceStore()
    rows = store.for_subjects(subjects, fresh_only=fresh_only, now=_now_iso(now), rows=rows)
    if since:
        rows = [r for r in rows if (r.get("first_seen") or r.get("published_at") or "") > since]
    rows.sort(key=_sort_key)
    return rows[:limit] if limit else rows


def prune_evidence(now: str | None = None, store=None) -> int:
    """GC: drop TTL-expired rows (evergreen reference domains exempt). Returns # removed."""
    from backend.db.evidence_store import EvidenceStore

    store = store or EvidenceStore()
    return store.prune(_now_iso(now), keep_domains=REFERENCE_DOMAINS)
