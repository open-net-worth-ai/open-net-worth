"""Bounded inline tool offer for an explicit-offer (agent/scheduled) turn.

The lazy-loading twin of the interactive CORE offer (``ai/tools/_groups.py``), but
scoped to the CALLER'S OWN tool list instead of the global registry. An agent's
``allowed_tools`` is CONFIGURATION (ADR ``2026-07-18-remove-layered-ai-permission-
system``) — what it may ever reach — never an enforced ceiling. This module changes
nothing about that: it only decides which of the agent's OWN tools get inlined as
full schemas THIS round versus discovered lazily (the same auto-load-then-validate
path interactive chat's ``find_tools``/discovery gate already use), so a large
``allowed_tools`` list stops paying for every schema on every round trip.

Measured motivation (2026-07-19, live ``agents.json``): the "Transaction & Trade
Logger" agent's 143-tool ``allowed_tools`` resolved to 135 live schemas — 156K+
chars sent inline on EVERY model round trip of EVERY scheduled fire, 4x the entire
interactive CORE offer. A small agent (e.g. "Monitor", 10 tools) is already cheap
and IS the agent's whole point — it must keep getting everything inline unchanged.

THRESHOLD: an agent whose tool count is at or under ``len(CORE_TOOL_NAMES)`` (the
interactive cold-start offer size, ~14 after adding the structural skill entrance — see
``docs/adr/2026-07-19-core-tool-offer-diet.md``, down from ~20) is small enough to
inline in full — matches the measured distribution (Monitor=10 stays inline; Fraud=64
and Trade-Logger=143 are the ones actually causing the bloat) and gives a principled
invariant: an agent's cold-start offer is never bigger than a chat turn's.

BOUNDED SET, when large: the agent's own tools that overlap the interactive CORE
set (very likely its hot path too — originally measured against the 13-tool CORE set:
9 of Fraud's 64 and 10 of Trade-Logger's 143, down from 14/16 of the old 20-tool set,
consistent with the same live ``agents.json``) plus ``find_tools`` (added even when
the agent never listed it — the mechanism this module exists to make possible) plus
enough additional tools (alphabetical, for a deterministic/cacheable prompt) to fill
the same budget. Everything else in the agent's list is reachable the instant the
model names it: the dispatch loop's discovery gate (``assistant._active_tools``'s
explicit-offer branch) auto-loads it and validates the already-emitted arguments,
scoped to (never wider than) this SAME ``offer`` — see ``ai/tools/_groups.py``'s
``_reachable_universe`` for the matching ``find_tools`` scoping.
"""

from __future__ import annotations

from typing import FrozenSet, Optional, Tuple


def _inline_limit() -> int:
    """The bounded-inline budget — pinned to the interactive CORE offer's own size so
    a bounded agent's cold-start payload is never bigger than a chat turn's."""
    from backend.ai.tools._groups import CORE_TOOL_NAMES

    return len(CORE_TOOL_NAMES)


def split_agent_offer(
    offer: FrozenSet[str],
) -> Tuple[Optional[FrozenSet[str]], FrozenSet[str]]:
    """Split an explicit-offer (agent) turn's tool list into ``(inline, catalog)``.

    ``inline`` is ``None`` when ``offer`` is small enough to deliver whole (at or under
    the budget) — the caller should use ``offer`` verbatim, byte-identical to before this
    module existed. ``catalog`` is empty in that case (nothing is hidden).

    Otherwise ``inline`` is the bounded subset to put in ``tools=`` THIS round: the CORE
    overlap (this agent's own likely-hot tools) + ``find_tools`` + enough of the
    remainder to fill the budget — its writes first (the discovery gate always bounces a
    deferred write), then alphabetical, deterministic, so the same ``offer`` always
    produces the same inline set and the prompt prefix stays cache-stable.
    ``catalog`` is everything else in ``offer`` — named (not described) in the agent
    system prompt so the model knows it can just call it (ai/prompts.py's
    ``_fmt_agent_tool_catalog``), exactly mirroring the interactive "TOOL CATALOG" block.

    Never adds a name that isn't in ``offer`` except ``find_tools`` itself — that is a
    delivery mechanism, not a widening: it lets the rest of THIS SAME ``offer`` stay
    reachable via search, it can never surface a tool outside it (see
    ``ai/tools/_groups.py``'s ``_reachable_universe`` scoping).
    """
    limit = _inline_limit()
    if len(offer) <= limit:
        return None, frozenset()

    from backend.ai.tools._groups import CORE_TOOL_NAMES
    from backend.ai.tools.registry import is_write_tool

    inline: set[str] = set(offer & CORE_TOOL_NAMES)
    inline.add("find_tools")
    # Writes before reads: the discovery gate runs a deferred READ on its guessed arguments,
    # but always bounces a deferred WRITE, and a model that gets bounced may stop instead of
    # retrying (a paper Autopilot review guessed propose_paper_orders, was bounced and ended
    # without its memo, 2026-11-21).
    rest = sorted(offer - inline, key=lambda n: (not is_write_tool(n), n))
    inline.update(rest[: max(0, limit - len(inline))])

    inline_frozen = frozenset(inline)
    catalog = offer - inline_frozen
    return inline_frozen, catalog
