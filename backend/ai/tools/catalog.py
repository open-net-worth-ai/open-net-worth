"""Auto-generated tool discovery catalog (Lane A — tool-discovery RFC).

``build_tool_catalog`` renders every registered tool as a BARE NAME in ONE flat,
alphabetical, comma-separated list.  The output is embedded in the static
system-prompt block so the model always knows the full tool surface — zero
hand-maintenance.

Flat + names-only is deliberate (matching how Claude Code lists its own deferred
tools).  The catalog's job is EXISTENCE, not documentation or taxonomy: the model
does not need to read a description or a domain header to find a tool, it needs to
know the tool is there.  Descriptions live in the registry, where
``_groups._search_tools`` scores them server-side for ``find_tools(query=…)``, and
in the real schema that the dispatch loop auto-loads the instant the model calls an
un-loaded name.  Domain headers existed to make the (now removed) ``groups=[…]``
load path discoverable; with groups gone they were pure overhead.

External-MCP tools are listed the same way, in the same list (owner, 2026-11-11): bare
names, no per-server section, no summary, no cap — the way Claude Code lists every tool it
has not loaded, MCP tools included. A server-chosen name is still cleaned before it enters
the prompt (`_safe_external_name`): that is a defence, not a layout.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Deprecated/back-compat shims that must never be advertised to the model, and tools
# for a feature that is deliberately hidden while still live in code. Empty again since
# the tax feature was REMOVED outright (2026-08-25) rather than hidden — kept as the
# seam for the next one.
_CATALOG_HIDDEN: frozenset[str] = frozenset()


def hidden_tools() -> frozenset[str]:
    """Every tool kept out of what the model is offered: the permanent hides above plus any tool a
    feature flag has retired (``ai/search/switch.retired_tools``, empty while its flag is off).
    The flag is read once per turn (``assistant.run`` pins it in ``_skill_scope``); a caller
    outside a turn resolves it here."""
    from backend.ai.tools._skill_scope import turn_retired

    retired = turn_retired()
    if retired is None:
        from backend.ai.search.switch import retired_tools

        retired = retired_tools()
    return _CATALOG_HIDDEN | retired


# Leading instruction line of the rendered catalog. Tells the model that names are all
# it gets here and where the schemas come from, so it never treats a bare name as an
# incomplete/unusable entry. Deliberately prose (parenthesized, no trailing ':') so it
# can never be mistaken for a tool name by a reader of the block.
_CATALOG_PREAMBLE = (
    "(Tool names only — no descriptions, and no schemas. Load a name with "
    'find_tools(names=[…]) before calling it, or find_tools(query="…") to search '
    "descriptions when the right name is not obvious.)"
)
# An external tool's name is chosen by a server we do not control: length-capped so one
# cannot fill the cached prompt on its own.
_MAX_EXTERNAL_NAME_CHARS = 80


def _safe_external_name(name: str) -> str:
    """A server-chosen tool name, fit for the one comma-separated list: control characters and
    markup stripped, and no whitespace or comma — so it can neither start a line of its own
    nor pass for a second name."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", name)
    text = re.sub(r"[\s,]+", "", text)
    return text.lstrip("#=`*->|").strip()[:_MAX_EXTERNAL_NAME_CHARS]


def _internal_names() -> list[str]:
    """Every registered internal tool name, sorted alphabetically.

    Excludes ``_CATALOG_HIDDEN`` (the permanent hides — empty today) AND
    ``_connector_gate.hidden_connector_tools()`` — a ``connector=``-tagged tool whose
    connector isn't enabled (RFC ``.claude/rfcs/2026-08-10-connectors-architecture.md``
    §B2). An untagged (internal) tool is never affected by the second check.

    A REST API's tools are here too, as bare names like every internal tool: no section, no
    summary, no roster line, however many endpoints it has — the way Claude Code lists the
    tools it has not loaded (owner, 2026-11-11). ``find_tools`` and the discovery gate do the
    rest.
    """
    from backend.ai.tools._connector_gate import hidden_connector_tools
    from backend.ai.tools.registry import iter_registered_tools

    hidden = hidden_tools() | hidden_connector_tools()
    return sorted(
        name for name, _description, _module in iter_registered_tools() if name not in hidden
    )


def _external_names() -> list[str]:
    """Every external-MCP tool name, cleaned (best-effort, never raises): listed in the same
    flat list as every internal tool, however many a server advertises. ``find_tools`` and
    the discovery gate load a schema when it is needed."""
    try:
        from backend.core import mcp_external

        dispatcher = mcp_external.get_external_dispatcher()
        if dispatcher is None:
            return []
        names = {_safe_external_name(str(s.get("name") or "")) for s in dispatcher.schemas()}
        return sorted(n for n in names if n)
    except Exception:
        return []


def build_tool_catalog(
    include_external: bool = False, *, names: Optional[Iterable[str]] = None
) -> str:
    """Compact discovery catalog: bare tool NAMES, comma-separated, one flat list.

    Generated from the registry — zero hand-maintenance. Returns a string ready to embed
    in the system prompt. Never raises (best-effort; returns '' or partial on error).

    Flat and alphabetical: domain grouping was what made the removed ``find_tools(groups=…)``
    load path discoverable, and with only keyword search left it bought nothing. Descriptions
    are NOT rendered — see the module docstring.

    ``names``, when given, restricts the internal list to exactly those names (still
    alphabetical) — the scoped variant an explicit-offer (agent) system prompt uses
    (``ai/prompts._fmt_agent_tool_catalog``) so a bounded agent's catalog names only ITS
    OWN not-yet-inlined tools, never the whole registry. ``None`` (the default) is the
    interactive behavior: every registered tool.
    """
    try:
        internal = _internal_names()
        if names is not None:
            allowed = set(names)
            internal = [n for n in internal if n in allowed]
        # External-MCP tools are names in the same list, after the internal ones.
        external = _external_names() if include_external else []
        return "\n".join([_CATALOG_PREAMBLE, ", ".join(internal + external)])
    except Exception:
        return ""
