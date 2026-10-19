"""Read the top web-search results' pages so the model answers from page text, not snippets.

A search row is a one-to-two-line snippet, often truncated or stale. Measured on the owner's real
history (2026-10-11): 93 of 114 chat turns that searched never read a page, and a description
nudge toward ``fetch_page`` changed nothing — so the read is structural. ``attach_page_excerpts``
reads the first ``read_top`` result URLs IN PARALLEL through the provider's OWN ``fetch_page``
(Tavily/Firecrawl extract APIs; the others the SSRF-guarded pinned GET), and attaches a
query-focused ``excerpt`` (``search/excerpt.py``) to each row it could read.

A row it could not read keeps its snippet and says why (``excerpt_unavailable``); a row it never
tried (beyond ``read_top``, or ``read_top=0``) carries neither field. A failed read never fails
the search. Every URL passes the SSRF guard BEFORE any fetch.

Bounds: ``read_top`` <= ``MAX_READ_TOP`` pages x ``EXCERPT_MAX_CHARS`` each, and the whole batch
returns by ``READ_DEADLINE_S`` however slow the pages are.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.ai.search.base import SearchProvider, record_paid_fetch
from backend.ai.search.excerpt import build_excerpt

logger = logging.getLogger(__name__)

DEFAULT_READ_TOP = 3
MAX_READ_TOP = 5
# One page's fetch (after the SSRF check). The deadline bounds the whole batch, DNS included.
PAGE_TIMEOUT_S = 4.0
READ_DEADLINE_S = 5.0
# cost_log ``source`` for the reads (the ``fetch:<id>`` pseudo-model keeps them out of the
# AI-model breakdowns).
COST_SOURCE = "web_search_read"

# In-band failure strings the providers return from ``fetch_page`` instead of raising.
_SENTINELS = (
    ("[fetch blocked", "blocked"),
    ("[fetch failed", "error"),
    ("[no content extracted", "empty"),
)


def clamp_read_top(value: Any) -> int:
    """``read_top`` as an int in ``0..MAX_READ_TOP``; anything unparseable is the default."""
    try:
        return max(0, min(int(value), MAX_READ_TOP))
    except (TypeError, ValueError):
        return DEFAULT_READ_TOP


async def _read_one(provider: SearchProvider, url: str, query: str, timeout_s: float) -> dict:
    """The fields to attach to one result row: ``excerpt`` + ``page_chars_total`` on success,
    else ``excerpt_unavailable`` with the reason. Never raises."""
    from backend.ai.tools._egress import UnsafeUrlError, assert_safe_outbound_url

    try:
        # Off the loop: the guard ends in a blocking getaddrinfo.
        await asyncio.to_thread(assert_safe_outbound_url, url)
    except UnsafeUrlError:
        return {"excerpt_unavailable": "blocked"}
    try:
        text = await asyncio.wait_for(provider.fetch_page(url), timeout_s)
    except asyncio.TimeoutError:
        return {"excerpt_unavailable": "timeout"}
    except Exception as exc:
        logger.info("search read failed for %s: %s", url, exc)
        return {"excerpt_unavailable": "error"}
    # The extract API answered, so the read is billable whatever the page held — but not an
    # in-band "[fetch …" failure, where the call never completed.
    if not text.startswith("[fetch "):
        record_paid_fetch(getattr(provider, "provider_id", ""), COST_SOURCE)
    for prefix, reason in _SENTINELS:
        if text.startswith(prefix):
            return {"excerpt_unavailable": reason}
    excerpt = build_excerpt(text, query)
    if not excerpt:
        return {"excerpt_unavailable": "no_relevant_text"}
    return {"excerpt": excerpt, "page_chars_total": len(text)}


async def attach_page_excerpts(
    provider: SearchProvider,
    results: list[dict],
    query: str,
    read_top: int = DEFAULT_READ_TOP,
    *,
    deadline_s: float = READ_DEADLINE_S,
    page_timeout_s: float = PAGE_TIMEOUT_S,
) -> list[dict]:
    """``results`` with a page excerpt attached to the first ``read_top`` rows that have a
    distinct http(s) URL. Returns new dicts; the input is untouched. Never raises."""
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
