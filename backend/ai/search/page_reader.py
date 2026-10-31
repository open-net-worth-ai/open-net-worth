"""Read the top web-search results' pages so the model answers from page text, not snippets.

Behind the ``search_reads_results`` flag (``ai/search/switch.py`` is the only caller).

A search row is a one-to-two-line snippet, often truncated or stale. Measured on the owner's real
history (2026-10-11): 93 of 114 chat turns that searched never read a page, and a description
nudge toward ``fetch_page`` changed nothing — so the read is structural. ``attach_page_excerpts``
reads the first ``READ_TOP`` result URLs IN PARALLEL and attaches a query-focused ``excerpt``
(``search/excerpt.py``) to each row it could read. Tavily and Firecrawl read through their own
extract APIs; every other provider through ``bounded_fetch.fetch_text_until`` (the SSRF-guarded,
DNS-pinned GET with a wall-clock deadline, on its own small thread pool).

A row it could not read keeps its snippet and says why (``excerpt_unavailable``); a row it never
tried (beyond ``read_top``) carries neither field. A failed read never fails the search. Every URL
passes the SSRF guard BEFORE any fetch.

The page text is untrusted third-party text: the excerpt is run through ``sanitize_untrusted`` here,
at the source, before it joins a row, so no consumer can forget to.

Bounds: ``read_top`` <= ``MAX_READ_TOP`` pages x ``EXCERPT_MAX_CHARS`` each; a page is excerpted
from at most its first ``MAX_PAGE_CHARS``; the whole batch returns by ``READ_DEADLINE_S`` however
slow the pages are; and no read keeps a thread past its own ``PAGE_TIMEOUT_S`` deadline.
"""

from __future__ import annotations

import asyncio
import logging
import time

from backend.ai.search.base import SearchProvider
from backend.ai.search.bounded_fetch import fetch_text_until, run_bounded
from backend.ai.search.excerpt import EXCERPT_MAX_CHARS, build_excerpt
from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)

# Pages read per search. Not a model-facing knob: the tool schemas are the ones main ships.
READ_TOP = 3
MAX_READ_TOP = 5
# Only the head of a page is scored — an extract API can return a whole book.
MAX_PAGE_CHARS = 200_000
# One page's whole read, SSRF check to excerpt. The deadline bounds the batch, DNS included.
PAGE_TIMEOUT_S = 4.0
READ_DEADLINE_S = 5.0
# cost_log ``source`` for the reads (the ``fetch:<id>`` pseudo-model keeps them out of the
# AI-model breakdowns).
COST_SOURCE = "web_search_read"

# What one extract call costs, for the providers that bill per page. Mirrors
# ``monitoring.asset_brief`` (Intel's full-text fetch), which sits above this package and so cannot
# be imported here; ``tests/test_search_page_reader.py`` pins the two tables equal. Plain-GET
# readers cost nothing.
_PAID_READ_COST_USD: dict[str, float] = {"tavily": 0.002, "firecrawl": 0.001}

# In-band failure strings the providers return from ``fetch_page`` instead of raising.
_SENTINELS = (
    ("[fetch blocked", "blocked"),
    ("[fetch failed", "error"),
    ("[no content extracted", "empty"),
)


def _record_paid_read(provider: SearchProvider) -> None:
    """Log one page read to ``cost_log`` when ``provider`` reads through a paid extract API.
    Called when the request is SENT: the provider bills it whether or not we wait for the reply."""
    cost = _PAID_READ_COST_USD.get(getattr(provider, "provider_id", ""))
    if cost is None:
        return
    try:
        from backend.ai.cost_tracker import record
        from backend.core.chat_session import get_session_id
        from backend.core.cost_context import get_cost_surface

        record(
            source=COST_SOURCE,
            model=f"fetch:{provider.provider_id}",
            input_tokens=0,
            output_tokens=0,
            cost_usd=cost,
            session_id=get_session_id() or "",
            surface=get_cost_surface(),
        )
    except Exception:
        logger.warning("search read: could not log the page-read cost", exc_info=True)


async def _fetch(provider: SearchProvider, url: str, deadline: float) -> str:
    """The page text (or an in-band ``[fetch …`` failure string); never the default executor."""
    if getattr(provider, "plain_page_fetch", False):
        return await run_bounded(fetch_text_until, url, deadline)
    _record_paid_read(provider)
    return await provider.fetch_page(url)


async def _read(provider: SearchProvider, url: str, query: str, deadline: float) -> dict:
    from backend.ai.tools._egress import UnsafeUrlError, assert_safe_outbound_url

    try:
        # On the reader pool: the guard ends in a blocking getaddrinfo.
        await run_bounded(assert_safe_outbound_url, url)
    except UnsafeUrlError:
        return {"excerpt_unavailable": "blocked"}
    text = await _fetch(provider, url, deadline)
    for prefix, reason in _SENTINELS:
        if text.startswith(prefix):
            return {"excerpt_unavailable": reason}
    excerpt = await run_bounded(build_excerpt, text[:MAX_PAGE_CHARS], query)
    if not excerpt:
        return {"excerpt_unavailable": "no_relevant_text"}
    # Capped AFTER normalisation: NFKC can expand a character (U+FDFA becomes 18).
    return {
        "excerpt": sanitize_untrusted(excerpt, max_len=EXCERPT_MAX_CHARS),
        "page_chars_total": len(text),
    }


async def _read_one(provider: SearchProvider, url: str, query: str, timeout_s: float) -> dict:
    """The fields to attach to one result row: ``excerpt`` + ``page_chars_total`` on success,
    else ``excerpt_unavailable`` with the reason. Never raises."""
    deadline = time.monotonic() + timeout_s
    try:
        return await asyncio.wait_for(_read(provider, url, query, deadline), timeout_s)
    except TimeoutError:
        return {"excerpt_unavailable": "timeout"}
    except Exception as exc:
        logger.info("search read failed for %s: %s", url, exc)
        return {"excerpt_unavailable": "error"}


async def attach_page_excerpts(
    provider: SearchProvider,
    results: list[dict],
    query: str,
    read_top: int = READ_TOP,
    *,
    deadline_s: float = READ_DEADLINE_S,
    page_timeout_s: float = PAGE_TIMEOUT_S,
) -> list[dict]:
    """``results`` with a page excerpt attached to the first ``read_top`` rows that have a
    distinct http(s) URL. Returns new dicts; the input is untouched. Never raises."""
    read_top = max(0, min(read_top, MAX_READ_TOP))
    targets: dict[int, str] = {}
    for index, row in enumerate(results):
        url = str(row.get("url") or "")
        if len(targets) >= read_top:
            break
        if url.startswith(("http://", "https://")) and url not in targets.values():
            targets[index] = url
    if not targets:
        return [dict(r) for r in results]

    started = time.monotonic()
    tasks = {
        index: asyncio.create_task(_read_one(provider, url, query, page_timeout_s))
        for index, url in targets.items()
    }
    try:
        _, pending = await asyncio.wait(tasks.values(), timeout=deadline_s)
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    attached: dict[int, dict] = {}
    for index, task in tasks.items():
        if task in pending:
            attached[index] = {"excerpt_unavailable": "timeout"}
        elif task.exception() is not None:
            attached[index] = {"excerpt_unavailable": "error"}
        else:
            attached[index] = task.result()
    logger.info(
        "search read: %d page(s), %d excerpted, %.1fs",
        len(targets),
        sum(1 for a in attached.values() if "excerpt" in a),
        time.monotonic() - started,
    )
    return [{**r, **attached.get(i, {})} for i, r in enumerate(results)]
