"""Per-provider web-search tools — ``<id>_search`` / ``<id>_fetch_page``.

**2026-08-20: renamed verb-first → connector-first** (``search_via_<id>``/
``fetch_page_via_<id>`` → ``<id>_search``/``<id>_fetch_page``) to match the same convention
every other built-in connector tool now uses. Single-operator, self-hosted instance (ADR
``2026-08-20-no-compat-aliases-single-user-instance``): a clean rename, no alias table entry.

``web_search``/``fetch_page`` (``ai/tools/misc.py``) are GENERIC: they run whichever
provider is currently ACTIVE (``SearchConfigStore``'s precedence/fallback chain) — the
"just search, let the system pick" default, kept as the everyday recommended path (owner
confirmed, RFC ``.claude/rfcs/2026-08-10-connectors-architecture.md`` §B3). The tools here
are the EXPLICIT counterpart: name exactly one provider, bypassing the active-selection
chain entirely.

**Why split at all (RFC §B3, reversing this codebase's own earlier "one consolidated
search tool" reasoning).** Tool permissions are NAME-KEYED, never
argument-pattern (the base permission RFC's deliberate design, unrevisited here). A single
shared ``web_search`` name means "always allow Tavily" is inexpressible — the decision would
silently also always-allow Perplexity, Serper, whichever the store's precedence chain
picks next, since they're all the exact same tool name. Per-provider tools
(``tavily_search``/``perplexity_search``/…) give each provider its own permissionable
name, exactly like the data-source split in ``connectors.py``. This is a permission-
granularity argument, not a provider-interchangeability one — providers genuinely ARE
substitutable for what a plain "search the web" call needs, but a user may still
legitimately want a different TRUST level per provider (always-allow the one they trust,
ask-every-time for one they just added).

One (search, fetch) pair per ``PROVIDER_REGISTRY`` entry (``ai/search/base.py``) —
INCLUDING DuckDuckGo, the zero-config default, so it is explicitly callable/grantable
like every other provider rather than only reachable through the generic tool.

Each handler resolves its OWN provider directly via ``ai.search.base.build_provider`` —
never ``get_search_provider()``'s active-selection chain — so calling
``tavily_search`` always hits Tavily specifically, never silently substitutes
DuckDuckGo if Tavily happens to be mis-configured (that silent substitution is exactly
what picking THIS tool over the generic one is meant to avoid). An unconfigured or
disabled provider fails with a clear, provider-named message instead of a generic "web
search not configured" — mirroring ``ai.search.get_search_provider``'s own error-message
convention, just scoped to one provider instead of the whole active chain.

**Discovery gating (``connector=``, RFC §B2).** Each tool is tagged
``connector="web_search:<id>"`` — the exact id ``services.connectors.list_connectors()``
synthesizes for that provider's own connector card. ``ai.tools.catalog``/
``ai.tools._groups`` read that tag to leave the tool OUT of the flat catalog and OUT of
``find_tools`` results/loads until the user has enabled that specific provider, mirroring
the data-source split's `connector=` tags exactly. ``web_search``/``fetch_page``
(``misc.py``) deliberately carry NO tag, for the same reason ``connectors.py``'s bulk
tools don't: they are the "let the system pick" convenience default and must keep working
even before any ONE provider is individually configured (DuckDuckGo alone makes them work
out of the box on a fresh install).
"""

from __future__ import annotations

import json
import logging

from backend.ai.search.base import (
    PROVIDER_REGISTRY,
    ProviderSpec,
    build_provider,
    fetch_result_payload,
)
from backend.ai.tools._fetch_gate import note_search_results
from backend.ai.tools.misc import FetchPageInput, WebSearchInput, sanitize_search_results
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import schema_of

logger = logging.getLogger(__name__)


def _unusable_reason(spec: ProviderSpec) -> str | None:
    """Why ``spec`` can't be used right now, or ``None`` if it's ready to call.

    Mirrors ``SearchConfigStore.is_enabled``/``has_key`` — reads the SAME store, just
    phrased as a single provider-named message instead of the generic active-chain error
    ``ai.search.get_search_provider`` raises.
    """
    from backend.db.search_config_store import SearchConfigStore

    store = SearchConfigStore()
    # "Web" is the family tab's real label (``model.ts`` FAMILY_SECTIONS) — the card itself is
    # named for the provider, so "Connectors → Web search → X" named a screen that isn't there.
    if not store.has_key(spec.id):
        need = "an API key" if spec.needs_api_key else "its configuration"
        return f"{spec.label} is not set up — add {need} in Connectors → Web → {spec.label}."
    if not store.is_enabled(spec.id):
        return f"{spec.label} is turned off. Connect it in Connectors → Web → {spec.label}."
    return None


async def _search_via(spec: ProviderSpec, input_data: dict) -> tuple[str, None]:
    reason = _unusable_reason(spec)
    if reason is not None:
        return json.dumps({"error": reason}, ensure_ascii=False), None

    from backend.db.search_config_store import SearchConfigStore

    query = str(input_data["query"]).strip()
    max_results = min(int(input_data.get("max_results", 8)), 15)
    cfg = SearchConfigStore().provider_runtime_config(spec.id)
    provider = build_provider(spec.id, cfg)
    logger.info("%s_search: %s", spec.id, query)
    try:
        results = await provider.search(query, max_results)
    except Exception as exc:
        logger.error("%s_search failed: %s", spec.id, exc)
        raise
    logger.info("%s_search returned %d results", spec.id, len(results))
    clean = sanitize_search_results(results)
    note_search_results(clean)  # their URLs may now be fetched without a card
    return json.dumps(clean, ensure_ascii=False), None


async def _fetch_via(spec: ProviderSpec, input_data: dict) -> tuple[str, None]:
    from backend.ai.tools._egress import UnsafeUrlError, assert_safe_outbound_url

    url = input_data["url"]
    logger.info("%s_fetch_page: %s", spec.id, url)
    # SSRF guard (defense in depth) — BEFORE the reason check and BEFORE handing the URL
    # to any provider, matching fetch_page's own ordering in misc.py.
    try:
        assert_safe_outbound_url(url)
    except UnsafeUrlError as exc:
        logger.warning("%s_fetch_page blocked unsafe url %r: %s", spec.id, url, exc)
        return json.dumps(
            {"url": url, "content": f"[fetch blocked: {exc}]"}, ensure_ascii=False
        ), None

    reason = _unusable_reason(spec)
    if reason is not None:
        return json.dumps({"error": reason}, ensure_ascii=False), None
    # The same provenance gate as fetch_page: naming a provider must not be a way around it.
    from backend.ai.tools import _fetch_gate

    gated = _fetch_gate.check(url, tool=f"{spec.id}_fetch_page")
    if gated is not None:
        return gated

    from backend.db.search_config_store import SearchConfigStore

    cfg = SearchConfigStore().provider_runtime_config(spec.id)
    provider = build_provider(spec.id, cfg)
    content = await provider.fetch_page(url)
    return json.dumps(fetch_result_payload(url, content), ensure_ascii=False), None


def _register(spec: ProviderSpec) -> None:
    """Register one (<id>_search, <id>_fetch_page) pair for ``spec``.

    A closure per provider, called once per ``PROVIDER_REGISTRY`` entry below — NOT a
    hand-authored function per provider, since the only thing that varies is the spec
    itself and the tool name string. ``spec`` is safely closed over: it is THIS CALL's own
    parameter, not a loop variable shared across iterations, so there is no late-binding
    hazard to guard against. Each pair still gets its OWN ``@tool`` registration (its own
    schema-derivation record, its own connector tag), so it is indistinguishable from a
    hand-written tool to the registry, ``find_tools``, or the golden-schema gate.
    """
    search_name = f"{spec.id}_search"
    fetch_name = f"{spec.id}_fetch_page"

    @tool(
        search_name,
        description=(
            f"Search the web via {spec.label} specifically — bypasses the active-provider "
            f"selection, so this always hits {spec.label} even if a different provider is "
            "currently active. Same result shape as web_search (url/title/content). Use "
            "when the user names this engine explicitly, or you deliberately want THIS "
            f"provider rather than whichever is active. {spec.hint}"
        ),
        input_schema=schema_of(WebSearchInput, tool_name=search_name),
        connector=f"web_search:{spec.id}",
    )
    async def _search(input_data: dict):
        return await _search_via(spec, input_data)

    @tool(
        fetch_name,
        description=(
            f"Fetch and read the full text of a URL via {spec.label} specifically — "
            "bypasses the active-provider selection. Same result shape as fetch_page. Use "
            f"after {spec.id}_search, or whenever you deliberately want {spec.label}'s own "
            "extraction."
        ),
        input_schema=schema_of(FetchPageInput, tool_name=fetch_name),
        connector=f"web_search:{spec.id}",
    )
    async def _fetch(input_data: dict):
        return await _fetch_via(spec, input_data)


for _spec in PROVIDER_REGISTRY:
    _register(_spec)
del _spec
