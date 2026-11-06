"""RSS/Atom news feeds — pure parsing + portfolio-driven feed resolution.

The FREE, default news-source layer behind Intel scans (web search becomes the optional
"comprehensive" upgrade). This module is pure: parse a feed's bytes into provider-shaped hits, and
resolve a portfolio entity (holding / topic) into the feed URLs + subjects to fetch. The collector
that actually fetches lives in monitoring/collect.gather_rss; keeping I/O out of here makes the
parser + resolver unit-testable and keeps the layer rule (services → core only).

Captures every useful field a feed gives: url, title, summary, published, outlet source, guid,
author, image — per the "store as much as is useful" intent.
"""

from __future__ import annotations

import html
import re
import urllib.parse
import xml.etree.ElementTree as ET

from backend.core.urls import hostname
from backend.db.watchlist_topics import is_topic
from backend.services.evidence import ticker_subject

# Yahoo ticker suffix → Google News locale (hl, gl, ceid). A non-US listing's news — especially
# shareholder/regulatory disclosures — is published in the local language by local outlets, so a
# locale-aware query materially improves recall (the KSTL.DE → German-outlets finding).
_SUFFIX_LOCALE: dict[str, tuple[str, str, str]] = {
    "DE": ("de", "DE", "DE:de"),
    "F": ("de", "DE", "DE:de"),
    "VI": ("de", "AT", "AT:de"),
    "SW": ("de", "CH", "CH:de"),
    "L": ("en-GB", "GB", "GB:en"),
    "PA": ("fr", "FR", "FR:fr"),
    "AS": ("nl", "NL", "NL:nl"),
    "BR": ("nl", "BE", "BE:nl"),
    "MI": ("it", "IT", "IT:it"),
    "MC": ("es", "ES", "ES:es"),
    "ST": ("sv", "SE", "SE:sv"),
    "OL": ("no", "NO", "NO:no"),
    "HE": ("fi", "FI", "FI:fi"),
    "T": ("ja", "JP", "JP:ja"),
    "HK": ("zh-HK", "HK", "HK:zh-Hant"),
    "SS": ("zh-CN", "CN", "CN:zh-Hans"),
    "SZ": ("zh-CN", "CN", "CN:zh-Hans"),
    "TW": ("zh-TW", "TW", "TW:zh-Hant"),
    "KS": ("ko", "KR", "KR:ko"),
    "KQ": ("ko", "KR", "KR:ko"),
    "SI": ("en-SG", "SG", "SG:en"),
    "AX": ("en-AU", "AU", "AU:en"),
    "NZ": ("en-NZ", "NZ", "NZ:en"),
    "TO": ("en-CA", "CA", "CA:en"),
    "V": ("en-CA", "CA", "CA:en"),
    "NS": ("en-IN", "IN", "IN:en"),
    "BO": ("en-IN", "IN", "IN:en"),
    "SA": ("pt-BR", "BR", "BR:pt-419"),
    "MX": ("es-419", "MX", "MX:es-419"),
}
_DEFAULT_LOCALE: tuple[str, str, str] = ("en-US", "US", "US:en")


def locale_for_ticker(ticker: str) -> tuple[str, str, str]:
    """Google News (hl, gl, ceid) for a Yahoo-style ticker, keyed off its exchange suffix."""
    if ticker and "." in ticker:
        suffix = ticker.rsplit(".", 1)[1].upper()
        return _SUFFIX_LOCALE.get(suffix, _DEFAULT_LOCALE)
    return _DEFAULT_LOCALE


def google_news_url(query: str, locale: tuple[str, str, str] = _DEFAULT_LOCALE) -> str:
    hl, gl, ceid = locale
    return (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + f"&hl={urllib.parse.quote(hl)}&gl={urllib.parse.quote(gl)}&ceid={urllib.parse.quote(ceid)}"
    )


def yahoo_ticker_url(ticker: str) -> str:
    return (
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s="
        + urllib.parse.quote(ticker)
        + "&region=US&lang=en-US"
    )


def bare_ticker(value: str) -> str | None:
    """Strip a Yahoo-style exchange suffix, e.g. "KSTL.DE" -> "KSTL" (owner incident 2026-08-29
    — a real headline almost always uses the bare ticker, never the exchange-qualified form; a
    search query benefits from carrying both since Google tokenizes on punctuation anyway).
    None when there's no "." to strip. NEVER use this for `locale_for_ticker` or
    `yahoo_ticker_url` — both need the exchange-qualified ticker to resolve correctly; this is
    for search-QUERY TEXT and literal-matching TERMS only."""
    bare = (value or "").split(".", 1)[0].strip()
    return bare if bare and bare != value else None


def _feed(
    url: str,
    subjects: list[str],
    kind: str,
    via: str,
    *,
    source_kind: str = "search",
    cadence: str = "slow",
) -> dict:
    """One resolved feed. ``source_kind`` (regulatory|ticker|search|topic_engine|custom) and
    ``cadence`` (hot|fast|slow) are additive observability/routing metadata (news-ingestion-
    latency.md §1) — every resolver stamps them; existing callers keep the pre-INT5 defaults
    (search/slow) so untouched call sites are unaffected."""
    return {
        "url": url,
        "subjects": subjects,
        "kind": kind,
        "via": via,
        "source_kind": source_kind,
        "cadence": cadence,
    }


def resolve_holding_feeds(row: dict) -> list[dict]:
    """Feeds for one portfolio holding. Subjects tag BOTH the per-asset lane (``asset:{id}`` — so the
    asset-detail view picks it up via its existing read) and the scan lane (``scan:intel``)."""
    iid = (row.get("instrument_id") or "").strip()
    ticker = (row.get("price_source_id") or row.get("ticker") or "").strip()
    label = (row.get("label") or row.get("symbol") or ticker).strip()
    asset_class = (row.get("asset_class") or "equity").lower()
    subjects = [s for s in (f"asset:{iid}" if iid else "", "scan:intel") if s]
    # With no instrument there is no asset: tag, and the shared scan pool can't be searched by
    # stock. A watchlist item carries its symbol as `value`; the feed's query is unchanged.
    symbol = ticker or (row.get("value") or "").strip()
    if not iid and symbol:
        subjects.append(ticker_subject(symbol))
    if not subjects or not (label or ticker):
        return []
    feeds: list[dict] = []
    if asset_class == "crypto":
        feeds.append(_feed(google_news_url(f"{label} crypto"), subjects, "news", "rss:google_news"))
    elif asset_class == "commodity":
        # Commodity holdings are not equities — "stock" returns corporate results not price news.
        # "price" / "commodity market" surfaces the price catalysts, supply/demand, and macro drivers.
        # ticker_terms carries BOTH the exchange-qualified and bare forms (owner ask 2026-08-29) —
        # locale_for_ticker/yahoo_ticker_url below still take the ORIGINAL `ticker`, which is the
        # one that actually needs the suffix.
        bare = bare_ticker(ticker)
        ticker_terms = f"{ticker} {bare}" if bare else ticker
        query = f"{label} {ticker_terms} commodity market price".strip()
        locale = locale_for_ticker(ticker)
        feeds.append(_feed(google_news_url(query, locale), subjects, "news", "rss:google_news"))
        if locale != _DEFAULT_LOCALE:
            feeds.append(
                _feed(google_news_url(query, _DEFAULT_LOCALE), subjects, "news", "rss:google_news")
            )
        if ticker:
            feeds.append(
                _feed(
                    yahoo_ticker_url(ticker),
                    subjects,
                    "news",
                    "rss:yahoo",
                    source_kind="ticker",
                    cadence="fast",
                )
            )
    else:
        bare = bare_ticker(ticker)
        ticker_terms = f"{ticker} {bare}" if bare else ticker
        query = f"{label} {ticker_terms} stock".strip()
        locale = locale_for_ticker(ticker)
        # Local-language edition — surfaces local-outlet + regulatory news the English edition misses
        # (the German Börse/WpHG filings finding).
        feeds.append(_feed(google_news_url(query, locale), subjects, "news", "rss:google_news"))
        # AND the English (en-US) edition — the global wires (Reuters/Bloomberg/FT/WSJ) cover a
        # non-US name prominently in English, which the local edition under-covers. Both editions →
        # full recall; canonical-URL dedup merges any overlap. (US listings: local IS en-US, skip.)
        if locale != _DEFAULT_LOCALE:
            feeds.append(
                _feed(google_news_url(query, _DEFAULT_LOCALE), subjects, "news", "rss:google_news")
            )
        if ticker:
            feeds.append(
                _feed(
                    yahoo_ticker_url(ticker),
                    subjects,
                    "news",
                    "rss:yahoo",
                    source_kind="ticker",
                    cadence="fast",
                )
            )
    return feeds


def resolve_topic_feeds(item: dict) -> list[dict]:
    """Feeds for one watched topic — its crafted search_queries become Google News queries,
    PLUS (news-ingestion-latency.md §1) the linked ticker's fast Yahoo lane. The item's own
    ``custom_feed_urls`` (owner directive 2026-07-15) are a SEPARATE resolver
    (:func:`resolve_custom_feeds`) fetched only by the hot tier — not folded in here, so a
    slow-tier/intel-scan caller of this fn never double-fetches them.

    Google News search RSS honors `when:` as a FILTER, not an accelerant (measured: median 3.4h
    publish→cache either way) — so each query is rewritten with a trailing `when:1d` to bias
    results toward the last day (demoted to `cadence: slow` backfill; the fast/near-real-time
    path for a topic is its linked ticker's Yahoo lane + the hot-tier regulatory/GDELT sources,
    not this Google lane)."""
    tid = (item.get("id") or "").strip()
    subjects = [s for s in (f"topic:{tid}" if tid else "", "scan:intel") if s]
    if not subjects:
        return []
    queries = item.get("search_queries") or [item.get("value") or item.get("label") or ""]
    feeds = [
        _feed(google_news_url(f"{q} when:1d"), subjects, "news", "rss:google_news")
        for q in queries
        if q.strip()
    ]
    # Topic↔instrument linkage: a linked ticker inherits its own fast Yahoo lane onto the SAME
    # topic subject, so topic evidence isn't Google-search-only (owner directive — a topic like
    # "KSTL M&A" linked to KSTL.DE should ride the 16-min-median Yahoo feed, not wait on the
    # 3.4h-median Google search RSS). The regulatory/GDELT lanes for a linked ticker are fed by
    # the hot tier's server-side name/ticker matching (services/feeds_regulatory.py,
    # services/feeds_hot.py) — no per-ticker URL needed here for those.
    for ticker in item.get("linked_tickers") or []:
        ticker = (ticker or "").strip()
        if not ticker:
            continue
        feeds.append(
            _feed(
                yahoo_ticker_url(ticker),
                subjects,
                "news",
                "rss:yahoo",
                source_kind="ticker",
                cadence="fast",
            )
        )
    return feeds


def resolve_custom_feeds(item: dict) -> list[dict]:
    """User-supplied ``custom_feed_urls`` for one watchlist item (ticker OR topic) — an
    open-source-generalization escape hatch (owner directive 2026-07-15): a market with no
    built-in regulatory registry entry (services/feeds_regulatory.py) can still get a fast,
    dedicated feed if the user points one at their own item. Validated (http(s), SSRF-guarded,
    ≤5 per item) at the WatchlistStore write boundary — this resolver trusts the stored list is
    already clean and just tags it for the hot tier (``via="rss:custom"``, ``cadence="hot"``)."""
    urls = [u.strip() for u in (item.get("custom_feed_urls") or []) if (u or "").strip()]
    if not urls:
        return []
    if is_topic(item):
        tid = (item.get("id") or "").strip()
        subjects = [s for s in (f"topic:{tid}" if tid else "", "scan:intel") if s]
    else:
        iid = (item.get("instrument_id") or "").strip()
        subjects = [s for s in (f"asset:{iid}" if iid else "", "scan:intel") if s]
    if not subjects:
        return []
    return [
        _feed(u, subjects, "news", "rss:custom", source_kind="custom", cadence="hot") for u in urls
    ]


def resolve_intel_feeds(holdings: list[dict], topics: list[dict]) -> list[dict]:
    """All RSS feeds for an intel scan: per-holding + per-topic."""
    feeds: list[dict] = []
    for row in holdings or []:
        feeds.extend(resolve_holding_feeds(row))
    for item in topics or []:
        feeds.extend(resolve_topic_feeds(item))
    return feeds


def resolve_opportunity_feeds(queries: list[str]) -> list[dict]:
    """Map discovery queries to Google News RSS feeds tagged scan:opportunity.

    The FREE default layer for opportunity scanning — each query becomes a Google News
    URL with the scan:opportunity subject so gather_rss populates the opportunity
    evidence cache without any paid web-search call.
    """
    return [
        _feed(google_news_url(q), ["scan:opportunity"], "opportunity", "rss:google_news")
        for q in (queries or [])
        if q.strip()
    ]


# ── parsing ──────────────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")

# A clean DNS/IP host label — letters, digits, dots, hyphens only. Used to reject a crafted
# <source url> authority (e.g. https://a"onerror=…) before its host can flow to a favicon src.
_SAFE_HOST_RE = re.compile(r"^[a-z0-9.-]+$", re.IGNORECASE)


def _strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _text(node, tag: str) -> str:
    el = node.find(f"{{*}}{tag}")
    return (el.text or "").strip() if el is not None and el.text else ""


def _link(node) -> str:
    # RSS: <link>url</link>; Atom: <link href="url"/> (prefer rel=alternate)
    direct = _text(node, "link")
    if direct:
        return direct
    best = ""
    for le in node.findall("{*}link"):
        href = (le.get("href") or "").strip()
        if not href:
            continue
        if (le.get("rel") or "alternate") == "alternate":
            return href
        best = best or href
    return best


def _image(node) -> str | None:
    for tag in ("thumbnail", "content"):
        el = node.find(f"{{*}}{tag}")
        if el is not None and (el.get("url") or "").strip():
            return el.get("url").strip()
    enc = node.find("{*}enclosure")
    if enc is not None and "image" in (enc.get("type") or "") and enc.get("url"):
        return enc.get("url").strip()
    return None


def _author(node) -> str:
    a = _text(node, "author") or _text(node, "creator")
    if a:
        return _strip_html(a)
    name = node.find("{*}author/{*}name")
    return (name.text or "").strip() if name is not None and name.text else ""


def _outlet_from_host(host: str) -> str:
    """Best-effort outlet name from a hostname: 'finance.yahoo.com' → 'Yahoo', 'a.co.uk' → 'A'."""
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) >= 2:
        parts = parts[:-1]  # drop the TLD (.com / .uk)
        if parts and parts[-1] in ("co", "com", "org", "net", "gov", "ac", "edu"):
            parts = parts[:-1]  # drop a second-level domain (.co.uk / .com.au)
    label = parts[-1] if parts else host
    return label[:1].upper() + label[1:] if label else ""


def _parse_item(node, default_via: str) -> dict:
    url = _link(node)
    # Only http(s) links may flow downstream — a feed link with a javascript:/data: scheme survives
    # canonical_url and would become a live href (XSS). Drop it here, at ingest, before the store.
    if url and not url.lower().startswith(("http://", "https://")):
        url = ""
    source_el = node.find("{*}source")
    source = (source_el.text or "").strip() if source_el is not None and source_el.text else ""
    # Google News emits <source url="https://www.benzinga.com">Benzinga</source>: the publisher's
    # homepage — the REAL domain behind the news.google.com redirect wrapper. Captured for display
    # (favicon + domain chip) so surfaces show the publisher, not the wrapper. http(s) only — a
    # javascript:/data: url here would otherwise flow to a favicon src downstream.
    source_url = (source_el.get("url") or "").strip() if source_el is not None else ""
    # Drop it unless it's http(s) AND its authority is a clean host label — defence-in-depth so a
    # crafted authority (quotes/spaces/angle-brackets) can't reach a downstream favicon src even if
    # a consumer forgets to escape. (urlparse().hostname alone does NOT guarantee a bare host.)
    if source_url and (
        not source_url.lower().startswith(("http://", "https://"))
        or not _SAFE_HOST_RE.match(hostname(source_url))
    ):
        source_url = ""
    if not source:
        source = _outlet_from_host(hostname(url))
    return {
        "url": url,
        "title": _strip_html(_text(node, "title")),
        "content": _strip_html(
            _text(node, "description") or _text(node, "summary") or _text(node, "content")
        )[:500],
        "published_date": _text(node, "pubDate")
        or _text(node, "published")
        or _text(node, "updated"),
        "source": source,
        "source_url": source_url,
        "guid": _text(node, "guid") or _text(node, "id"),
        "author": _author(node),
        "image": _image(node),
        "via": default_via,
    }


def parse_rss(xml_bytes: bytes, default_via: str = "rss") -> list[dict]:
    """Parse RSS or Atom bytes into provider-shaped hits. Pure; returns [] on any error.

    Feeds are third-party content, and stdlib ElementTree is NOT hardened against malicious XML, so
    we refuse any document carrying a DTD/entity declaration (``<!DOCTYPE`` / ``<!ENTITY``) — RSS and
    Atom never need one, and rejecting it blocks entity-expansion ("billion laughs") and XXE without a
    new dependency. The DTD always sits near the top, so scanning the head is sufficient."""
    if not xml_bytes:
        return []
    # A DTD/entity declaration is only legal in the PROLOGUE (before the root element), and RSS/Atom
    # never need one — so reject any document declaring one there. Entity-expansion ("billion laughs")
    # and XXE both REQUIRE a DTD, so this fully blocks that class (equivalent to defusedxml forbid_dtd
    # for our use, no new dep). Scanning only the bounded prologue avoids false-positives on feed
    # CONTENT that merely mentions "<!DOCTYPE" inside an item; a feed whose root isn't in the first
    # 64 KB is treated as malformed/hostile.
    head = bytes(xml_bytes[:65536]).lower()
    roots = [i for i in (head.find(b"<rss"), head.find(b"<feed"), head.find(b"<rdf")) if i >= 0]
    if not roots:
        return []
    prologue = head[: min(roots)]
    if b"<!doctype" in prologue or b"<!entity" in prologue:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return []
    nodes = root.findall(".//{*}item") or root.findall(".//{*}entry")
    out = []
    for node in nodes:
        hit = _parse_item(node, default_via)
        if hit.get("url") or hit.get("title"):
            out.append(hit)
    return out
