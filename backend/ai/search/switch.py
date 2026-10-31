"""The ONE place the ``search_reads_results`` flag is read (docs/FEATURE_FLAGS.md).

Off: web search is exactly what it was — a result is the search engine's snippet, ``x_search`` is
offered, and nothing here touches the result. On: the top results' pages are read
(``page_reader.attach_page_excerpts``) and ``x_search`` is retired — left out of every tool offer,
and an Automation's ``always_allowed`` entry for it counts as one for ``web_search``. Nothing stored
is rewritten, so turning the flag off again restores the old behaviour with the same data.

Everything outside this module asks one of the three questions below and never reads the flag
itself. The search tools call ``search_results`` instead of ``provider.search``; the places that
build a tool offer subtract ``retired_tools()``; the Automation approval list folds its names through
``with_stand_ins``.
"""

from __future__ import annotations

from backend.ai.search.base import SearchProvider
from backend.ai.search.page_reader import attach_page_excerpts
from backend.services.feature_flags import enabled

FLAG = "search_reads_results"

# A tool the flag retires -> the tool whose permission an Automation's entry for it now counts as.
# ``x_search`` was ``web_search`` with ``(site:x.com OR site:twitter.com)`` appended.
_RETIRED: dict[str, str] = {"x_search": "web_search"}


def reads_results() -> bool:
    return enabled(FLAG)


async def search_results(provider: SearchProvider, query: str, max_results: int) -> list[dict]:
    """``provider.search`` — plus, with the flag on, a page ``excerpt`` on each of the top results
    (see ``page_reader`` for the bounds and the per-row failure marks)."""
    if not reads_results():
        return await provider.search(query, max_results)
    results = await provider.search(query, max_results)
    return await attach_page_excerpts(provider, results, query)


def retired_tools() -> frozenset[str]:
    """Tool names to leave out of every offer the model sees (empty while the flag is off)."""
    return frozenset(_RETIRED) if reads_results() else frozenset()


def with_stand_ins(names: set[str]) -> set[str]:
    """``names`` plus, for each retired tool named, the tool it stands in for (the set itself while
    the flag is off). The retired name stays: it still runs if called by name."""
    if not reads_results():
        return names
    return names | {_RETIRED[name] for name in names if name in _RETIRED}
