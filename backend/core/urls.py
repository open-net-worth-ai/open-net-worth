"""Pure URL helpers (leaf — stdlib urllib.parse only).

Shared utility so both monitoring/asset_brief.py and services/asset_detail.py can
derive a hostname without creating an illegal services→monitoring import edge.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Query params that identify a click/campaign, not the document — dropped when canonicalising
# so the same article shared with different tracking tags dedupes to one evidence row.
_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_eid", "mc_cid", "igshid", "yclid", "_hsenc")


def hostname(url: str) -> str:
    """Return the bare hostname of a URL (leading 'www.' stripped). Returns '' on failure."""
    if not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


# Two-label ccTLDs common enough in real news/wire domains that a bare "last two labels"
# split would wrongly treat e.g. "bbc.co.uk" as registrable domain "co.uk" (merging every
# unrelated *.co.uk site into one "source"). Not a full Public Suffix List — a best-effort
# heuristic for corroboration counting (ADR 2026-08-28-sourced-and-corroborated-intel-signal-
# updates' F2 fix), not a security boundary.
_COMPOUND_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "ltd.uk",
        "plc.uk",
        "co.jp",
        "co.kr",
        "co.in",
        "co.nz",
        "co.za",
        "co.il",
        "com.au",
        "com.br",
        "com.cn",
        "com.mx",
        "com.sg",
        "com.hk",
        "com.tw",
    }
)


def registrable_domain(url: str) -> str:
    """Best-effort eTLD+1 (registrable domain) for `url` — the base domain a hostile feed
    can't dodge by posting from different subdomains (`news.evilwire.example` and
    `articles.evilwire.example` both count as ONE source). Built on `hostname()`; a small
    hardcoded compound-suffix list handles the common two-label ccTLD case (`bbc.co.uk` ->
    `bbc.co.uk`, not `co.uk`), else the last two labels. Returns '' on failure/empty input."""
    host = hostname(url)
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in _COMPOUND_SUFFIXES:
        return ".".join(labels[-3:])
    return last_two


def canonical_url(url: str) -> str:
    """Normalise a URL for dedup: lowercase scheme+host (strip 'www.'), drop the #fragment and
    tracking query params (utm_*, fbclid, gclid, …), strip a trailing slash. Returns the trimmed
    input unchanged on failure — never raises. Used as the stable key for the evidence cache."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        host = (p.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        netloc = f"{host}:{p.port}" if p.port else host
        kept = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not any(k.lower().startswith(pref) for pref in _TRACKING_PREFIXES)
        ]
        path = p.path.rstrip("/")
        return urlunparse(((p.scheme or "https").lower(), netloc, path, "", urlencode(kept), ""))
    except Exception:
        return url.strip()


def fetch_key(url: str) -> str:
    """The identity two URLs share only when fetching one sends exactly what the other sends:
    host and port lowercased, scheme and #fragment dropped (neither reaches the server's log
    differently), path and query kept BYTE-FOR-BYTE. Deliberately NOT ``canonical_url`` —
    dropping tracking params would let ``?utm_x=<data>`` match a URL the user gave, which is
    the exact channel the fetch provenance gate exists to close. '' for anything not http(s)."""
    try:
        p = urlparse((url or "").strip())
        port = p.port
    except ValueError:
        return ""
    host = (p.hostname or "").lower().rstrip(".")
    if p.scheme.lower() not in ("http", "https") or not host:
        return ""
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    return urlunparse(("", netloc, p.path or "/", p.params, p.query, ""))


def normalize_host(value: str) -> str:
    """A bare lowercase hostname from what someone typed — ``Store.Example.com``,
    ``https://store.example.com/x`` and ``*.example.com`` all give the host. '' when it is not
    a plausible DNS name (no dot, a space, a port, a path segment left over)."""
    text = (value or "").strip().lower()
    if "://" in text:
        text = urlparse(text).hostname or ""
    text = text.split("/", 1)[0].removeprefix("*.").strip(".")
    labels = text.split(".")
    if len(labels) < 2 or any(
        not label or not all(c.isalnum() or c == "-" for c in label) for label in labels
    ):
        return ""
    return text


def host_allowed(url: str, allowed_hosts) -> bool:
    """Whether ``url``'s host is one of ``allowed_hosts`` or a subdomain of one (``example.com``
    covers ``store.example.com``, never ``badexample.com``)."""
    try:
        host = (urlparse((url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in allowed_hosts if h)
