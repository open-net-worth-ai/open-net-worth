from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

# Cap on the redirect chain ``plain_get_fetch`` will follow (each hop SSRF-re-validated).
_MAX_FETCH_REDIRECTS = 5
# Cap the response body so a hostile/huge page can't exhaust memory — mirrors _RSS_MAX_BYTES in
# backend/monitoring/collect.py / _CG_MAX_BYTES in backend/services/prices.py.
_MAX_FETCH_BYTES = 4_000_000

# Cap what a single fetch_page call can inject into the conversation (2026-08-27,
# owner-directed) — a DIFFERENT, tighter bound than _MAX_FETCH_BYTES above, which only
# limits what's read off the wire before local extraction runs and (via a
# survives-extraction-almost-whole page) could otherwise still reach ~1M tokens. 15,000
# is real headroom over observed usage on a live instance (fetch_page: avg ~4,900 chars,
# max ~10,900 total incl. the JSON envelope — tool_call_log, 2026-08-27).
MAX_FETCH_PAGE_CHARS = 15_000


# Providers whose fetch_page uses a paid API endpoint (~$0.002/page, Tavily). Firecrawl's own
# /scrape is 1 credit/page, ~$0.001 at the same plan tier — the two are NOT the same per-call
# cost, hence the per-provider dict rather than one constant for both. Plain-GET readers
# (duckduckgo, serper, google_pse, perplexity) cost ~$0.
PAID_FETCH_PROVIDERS: frozenset[str] = frozenset({"tavily", "firecrawl"})
_PAID_FETCH_COST_USD = 0.002
_PAID_FETCH_COST_USD_BY_PROVIDER: dict[str, float] = {"firecrawl": 0.001}


def record_paid_fetch(provider_id: str, source: str) -> None:
    """Log one page read to ``cost_log`` as the ``fetch:<id>`` pseudo-model when ``provider_id``
    reads through a paid extract API (a no-op for a plain-GET reader). Shared by asset_brief's
    full-text fetch and the web-search top-result reads so both account a read identically."""
    if provider_id not in PAID_FETCH_PROVIDERS:
        return
    from backend.ai.cost_tracker import record

    record(
        source=source,
        model=f"fetch:{provider_id}",
        input_tokens=0,
        output_tokens=0,
        cost_usd=_PAID_FETCH_COST_USD_BY_PROVIDER.get(provider_id, _PAID_FETCH_COST_USD),
    )


def fetch_result_payload(url: str, content: str) -> dict:
    """Build the standard ``{url, content, ...}`` fetch_page result dict.

    Sanitizes untrusted page content (control chars, fence-confusable characters) and
    head-caps it at MAX_FETCH_PAGE_CHARS. Unlike the retired history-rendering
    truncation this REPORTS a cut honestly — content_chars_shown/content_chars_total —
    instead of a silent stub with no signal of what's missing and no way to get the
    rest. Shared by fetch_page (misc.py) and every <provider>_fetch_page tool
    (web_search_providers.py) so both stay byte-identical in this behavior.
    """
    from backend.core.untrusted import sanitize_untrusted

    raw = content or ""
    total = len(raw)
    sanitized = sanitize_untrusted(raw, max_len=MAX_FETCH_PAGE_CHARS)
    payload: dict = {"url": url, "content": sanitized}
    if total > MAX_FETCH_PAGE_CHARS:
        payload["content_chars_shown"] = len(sanitized)
        payload["content_chars_total"] = total
    return payload


class SearchProvider(ABC):
    """Abstract interface for web search.

    A provider answers a query with a list of result rows and (where the API supports it)
    fetches the readable text of a single URL. Each concrete provider lives in its own
    ``<id>_provider.py`` and is registered in :data:`PROVIDER_REGISTRY` below, so a new
    provider is a new module + one registry entry — NO provider is "primary".
    """

    @abstractmethod
    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        """
        Returns a list of results, each with at minimum:
          { url: str, title: str, content: str, score?: float }
        """
        ...

    @abstractmethod
    async def fetch_page(self, url: str) -> str:
        """Fetch and return the readable text content of a URL."""
        ...

    # The registry id this instance was built for ("tavily", …); stamped by ``build_provider``.
    provider_id: str = ""

    # True for a provider whose ``fetch_page`` is the plain GET (``plain_get_fetch``): the
    # page's og:image then comes from the SAME download. A provider with its own extract API
    # (Tavily, Firecrawl) leaves this False and reports no image — never a second fetch.
    plain_page_fetch: bool = False

    async def fetch_page_with_meta(self, url: str) -> tuple[str, str | None]:
        """``fetch_page`` plus the page's og:image URL (None when unknown)."""
        if self.plain_page_fetch:
            return await plain_get_fetch_with_meta(url)
        return await self.fetch_page(url), None

    async def search_news(self, query: str, max_results: int = 8) -> list[dict]:
        """Search recent NEWS articles — real headlines + dates, not generic quote/overview pages.

        Default implementation falls back to ``search`` (general web). Providers with a
        dedicated news vertical (Serper ``/news``, Tavily ``topic=news``) override this to
        return article rows, often with ``source`` and ``image`` populated.
        """
        return await self.search(query, max_results)


# ── provider registry / catalog ────────────────────────────────────────────────────
#
# The single source of truth for the set of web-search providers the instance can use.
# Each entry pairs the presentation metadata (label/hint/docs/extra-config the UI renders)
# with a factory that builds the provider from a resolved config dict ``{api_key, ...extra}``.
#
# NO provider is special-cased as a default — when several are enabled the store picks the
# active one generically (first enabled, in catalog order). Adding a provider = a new
# ``<id>_provider.py`` + one entry here.


@dataclass(frozen=True)
class ProviderSpec:
    """Static metadata + factory for one web-search provider."""

    id: str
    label: str
    # One-line description shown under the key field in the UI.
    hint: str
    # Link to the provider's API-key dashboard / docs.
    docs_url: str
    # Build the provider from a resolved config dict ``{api_key, <extra fields>}``.
    factory: Callable[[dict], "SearchProvider"]
    # Whether this provider's API supports its own page-extract endpoint (richer than a
    # plain GET). Informational for the UI; every provider implements ``fetch_page`` (a
    # plain HTTP GET as the universal fallback).
    native_fetch: bool = False
    # Extra non-secret config fields beyond the API key. Each is {key, label, hint}
    # — e.g. Google PSE's ``cx`` engine id. Rendered as plain text inputs + stored alongside
    # the (write-only) key. NOT a secret.
    extra_fields: tuple = field(default_factory=tuple)
    # Whether a usable instance requires an API key (all current providers do).
    needs_api_key: bool = True


def _tavily_factory(cfg: dict) -> SearchProvider:
    from backend.ai.search.tavily_provider import TavilyProvider

    return TavilyProvider(api_key=cfg["api_key"])


def _perplexity_factory(cfg: dict) -> SearchProvider:
    from backend.ai.search.perplexity_provider import PerplexityProvider

    return PerplexityProvider(api_key=cfg["api_key"])


def _serper_factory(cfg: dict) -> SearchProvider:
    from backend.ai.search.serper_provider import SerperProvider

    return SerperProvider(api_key=cfg["api_key"])


def _google_pse_factory(cfg: dict) -> SearchProvider:
    from backend.ai.search.google_pse_provider import GooglePseProvider

    return GooglePseProvider(api_key=cfg["api_key"], cx=str(cfg.get("cx") or ""))


def _duckduckgo_factory(cfg: dict) -> SearchProvider:
    from backend.ai.search.duckduckgo_provider import DuckDuckGoProvider

    return DuckDuckGoProvider()


def _firecrawl_factory(cfg: dict) -> SearchProvider:
    from backend.ai.search.firecrawl_provider import FirecrawlProvider

    return FirecrawlProvider(api_key=cfg["api_key"])


# Order = display order in the UI. DuckDuckGo needs no key and is first, so it is the natural
# first-enabled fallback; the rest are real, currently-available search APIs (verified June 2026).
# Each maps to a concrete SearchProvider implementation.
#
# ``label`` is a NAME and nothing else — never a name plus its own metadata. It is rendered as the
# connector's title, in its modal heading, and inside every error message below, so a parenthetical
# like "(no key)" printed twice on one screen and duplicated the "No key needed" badge beside it.
# ``needs_api_key`` is the field that carries that fact.
PROVIDER_REGISTRY: tuple = (
    ProviderSpec(
        id="duckduckgo",
        label="DuckDuckGo",
        hint="Free web search with no account or API key.",
        docs_url="https://duckduckgo.com/",
        native_fetch=False,
        needs_api_key=False,
        factory=_duckduckgo_factory,
    ),
    ProviderSpec(
        id="tavily",
        label="Tavily",
        hint="Search built for AI agents — returns extracted page text rather than raw HTML.",
        docs_url="https://app.tavily.com/",
        native_fetch=True,
        factory=_tavily_factory,
    ),
    ProviderSpec(
        id="perplexity",
        label="Perplexity (Sonar)",
        hint="Answers the query directly and returns the sources it cited.",
        docs_url="https://docs.perplexity.ai/",
        native_fetch=False,
        factory=_perplexity_factory,
    ),
    ProviderSpec(
        id="serper",
        label="Serper",
        hint="Fast, low-cost Google results via serper.dev.",
        docs_url="https://serper.dev/api-keys",
        native_fetch=False,
        factory=_serper_factory,
    ),
    ProviderSpec(
        id="google_pse",
        label="Google Programmable Search",
        hint="Google Custom Search. Needs an API key and a search engine ID.",
        docs_url="https://developers.google.com/custom-search/v1/overview",
        native_fetch=False,
        extra_fields=(
            {
                "key": "cx",
                "label": "Search engine ID (cx)",
                "hint": "Create one at programmablesearchengine.google.com.",
            },
        ),
        factory=_google_pse_factory,
    ),
    ProviderSpec(
        id="firecrawl",
        label="Firecrawl",
        hint="Search + full page scraping, JS-rendered pages included — built for agents.",
        docs_url="https://www.firecrawl.dev/app/api-keys",
        native_fetch=True,
        factory=_firecrawl_factory,
    ),
)

PROVIDER_BY_ID: dict = {spec.id: spec for spec in PROVIDER_REGISTRY}
PROVIDER_IDS: tuple = tuple(spec.id for spec in PROVIDER_REGISTRY)


# Below this many characters, a trafilatura extraction is treated as a miss (e.g. a
# JS-only page with no server-rendered article body) rather than a real result — fall
# back to the raw decoded text instead of returning a near-empty "extraction".
_MIN_EXTRACTED_CHARS = 40


async def plain_get_fetch(url: str) -> str:
    """``plain_get_fetch_with_meta`` without the image — the text only."""
    text, _ = await plain_get_fetch_with_meta(url)
    return text


async def plain_get_fetch_with_meta(url: str) -> tuple[str, str | None]:
    """Universal ``fetch_page`` fallback: a plain HTTP GET, then a LOCAL readability-style
    extraction pass (trafilatura) to strip nav/ads/boilerplate down to the main content.

    Providers whose search API has no dedicated page-extract endpoint (Serper/Google PSE/
    Perplexity) reuse this so ``fetch_page`` keeps working everywhere. Best-effort — never
    raises; the richer per-provider readers (Tavily's /extract) override it. Never
    fabricates content.

    Extraction is LOCAL and free (no JS rendering, no external service — matches what
    OpenClaw/Hermes Agent both do as their first-pass tier before falling back to a paid
    render-capable extractor). A JS-only page with no server-rendered body will still come
    back mostly empty; that harder case needs a real browser/render service and is out of
    scope here — see the extraction RFC. If trafilatura finds too little content (or the
    body isn't extractable HTML at all), this falls back to the raw decoded text — the
    caller never gets a worse result than the pre-extraction behavior, only a better one.

    SSRF-GUARDED + DNS-REBIND-SAFE (ADR ``2026-07-16-dns-rebind-pinned-fetch``): the fetch runs
    through ``core.pinned_http.pinned_urlopen`` (blocking stdlib call, off the event loop via
    ``asyncio.to_thread``) — every hop is validated by ``core.net_guard`` (http(s) scheme + a
    public host — no localhost/private/link-local/metadata target) AND the connection is PINNED
    to the validated IP, so a low-TTL attacker domain that answers public at validation and
    private at connect (classic DNS rebinding) is refused, not just one that's private outright.
    Redirects are followed MANUALLY, capped at ``_MAX_FETCH_REDIRECTS`` — a public URL that
    30x-redirects to ``169.254.169.254`` / localhost / a private-LAN host is refused on the hop.
    The body is capped at ``_MAX_FETCH_BYTES`` on the wire (a hostile/huge page can't exhaust
    memory). Extraction runs on the ALREADY-FETCHED bytes — it never issues its own request,
    so it carries no separate SSRF surface."""
    import asyncio

    from backend.ai.search.html_extract import extract_main_content, extract_og_image
    from backend.core.net_guard import UnsafeUrlError
    from backend.core.pinned_http import PinnedFetchError, pinned_urlopen

    headers = {"User-Agent": "OpenNetWorth/1.0"}

    def _fetch() -> str:
        with pinned_urlopen(
            url, headers=headers, timeout=60, max_redirects=_MAX_FETCH_REDIRECTS
        ) as resp:
            body = resp.read(_MAX_FETCH_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
        return body.decode(charset, errors="replace")

    def _extract(raw: str) -> str:
        # Out of process, deliberately. trafilatura parses through lxml -> libxml2, and a
        # memory fault in that C code calls abort() rather than raising — it killed this
        # server twice on 2026-08-25 while researching an asset. An `except` cannot catch
        # that; only a process boundary contains it. See ai/search/html_extract.py.
        extracted = extract_main_content(raw, url)
        if extracted and len(extracted.strip()) >= _MIN_EXTRACTED_CHARS:
            return extracted.strip()
        return raw

    try:
        raw_text = await asyncio.to_thread(_fetch)
    except UnsafeUrlError as exc:
        return f"[fetch blocked: {exc}]", None
    except PinnedFetchError as exc:
        return f"[fetch failed: {exc}]", None
    except Exception as exc:
        return f"[fetch failed: {exc}]", None
    if not raw_text.strip():
        return "[no content extracted]", None
    image = extract_og_image(raw_text)
    text = await asyncio.to_thread(_extract, raw_text)
    return (text if text.strip() else "[no content extracted]"), image


def get_spec(provider_id: str) -> Optional[ProviderSpec]:
    return PROVIDER_BY_ID.get(provider_id)


def build_provider(provider_id: str, cfg: dict) -> SearchProvider:
    """Build a concrete ``SearchProvider`` for ``provider_id`` from a resolved config dict
    ``{api_key, <extra fields>}``. Raises ``ValueError`` for an unknown provider."""
    spec = PROVIDER_BY_ID.get(provider_id)
    if spec is None:
        raise ValueError(f"Unknown search provider: {provider_id!r}")
    provider = spec.factory(cfg)
    provider.provider_id = provider_id
    return provider
