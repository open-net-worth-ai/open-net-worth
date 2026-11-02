"""The ONE place the ``search_reads_results`` flag is read (docs/FEATURE_FLAGS.md).

Off: web search is exactly what it was — a result is the search engine's snippet, ``x_search`` is
offered, and nothing here touches the result. On: the top results' pages are read
(``page_reader.attach_page_excerpts``) and ``x_search`` is retired — left out of every tool offer,
and an Automation's ``always_allowed`` entry for it counts as one for ``web_search`` unless the
owner stored Ask or Block for ``web_search``. Nothing stored is rewritten, so turning the flag off
again restores the old behaviour with the same data.

Everything outside this module asks one of the questions below and never reads the flag itself.
The search tools call ``search_results`` instead of ``provider.search``; the places that build a
tool offer subtract ``retired_tools()`` (through ``catalog.hidden_tools``, resolved ONCE per turn
by ``assistant.run`` into ``_skill_scope``); the Automation approval list folds its names through
``with_stand_ins``.

A flag that cannot be read is OFF: nothing is retired, nothing is read, no stand-in is granted.
"""

from __future__ import annotations

import logging

from backend.ai.search.base import SearchProvider
from backend.ai.search.page_reader import attach_page_excerpts
from backend.services.feature_flags import enabled

logger = logging.getLogger(__name__)

FLAG = "search_reads_results"

# A tool the flag retires -> the tool whose permission an Automation's entry for it now counts as.
# ``x_search`` was ``web_search`` with ``(site:x.com OR site:twitter.com)`` appended.
_RETIRED: dict[str, str] = {"x_search": "web_search"}

# Stored levels of the stand-in tool that an inherited grant must never override.
_RESTRICTED_LEVELS = frozenset({"needs_approval", "blocked"})


def reads_results() -> bool:
    try:
        return enabled(FLAG)
    except Exception:
        logger.warning("could not read the %s flag; treating it as off", FLAG, exc_info=True)
        return False


async def search_results(provider: SearchProvider, query: str, max_results: int) -> list[dict]:
    """``provider.search`` — plus, with the flag on, a page ``excerpt`` on each of the top results
    (see ``page_reader`` for the bounds and the per-row failure marks)."""
    if not reads_results():
        return await provider.search(query, max_results)
    results = await provider.search(query, max_results)
    return await attach_page_excerpts(provider, results, query)


def retired_tools() -> frozenset[str]:
    """Tool names to leave out of every offer the model sees (empty while the flag is off or
    cannot be read). Resolved once per turn by ``assistant.run``; see ``_skill_scope``."""
    return frozenset(_RETIRED) if reads_results() else frozenset()


def _stand_in_is_restricted(stand_in: str) -> bool:
    """True when the owner stored Ask or Block for ``stand_in``, or the store cannot say."""
    try:
        from backend.ai.tools._connector_gate import permission_owner
        from backend.services.tool_permissions import stored_tool_level

        return stored_tool_level(permission_owner(stand_in), stand_in) in _RESTRICTED_LEVELS
    except Exception:
        logger.warning("could not read the %s permission; granting no stand-in", stand_in)
        return True


def with_stand_ins(names: set[str]) -> set[str]:
    """``names`` plus, for each retired tool named, the tool it stands in for — unless the owner
    stored Ask or Block for that tool, which an inherited grant must not override. The set itself
    while the flag is off. The retired name stays: it still runs if called by name."""
    if not names.intersection(_RETIRED) or not reads_results():
        return names
    granted = {
        _RETIRED[name]
        for name in names
        if name in _RETIRED and not _stand_in_is_restricted(_RETIRED[name])
    }
    return names | granted
