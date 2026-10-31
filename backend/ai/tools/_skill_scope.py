"""Per-turn skill invocation state.

Inline skills carry instructions and resource-bundle identity only. They never shape
the ordinary provider tool offer or dispatch policy. The older restrictive active-scope
API remains for callers that explicitly request that legacy execution shape.

There is no inline-skill depth limit: multiple distinct standard skills may be selected
in one turn. This module imports nothing internal (a leaf of the ai/tools package), so it
cannot create an import cycle with the registry or assistant.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import NamedTuple, Optional

# A deprecated restrictive scope retained for explicit legacy callers. Instruction
# skills never set it.
_active_invocation_ids: ContextVar[tuple[str, ...]] = ContextVar(
    "skill_active_invocation_ids", default=()
)

# The active script-bundle context — set by ``activate_skill`` when the active skill is of
# ``kind=="script"``. Stores a small dict with keys:
#   ``skill_id``            — the skill's id (str)
#   ``manifest``            — the validated manifest dict (from ``read_bundle``)
#   ``bundle_dir``          — the version dir Path (pre-resolved by ``activate_skill``)
#   ``bundle_sha``          — the expected content-addressed sha (str)
#   ``allowed_script_ids``  — the set of script ids the turn may run
# Empty = no bundled skill active (the common case). Stacked direct skills retain each
# bundle by skill id so progressive disclosure never depends on activation order.
_active_bundles: ContextVar[dict[str, dict]] = ContextVar("skill_active_bundles", default={})

# The EXPLICIT TOOL OFFER for this turn — the exact set of tool names to put in ``tools=``,
# supplied by the CALLER. ``None`` (the default) means "no caller opinion": the turn gets the
# normal interactive offer (CORE ∪ whatever ``find_tools`` has loaded).
#
# This is an OFFER, not a permission ceiling. It replaced the old ``_headless_scope``, which
# was the same ContextVar carrying a fixed read+notify+propose allowlist that also acted as a
# hard dispatch-time deny. The ceiling is gone; what remains is the mundane and still-necessary
# job it was doing on the side — telling a non-interactive caller's turn which tools to offer,
# since such a turn has no session-sticky ``find_tools`` history to build an offer from.
#
# An agent run passes its own ``allowed_tools``; the P3-AG5 reflection pass passes an EMPTY set
# (a pure completion that must reach zero tools) — which is why ``None`` and ``frozenset()``
# mean different things here and must never be collapsed with ``or``.
_offer_scope: ContextVar[Optional[frozenset]] = ContextVar("offer_scope", default=None)

# The tool names a feature flag retires from every offer THIS turn, resolved once when the turn
# starts (``assistant.run``) so an offer build or a dispatch never re-reads the flag. ``None`` =
# not resolved: ``catalog.hidden_tools`` then resolves it on the spot. A failed read resolves to
# the empty set (nothing retired), never to "no restriction" on anything else.
_turn_retired: ContextVar[Optional[frozenset]] = ContextVar("turn_retired_tools", default=None)

# Is there a user in the loop this turn? ``False`` (the default) = interactive.
#
# Deliberately SEPARATE from the offer above. Unattendedness used to be inferred from "a
# headless scope is set", which conflated two unrelated facts: what tools to offer, and
# whether anyone is watching. Several features legitimately need the second one and have
# nothing to do with tool tiering — ``run_script``'s instance-level unattended switch and
# ``run_code``'s interactive-only rule. Giving them their own signal keeps each one honest
# instead of riding on a permission ceiling that no longer exists. The external-MCP offer
# was once listed here too; it is NOT gated on this and must not be — an external tool's
# permission level is the single authority on whether it fires, on every surface (ADR
# ``2026-08-13-mcp-permission-config-is-the-single-authority``; ``CLAUDE.md``,
# Security-critical areas: do not reintroduce a surface-dependent override).


def activate_invocation(skill_id: str) -> None:
    """Mark one inline invocation active, with the newest invocation as current."""
    normalized = str(skill_id or "")
    if not normalized:
        return
    prior = tuple(item for item in _active_invocation_ids.get() if item != normalized)
    _active_invocation_ids.set((*prior, normalized))


def active_invocation_ids() -> tuple[str, ...]:
    return _active_invocation_ids.get()


def current_invocation_id() -> Optional[str]:
    ids = _active_invocation_ids.get()
    return ids[-1] if ids else None


def set_active_bundle(bundle_info: dict) -> None:
    """Add or replace one active bundle context for this turn.

    Called by ``activate_skill`` when the resolved skill carries bundle resources. The
    ``bundle_info`` dict carries the pre-resolved bundle
    state (skill_id, manifest, bundle_dir, bundle_sha, allowed_script_ids) so the
    ``run_script`` tool can read it without re-fetching the skill record.
    """
    skill_id = str(bundle_info.get("skill_id") or "")
    if not skill_id:
        raise ValueError("active skill bundle requires a skill_id")
    _active_bundles.set({**_active_bundles.get(), skill_id: bundle_info})


def active_bundle(skill_id: Optional[str] = None) -> Optional[dict]:
    """Select one active bundle, preserving the one-bundle legacy convenience."""
    bundles = _active_bundles.get()
    if skill_id:
        bundle = bundles.get(skill_id)
        if bundle is None:
            raise ValueError(
                f"skill bundle '{skill_id}' is not active; active bundles: {sorted(bundles)}"
            )
        return bundle
    if not bundles:
        return None
    if len(bundles) == 1:
        return next(iter(bundles.values()))
    raise ValueError(
        f"multiple active skill bundles {sorted(bundles)}; provide skill_id to select one"
    )


def active_bundle_ids() -> tuple[str, ...]:
    """Active bundle ids in activation order."""
    return tuple(_active_bundles.get())


def clear_scope() -> None:
    """Reset the scope + depth — called by ``assistant.run`` at turn end. Also clears the
    offer + unattended + bundle scopes so no per-turn state leaks across turns.

    Unconditional (wipes to None/0 regardless of what was active before this call) — correct
    for a TOP-LEVEL owner that is done for good (``api.chat``'s early-generator-close
    belt-and-braces cleanup, the scheduled-agent-runner's own turn, ``mcp.adapter``). NOT used
    by ``assistant.run`` itself any more for that reason — see ``snapshot_scope``/
    ``restore_scope`` below, which a NESTED turn needs instead (CONC-H5)."""
    _active_invocation_ids.set(())
    _offer_scope.set(None)
    _active_bundles.set({})
    _turn_retired.set(None)


def set_turn_retired(names) -> None:
    """Pin the flag-retired tool names for this turn (see ``_turn_retired``)."""
    _turn_retired.set(frozenset(names))


def turn_retired() -> Optional[frozenset]:
    """The names pinned for this turn, or None when none were."""
    return _turn_retired.get()


def clear_inline_activation() -> None:
    """Start a new real-user boundary without disturbing caller-owned turn state."""
    _active_invocation_ids.set(())
    _active_bundles.set({})


class ScopeSnapshot(NamedTuple):
    """A point-in-time capture of the per-turn scope ContextVars this module owns.

    These are OFFER + context state, not permissions. The snapshot/restore pair exists for
    NESTING (CONC-H5): a tool call that dispatches a sub-turn in the same async context must
    put the outer turn's state back rather than null it.
    """

    active_bundles: dict[str, dict]
    offer_scope: Optional[frozenset]
    active_invocation_ids: tuple[str, ...]
    turn_retired: Optional[frozenset] = None


def snapshot_scope() -> ScopeSnapshot:
    """Capture the CURRENT value of every per-turn scope ContextVar.

    ``assistant.run`` calls this on entry and ``restore_scope`` on exit instead of the
    blanket ``clear_scope()`` (CONC-H5): under nesting — an interactive turn's tool call
    dispatches to ``services.agents.run_agent``, which awaits a fresh, NESTED
    ``assistant.run`` turn in the SAME async context (no ``contextvars.Context`` fork) —
    the inner turn's own scope-clearing must not null the OUTER, still-running turn's state
    (its legacy scope, its explicit offer, and its bundle state) out from under it. A top-level
    (non-nested) turn's snapshot is simply the all-None/0 default, so restoring it at turn end
    is byte-identical to the old unconditional ``clear_scope()``.

    """
    return ScopeSnapshot(
        active_bundles=dict(_active_bundles.get()),
        offer_scope=_offer_scope.get(),
        active_invocation_ids=_active_invocation_ids.get(),
        turn_retired=_turn_retired.get(),
    )


def restore_scope(snapshot: ScopeSnapshot) -> None:
    """Restore the per-turn scope ContextVars to a prior ``snapshot_scope()``
    capture (``assistant.run``'s ``finally`` — see ``snapshot_scope``).

    """
    _active_bundles.set(dict(snapshot.active_bundles))
    _offer_scope.set(snapshot.offer_scope)
    _active_invocation_ids.set(snapshot.active_invocation_ids)
    _turn_retired.set(snapshot.turn_retired)


def set_offer_scope(tool_names) -> None:
    """Set the EXPLICIT tool offer for this turn (see ``_offer_scope``).

    An empty iterable is meaningful — it offers nothing — and is NOT the same as never
    calling this, which leaves the normal interactive offer in place."""
    _offer_scope.set(frozenset(tool_names))


def offer_tool_names() -> Optional[frozenset]:
    """The caller-supplied tool offer for this turn, minus catalog-hidden tools, or
    None for the default offer.

    An explicit offer (an agent's ``allowed_tools``) is CONFIGURATION, not an enforced
    ceiling (ADR ``2026-07-18-remove-layered-ai-permission-system``) — this function does
    not reintroduce one. But ``ai/tools/catalog.py``'s ``_CATALOG_HIDDEN`` is a narrower,
    deliberate exception: a tool hidden there must stay unreachable through EVERY normal
    path, not just the interactive catalog/``find_tools`` (Task 87 — the tax tools must
    never be discoverable OR directly offered while hidden, so a Lucius-authored agent
    can't route around the hide). Filtered here, not at ``set_offer_scope`` write time, so
    ``snapshot_scope``/``restore_scope`` round-trip the caller's raw list unchanged; every
    real consumer reads through this getter. Deferred import mirrors
    ``ai/tools/_groups.py``'s ``_candidate_schemas`` (avoids a load-order cycle)."""
    from backend.ai.tools.catalog import hidden_tools

    offer = _offer_scope.get()
    hidden = hidden_tools() if offer is not None else None
    if not hidden:
        return offer
    return offer - hidden
