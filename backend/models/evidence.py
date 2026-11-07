"""Evidence record schema — the durable cache row behind Intel scans (leaf, stdlib only).

One flat-list JSON store (``evidence.json``), keyed by a stable URL hash so the same article
seen across scans is ONE row. Persisting the raw hit (esp. ``snippet``) is the fix for "news
found yesterday disappears today even though it's still valid": a scan reads the accumulated
rows, tops them up with today's live search, and only ages items out on real TTL expiry —
never just because they fell out of one live search.

Derived (never stored, computed on read so they can't go stale): ``age_hours``, ``is_fresh``.
"""

from __future__ import annotations

from typing import Optional, TypedDict


class EvidenceRow(TypedDict, total=False):
    id: str  # "ev_" + sha1(canonical_url)[:16] — the dedup key
    url: str  # canonical URL (tracking params + fragment stripped)
    domain: str  # bare hostname (provenance / GC keep-list)
    title: str
    snippet: str  # provider content, <=500 chars — THE persisted evidence
    published_at: str  # provider published date, best-effort; "" if unknown (never fabricated)
    image: Optional[str]
    provider: str  # which SearchProvider produced it (legacy/provenance)
    # Richer provenance — capture everything useful a source gives us:
    source: str  # outlet display name, e.g. "Reuters" (RSS <source>), else domain-derived
    via: str  # the channel that fetched it: "rss:google_news" | "rss:yahoo" | "search" | "regulatory:edgar"
    guid: str  # feed item guid — stable id + dedup fallback when the URL is a redirect
    author: str  # article author when the feed supplies it (Atom), else ""
    kind: str  # "news" | "asset" | "macro" | "opportunity" | "filing" — TTL selector
    subjects: list[str]  # logical join keys, e.g. ["asset:42", "ticker:ACME", "scan:intel"]
    queries: list[str]  # every query string that surfaced this URL (audit + gap detection)
    first_seen: str  # ISO, set once on insert — the "yesterday's news stays" anchor
    last_seen: str  # ISO, bumped each time a live search re-surfaces it (corroboration)
    seen_count: int  # how many scans returned this URL
    expires_at: str  # ISO = last_seen + TTL(kind); drives the freshness gate AND GC
